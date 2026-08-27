from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from fluxion.errors.workflow import (
    WORKFLOW_BACKEND_UNAVAILABLE,
    WorkflowBackendUnavailableError,
    WorkflowEngineError,
)
from fluxion.observability.logging import emit_workflow_event_log
from fluxion.runtime.context import RuntimeContext
from fluxion.runtime.tools import ToolDescriptor, ToolResult


@dataclass(frozen=True, slots=True)
class WorkflowStartRequest:
    workflow_id: str
    tenant_id: str
    user_id: str
    execution_id: str
    trace_id: str
    arguments: dict[str, object]


@dataclass(frozen=True, slots=True)
class WorkflowStartResult:
    run_id: str
    status: str = "started"


@dataclass(frozen=True, slots=True)
class WorkflowRunStatus:
    """Workflow run 投影状态（resume/get_status 返回，design §3.4）。"""

    run_id: str
    status: str


class WorkflowEngine(Protocol):
    """最小 Durable Execution Contract（ADR-WF-001 §2.3.2；backend 无关）。

    所有成员的失败策略由 `ResilientWorkflowEngine` 统一承载（规则 18）；
    业务错误统一抛 `WorkflowEngineError` 族。
    """

    async def start(self, request: WorkflowStartRequest) -> WorkflowStartResult: ...

    async def resume(self, run_id: str) -> WorkflowRunStatus: ...

    async def signal(self, run_id: str, name: str, payload: object) -> None: ...

    async def cancel(self, run_id: str, *, timeout: float) -> None: ...

    async def get_status(self, run_id: str) -> WorkflowRunStatus: ...


@dataclass(frozen=True, slots=True)
class FailPolicy:
    """backend 调用失败策略（规则 18 / RULE-WF-04）：有限 timeout + 有限 retry + circuit breaker。"""

    timeout_seconds: float = 5.0
    max_retries: int = 1
    retry_delay_seconds: float = 0.1
    breaker_threshold: int = 3
    breaker_cooldown_seconds: float = 30.0


class ResilientWorkflowEngine:
    """按 `FailPolicy` 包装任意 WorkflowEngine 实现。

    - 单尝试 `asyncio.wait_for` timeout，无无限等待；
    - 有限 retry（总尝试 = 1 + max_retries）；
    - 连续 infrastructure 失败（连接/超时类）达阈值后熔断打开，快速失败返回
      `WorkflowBackendUnavailableError`；cooldown 到期后半开放行试探；
    - 业务契约错误（`WorkflowEngineError` 族）透传，不重试、不计熔断。

    各 PoC 候选（DBOS/Temporal/Restate）与生产实现复用同一失败策略。
    """

    def __init__(self, *, delegate: WorkflowEngine, policy: FailPolicy | None = None) -> None:
        self._delegate = delegate
        self._policy = policy or FailPolicy()
        self._consecutive_failures = 0
        self._breaker_opened_at: float | None = None

    @property
    def breaker_open(self) -> bool:
        if self._breaker_opened_at is None:
            return False
        if time.monotonic() - self._breaker_opened_at >= self._policy.breaker_cooldown_seconds:
            self._breaker_opened_at = None
            self._consecutive_failures = 0
            return False
        return True

    async def start(self, request: WorkflowStartRequest) -> WorkflowStartResult:
        return await self._invoke(
            "start",
            lambda: self._delegate.start(request),
            tenant_id=request.tenant_id,
            trace_id=request.trace_id,
        )

    async def resume(self, run_id: str) -> WorkflowRunStatus:
        return await self._invoke("resume", lambda: self._delegate.resume(run_id), run_id=run_id)

    async def signal(self, run_id: str, name: str, payload: object) -> None:
        await self._invoke(
            "signal", lambda: self._delegate.signal(run_id, name, payload), run_id=run_id
        )

    async def cancel(self, run_id: str, *, timeout: float) -> None:
        await self._invoke(
            "cancel", lambda: self._delegate.cancel(run_id, timeout=timeout), run_id=run_id
        )

    async def get_status(self, run_id: str) -> WorkflowRunStatus:
        return await self._invoke("get_status", lambda: self._delegate.get_status(run_id), run_id=run_id)

    async def _invoke(
        self,
        operation: str,
        call: Callable[[], Any],
        *,
        run_id: str | None = None,
        tenant_id: str | None = None,
        trace_id: str | None = None,
    ) -> Any:
        self._ensure_breaker_closed(operation, run_id=run_id, tenant_id=tenant_id, trace_id=trace_id)
        attempts = 1 + self._policy.max_retries
        last_error: BaseException | None = None
        for attempt in range(1, attempts + 1):
            try:
                return await asyncio.wait_for(call(), timeout=self._policy.timeout_seconds)
            except WorkflowEngineError:
                raise
            except (ConnectionError, TimeoutError, OSError) as error:
                last_error = error
                self._record_failure(
                    operation,
                    run_id=run_id,
                    tenant_id=tenant_id,
                    trace_id=trace_id,
                )
                if attempt < attempts:
                    await asyncio.sleep(self._policy.retry_delay_seconds)
        raise WorkflowBackendUnavailableError(
            f"workflow backend unavailable after {attempts} attempts: {last_error}"
        )

    def _ensure_breaker_closed(
        self,
        operation: str,
        *,
        run_id: str | None,
        tenant_id: str | None,
        trace_id: str | None,
    ) -> None:
        if not self.breaker_open:
            return
        raise WorkflowBackendUnavailableError(
            f"circuit breaker open, fast-failing operation {operation}"
        )

    def _record_failure(
        self,
        operation: str,
        *,
        run_id: str | None,
        tenant_id: str | None,
        trace_id: str | None,
    ) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures < self._policy.breaker_threshold:
            return
        if self._breaker_opened_at is None:
            self._breaker_opened_at = time.monotonic()
            emit_workflow_event_log(
                event="workflow.breaker.opened",
                level="error",
                run_id=run_id,
                tenant_id=tenant_id,
                trace_id=trace_id,
                error_code=WORKFLOW_BACKEND_UNAVAILABLE,
                detail=f"operation={operation} failures={self._consecutive_failures}",
            )


