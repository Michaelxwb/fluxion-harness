"""执行输入端口（事件/检查点）；agent-core 不持有持久化或服务依赖。

事件端口是**宿主中立**的：Runtime 实现把它写成 canonical_event，Worker 实现写成 task_event，
Kernel 只按同一组方法读事件、写回合 checkpoint、并询问这一轮之后是否要继续等。
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from muad_contracts import OperationWatermark

from ..model.provider import ModelMessage


class WaitDecision(StrEnum):
    CONTINUE = "CONTINUE"
    WAIT = "WAIT"
    FINISH = "FINISH"


@dataclass(frozen=True, slots=True)
class RunnerCheckpoint:
    turns: int = 0
    tool_calls: int = 0
    input_tokens: int | None = 0
    output_tokens: int | None = 0
    consumed_event_seq: int = 0
    #: 工具级挂起里还没拿到终态结果的异步操作（按 `call_id` 聚合，各带自己的水位）。
    #: 它属于「一次挂起」而不是「一次 Run」：完整模型回合边界的挂起（Human）恒为空。
    pending_tool_calls: tuple[OperationWatermark, ...] = ()
    #: 失败跟踪的可选持久形态（design §4.3）；旧 checkpoint 缺失时为空，恢复不臆造失败。
    failure_state: dict[str, Any] | None = None
    # Reconstructed by the host from completed tool events on takeover.
    recovered_window_limit: bool = False
    recovered_window_text: str = ""

    def __post_init__(self) -> None:
        for value in (
            self.turns,
            self.tool_calls,
            self.input_tokens,
            self.output_tokens,
            self.consumed_event_seq,
        ):
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                raise ValueError("checkpoint counters must be nonnegative integers or unknown tokens")
        call_ids = [watermark.call_id for watermark in self.pending_tool_calls]
        if len(call_ids) != len(set(call_ids)):
            raise ValueError("checkpoint pending tool calls must be unique per call_id")

    def as_state(self) -> dict[str, Any]:
        """checkpoint 被宿主原样落库，所以它的可持久形态由 Kernel 自己给出。

        不能依赖宿主的 `asdict`/`json.dumps`：待定操作是 contracts 的模型，`asdict` 不递归
        转换它们，写进 JSON 列就会直接失败。
        """
        return {
            "turns": self.turns,
            "tool_calls": self.tool_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "consumed_event_seq": self.consumed_event_seq,
            "pending_tool_calls": [
                {
                    "operation_id": str(watermark.operation_id),
                    "call_id": watermark.call_id,
                    "status": watermark.status.value,
                    "receipt_seq": watermark.receipt_seq,
                    "consumed_event_id": (
                        str(watermark.consumed_event_id) if watermark.consumed_event_id else None
                    ),
                }
                for watermark in self.pending_tool_calls
            ],
            "failure_state": self.failure_state,
        }


@dataclass(frozen=True, slots=True)
class ExecutionEventBatch:
    messages: tuple[ModelMessage, ...]
    cursor: int
    has_more: bool = False


class ExecutionEventPort(Protocol):
    async def drain_ready(self, cursor: int, limit: int) -> ExecutionEventBatch: ...

    async def checkpoint(self, checkpoint: RunnerCheckpoint) -> None: ...

    async def try_wait(self, checkpoint: RunnerCheckpoint, *, assistant_text: str) -> WaitDecision: ...
