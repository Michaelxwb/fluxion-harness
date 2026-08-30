from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

from fluxion.contracts.workflow import (
    FailPolicy,
    WorkflowEngine,
    WorkflowExecutionHistory,
    WorkflowPinnedRef,
    WorkflowRunStatus,
    WorkflowStartRequest,
    WorkflowStartResult,
    WorkflowStepRecord,
)
from fluxion.errors.workflow import (
    WORKFLOW_BACKEND_UNAVAILABLE,
    WorkflowBackendUnavailableError,
    WorkflowEngineError,
)
from fluxion.observability.logging import emit_workflow_event_log
from fluxion.runtime.context import RuntimeContext
from fluxion.runtime.tools import ToolDescriptor, ToolResult

# re-export：契约下沉到 `fluxion.contracts.workflow`（架构守护要求 api/services
# 不 import `fluxion.runtime.*`）；旧导入路径（runtime/workflow_dbos、cli、tests）
# 保持兼容——模块顶部 import 即构成 re-export，无需 __all__（避免模块级可变容器）。


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

    async def await_result(self, run_id: str, *, timeout: float) -> object:
        # P1-6：await_result 的 timeout 是调用方合法等待上界，不能被 FailPolicy 默认
        # 5s 砍掉（合法等待会被误判 infra 失败并污染熔断）。透传 operation timeout。
        return await self._invoke(
            "await_result",
            lambda: self._delegate.await_result(run_id, timeout=timeout),
            run_id=run_id,
            operation_timeout_seconds=timeout,
        )

    async def get_execution_history(self, run_id: str) -> WorkflowExecutionHistory:
        return await self._invoke(
            "get_execution_history",
            lambda: self._delegate.get_execution_history(run_id),
            run_id=run_id,
        )

    async def _invoke(
        self,
        operation: str,
        call: Callable[[], Any],
        *,
        run_id: str | None = None,
        tenant_id: str | None = None,
        trace_id: str | None = None,
        operation_timeout_seconds: float | None = None,
    ) -> Any:
        self._ensure_breaker_closed(operation, run_id=run_id, tenant_id=tenant_id, trace_id=trace_id)
        attempts = 1 + self._policy.max_retries
        # P1-6：成员可覆盖单次调用上界（await_result 透传调用方 timeout）；默认 FailPolicy。
        timeout_seconds = operation_timeout_seconds or self._policy.timeout_seconds
        last_error: BaseException | None = None
        for attempt in range(1, attempts + 1):
            try:
                result = await asyncio.wait_for(call(), timeout=timeout_seconds)
                # P1-7：熔断计数是"连续"失败——成功即清零，偶发 failure→success→failure
                # 不打开 breaker（健康 backend 不被误伤 30s）。
                self._consecutive_failures = 0
                return result
            except WorkflowBackendUnavailableError as error:
                # backend 不可达是基础设施失败（E-01）：计入熔断并走有界重试。
                last_error = error
                self._record_failure(
                    operation,
                    run_id=run_id,
                    tenant_id=tenant_id,
                    trace_id=trace_id,
                )
                if attempt < attempts:
                    await asyncio.sleep(self._policy.retry_delay_seconds)
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
        if isinstance(last_error, WorkflowBackendUnavailableError):
            raise last_error
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
    def __init__(
        self,
        *,
        workflow_id: str,
        engine: WorkflowEngine,
        version: str | None = None,
    ) -> None:
        if not workflow_id.strip():
            raise ValueError("workflow_id is required")
        self._workflow_id = workflow_id
        self._engine = engine
        # 可选版本 pin（RULE-P3-02）：Tool 装配自 ExecutionSnapshot 固定 workflow
        # 版本时传入；缺省不 pin（StubEngine 契约/纯工具语义场景），DBOS 引擎
        # start 会因缺 pinned workflow ref 拒绝——不允许未固定版本的漂移 start。
        self._version = version

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
                pinned=(
                    (WorkflowPinnedRef(kind="workflow", id=self._workflow_id, version=self._version),)
                    if self._version is not None
                    else ()
                ),
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