class WorkflowAdapter:
    def __init__(self, *, workflow_id: str, engine: WorkflowEngine) -> None:
        if not workflow_id.strip():
            raise ValueError("workflow_id is required")
        self._workflow_id = workflow_id
        self._engine = engine

    @property
    def descriptor(self) -> ToolDescriptor:
        return ToolDescriptor(
            tool_id=f"workflow.{self._workflow_id}.start",
            capability_id=f"workflow.{self._workflow_id}",
            name=f"workflow.{self._workflow_id}.start",
            external_dependency=True,
        )

    @property
    def local_durable_state_count(self) -> int:
        return 0

    async def execute(
        self,
        context: RuntimeContext,
        arguments: dict[str, object],
    ) -> ToolResult:
        result = await self._engine.start(
            WorkflowStartRequest(
                workflow_id=self._workflow_id,
                tenant_id=context.snapshot.tenant_id,
                user_id=context.snapshot.user_id,
                execution_id=context.snapshot.execution_id,
                trace_id=context.snapshot.trace_id,
                arguments=arguments,
            )
        )
        emit_workflow_event_log(
            event="workflow.started",
            run_id=result.run_id,
            tenant_id=context.snapshot.tenant_id,
            trace_id=context.snapshot.trace_id,
            execution_id=context.snapshot.execution_id,
        )
        return ToolResult.started(result.run_id, result.status)


class StubWorkflowEngine:
    def __init__(self, *, run_id: str) -> None:
        self._run_id = run_id
        self.started_requests: list[WorkflowStartRequest] = []
        self.resumed: list[str] = []
        self.signals: list[tuple[str, str, object]] = []
        self.cancelled: list[str] = []
        self._status = "running"

    async def start(self, request: WorkflowStartRequest) -> WorkflowStartResult:
        self.started_requests.append(request)
        return WorkflowStartResult(run_id=self._run_id)

    async def resume(self, run_id: str) -> WorkflowRunStatus:
        self.resumed.append(run_id)
        return WorkflowRunStatus(run_id=run_id, status=self._status)

    async def signal(self, run_id: str, name: str, payload: object) -> None:
        self.signals.append((run_id, name, payload))

    async def cancel(self, run_id: str, *, timeout: float) -> None:
        self.cancelled.append(run_id)
        self._status = "cancelled"

    async def get_status(self, run_id: str) -> WorkflowRunStatus:
        return WorkflowRunStatus(run_id=run_id, status=self._status)
