"""Workflow 领域契约（design §2.3.2 FEAT-P3-01 / §3.4）。

从 `runtime/workflow.py` 下沉到契约层：api/services 只依赖这里的 Contract，
不依赖 `fluxion.runtime.*`（架构守护 `test_workflow_architecture.py`）。
`runtime/workflow_dbos.py`（adapter）与 Contract 同 runtime 包、贴近实现；
`runtime/workflow.py` 对旧导入路径做 re-export 兼容。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class WorkflowPinnedRef:
    """ExecutionSnapshot 固定的资源版本快照（design §2.3.2 FEAT-P3-01 / RULE-P3-02）。

    一次 Execution 从 start 到结束固定这些版本；resume 不 resolve latest。
    """

    kind: str
    id: str
    version: str


@dataclass(frozen=True, slots=True)
class WorkflowStartRequest:
    workflow_id: str
    tenant_id: str
    user_id: str
    execution_id: str
    trace_id: str
    arguments: dict[str, object]
    # ExecutionSnapshot 固定的 workflow + 依赖资源版本快照（RULE-P3-02）。
    pinned: tuple[WorkflowPinnedRef, ...] = ()


@dataclass(frozen=True, slots=True)
class WorkflowStartResult:
    run_id: str
    status: str = "started"


@dataclass(frozen=True, slots=True)
class WorkflowRunStatus:
    """Workflow run 投影状态（resume/get_status 返回，design §3.4）。"""

    run_id: str
    status: str


@dataclass(frozen=True, slots=True)
class WorkflowStepRecord:
    """单 step/节点执行历史（get_execution_history 返回，design §3.4）。"""

    node_id: str
    status: str
    output: object | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class WorkflowExecutionHistory:
    """execution → run 关联 + 节点历史（roadmap 接口 8；Workflow Studio 数据源）。"""

    run_id: str
    status: str
    steps: tuple[WorkflowStepRecord, ...] = ()


@runtime_checkable
class WorkflowEngine(Protocol):
    """最小 Durable Execution Contract（ADR-WF-001 §2.3.2；backend 无关）。

    所有成员的失败策略由 `ResilientWorkflowEngine` 统一承载（规则 18）；
    业务错误统一抛 `WorkflowEngineError` 族。

    Phase 3（FEAT-P3-01）扩展为 7 成员：新增 `await_result`（有限等待，超时
    `TimeoutError`）与 `get_execution_history`（execution→run 关联）。
    """

    async def start(self, request: WorkflowStartRequest) -> WorkflowStartResult: ...

    async def resume(self, run_id: str) -> WorkflowRunStatus: ...

    async def signal(self, run_id: str, name: str, payload: object) -> None: ...

    async def cancel(self, run_id: str, *, timeout: float) -> None: ...

    async def get_status(self, run_id: str) -> WorkflowRunStatus: ...

    async def await_result(self, run_id: str, *, timeout: float) -> object: ...

    async def get_execution_history(self, run_id: str) -> WorkflowExecutionHistory: ...


@dataclass(frozen=True, slots=True)
class FailPolicy:
    """backend 调用失败策略（规则 18 / RULE-WF-04）：有限 timeout + 有限 retry + circuit breaker。"""

    timeout_seconds: float = 5.0
    max_retries: int = 1
    retry_delay_seconds: float = 0.1
    breaker_threshold: int = 3
    breaker_cooldown_seconds: float = 30.0
