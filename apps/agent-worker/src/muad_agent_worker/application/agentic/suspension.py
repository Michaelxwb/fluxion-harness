"""`ToolSuspensionPort` 的 Worker 实现：整回合挂起请求的**暂存**（不写库）。

为什么不在 `suspend()` 里直接写 `WAITING_EXTERNAL`/`WAITING_HUMAN`：

- Kernel 在**执行期**调用它，那一刻 WorkerLoop 的心跳仍在续租。任何把执行行改成等待并释放
  租约的写入都会与心跳抢同一行（续租失败 → 心跳叫停本地执行 → 结果被丢弃）。
- 等待状态的唯一写入方因此是 `WorkerLoop`：它在**停下心跳之后**，把 Kernel 交回的 checkpoint、
  等待状态、Human 问题与事件一次事务写完（design §3.5.8 的"同一事务"）。

本端口只做两件事：暂存挂起请求与挂起边界上的进度，并把多轮异步提交按 `call_id` 聚合。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from muad_agent_core.agent import SuspensionKind, SuspensionRequest
from muad_agent_core.agent.continuation import RunnerCheckpoint
from muad_contracts import OperationStatus, OperationWatermark, TerminalStatus

#: 宿主自己的挂起分类（原样写进中断事件；Kernel 不解释）。
ASYNC_OPERATION_REASON = "ASYNC_OPERATION"
HUMAN_INPUT_REASON = "HUMAN_INPUT"
ASYNC_SUBMITTED_PROMPT = "已提交异步任务，等待结果后继续。"


@dataclass(frozen=True, slots=True)
class SuspensionBoundary:
    """一次挂起的完整事实：请求（问题/待定操作）与挂起边界上的 checkpoint。"""

    request: SuspensionRequest
    progress: RunnerCheckpoint


class AgenticSuspension:
    def __init__(self, *, execution_id: UUID) -> None:
        self._execution_id = execution_id
        self._request: SuspensionRequest | None = None
        self._progress: RunnerCheckpoint | None = None
        self._pending: dict[str, OperationWatermark] = {}

    @property
    def suspend_request(self) -> SuspensionRequest | None:
        return self._request

    async def suspend(self, progress: RunnerCheckpoint) -> None:
        """记下挂起边界上的进度；状态写入交给 WorkerLoop。"""
        if self._request is None:
            raise ValueError("suspension was requested without a staged request")
        self._progress = progress

    def stage_operation(self, watermark: OperationWatermark, *, prompt: str = ASYNC_SUBMITTED_PROMPT) -> None:
        """登记一个已提交、尚未终态的异步调用（同一 `call_id` 只保留最新水位）。"""
        self._pending[watermark.call_id] = watermark
        self._request = SuspensionRequest(
            interrupt_id=uuid4(),
            reason=ASYNC_OPERATION_REASON,
            prompt=prompt,
            kind=SuspensionKind.TOOL_OPERATION,
            pending_tool_calls=self.pending_watermarks(),
        )

    def clear_operation(self, call_id: str) -> None:
        """结果已就绪（未进入等待）：把这一条从待定集合里拿掉。"""
        self._pending.pop(call_id, None)
        if not self._pending:
            self._request = None
            return
        assert self._request is not None
        self._request = SuspensionRequest(
            interrupt_id=self._request.interrupt_id,
            reason=self._request.reason,
            prompt=self._request.prompt,
            kind=SuspensionKind.TOOL_OPERATION,
            pending_tool_calls=self.pending_watermarks(),
            deadline_at=self._request.deadline_at,
        )

    def stage_human(
        self,
        *,
        question: str,
        input_schema: Mapping[str, object] | None = None,
        deadline_at: datetime,
        prompt: str | None = None,
    ) -> None:
        """暂存一次 Human 挂起（问题与输入 schema 由触发方给出；回答链路是 TASK-021）。"""
        if self._pending:
            raise ValueError("a tool-operation suspension is already staged")
        self._request = SuspensionRequest(
            interrupt_id=uuid4(),
            reason=HUMAN_INPUT_REASON,
            prompt=prompt or question,
            text_prompt=question,
            kind=SuspensionKind.HUMAN_INPUT,
            input_schema=dict(input_schema or {}),
            deadline_at=deadline_at,
        )

    def pending_watermarks(self) -> tuple[OperationWatermark, ...]:
        return tuple(self._pending[key] for key in sorted(self._pending))

    def boundary(self) -> SuspensionBoundary | None:
        if self._request is None or self._progress is None:
            return None
        return SuspensionBoundary(request=self._request, progress=self._progress)

    def take(self) -> SuspensionBoundary | None:
        boundary = self.boundary()
        self._request, self._progress = None, None
        return boundary


def pending_payload(watermarks: Sequence[OperationWatermark]) -> dict[str, object]:
    """`wait_payload_json` 的形状：`operations[]` 逐条带自己的水位（design §3.5.6）。"""
    return {
        "operations": [
            {
                "operation_id": str(item.operation_id),
                "call_id": item.call_id,
                "status": item.status.value,
                "receipt_seq": item.receipt_seq,
                "consumed_event_id": (
                    str(item.consumed_event_id) if item.consumed_event_id else None
                ),
            }
            for item in watermarks
        ]
    }


TERMINAL_OPERATION_VALUES = frozenset({"COMPLETED", "FAILED", "CANCELLED", "LATE"})


def settled_status(status: OperationStatus | TerminalStatus) -> bool:
    """终态判定与 Kernel 的 checkpoint 口径一致（`LATE` 也算终态）。"""
    return status.value in TERMINAL_OPERATION_VALUES


__all__ = [
    "ASYNC_OPERATION_REASON",
    "AgenticSuspension",
    "HUMAN_INPUT_REASON",
    "SuspensionBoundary",
    "pending_payload",
    "settled_status",
]
