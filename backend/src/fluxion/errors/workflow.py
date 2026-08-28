from __future__ import annotations

# Workflow Engine 错误码（集中定义，禁止散落硬编码——ADR-WF-001 TASK-001）。
# 40xxx 与 RUNTIME_APPLICATION_ERROR 同段（runtime 域），40_1xx 为 workflow 子段；
# 子段划分作为后续契约命名空间对齐项。
WORKFLOW_ENGINE_FAILURE = 40_100
WORKFLOW_RUN_NOT_FOUND = 40_101
WORKFLOW_INVALID_STATE = 40_102
WORKFLOW_CANCEL_TIMEOUT = 40_103
WORKFLOW_BACKEND_UNAVAILABLE = 40_104


class WorkflowEngineError(Exception):
    """WorkflowEngine Contract 异常基类；所有 backend 实现统一抛出此错误族。

    `code` 允许缺省（默认 `WORKFLOW_ENGINE_FAILURE`）：DBOS 在跨进程/重放
    边界以 `exc_type(*args)` 重建 step 异常（S-10/S-04 实测），keyword-only
    无默认值的 `code` 会令重建抛 TypeError，吞掉原始失败。
    """

    code: int

    def __init__(self, message: str, *, code: int = WORKFLOW_ENGINE_FAILURE) -> None:
        super().__init__(message)
        self.code = code


class WorkflowRunNotFoundError(WorkflowEngineError):
    """run 不存在（resume/signal/get_status）——design §3.4 NotFound。"""

    def __init__(self, message: str = "workflow run not found") -> None:
        super().__init__(message, code=WORKFLOW_RUN_NOT_FOUND)


class WorkflowInvalidStateError(WorkflowEngineError):
    """run 已终态等非法状态（signal）——design §3.4 InvalidState。"""

    def __init__(self, message: str = "workflow run in invalid state") -> None:
        super().__init__(message, code=WORKFLOW_INVALID_STATE)


class WorkflowCancelTimeoutError(WorkflowEngineError):
    """cancel 超时（规则 18：带 timeout，不无限等待）——design §3.4 CancelTimeout。"""

    def __init__(self, message: str = "workflow cancel timed out") -> None:
        super().__init__(message, code=WORKFLOW_CANCEL_TIMEOUT)


class WorkflowBackendUnavailableError(WorkflowEngineError):
    """backend 不可达/超时经 fail policy 兜底后的定义错误（E-01，非裸异常）。"""

    def __init__(self, message: str = "workflow backend unavailable") -> None:
        super().__init__(message, code=WORKFLOW_BACKEND_UNAVAILABLE)
