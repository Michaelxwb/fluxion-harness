"""中立挂起/恢复协议：Kernel 只认「问题、输入 schema、原因、恢复载荷」。

同一套端口有两个实现——Runtime（交互式 Run 的记忆确认）与 Worker（Task Execution 的 ASYNC
工具）——所以本模块**不得**出现任一宿主的形状：没有 Memory 的 proposal、没有 RunRecord、
没有 task_execution。适配器自己的数据（例如记忆确认的 `proposal`）留在适配器里，Kernel 只看
`SuspensionRequest` 的中立字段。

两类挂起点由 `SuspensionKind` 区分，写进 checkpoint 的内容也不同：

- `HUMAN_INPUT`：完整模型回合边界（这一轮的模型响应与它的全部工具结果都收口之后）等人回答；
- `TOOL_OPERATION`：同一回合里提交了异步操作，等外部结果；待定的调用以
  `OperationWatermark` 逐条记水位（`call_id` 聚合，`receipt_seq`/`consumed_event_id` 各自独立），
  全部终态才继续，部分失败不丢已完成的结果。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

from muad_contracts import OperationStatus, OperationWatermark

from ..model.provider import ModelMessage
from .continuation import RunnerCheckpoint


class SuspensionKind(StrEnum):
    """挂起点：整模型回合边界（等人回答）或工具级（等外部操作结果）。"""

    HUMAN_INPUT = "HUMAN_INPUT"
    TOOL_OPERATION = "TOOL_OPERATION"


#: 操作终态：只有这些结果能把这一个 `call_id` 从待定集合里拿掉。
#: `LATE` 也算终态 —— 执行已经收口，迟到结果只登记、绝不把执行拉回等待。
TERMINAL_OPERATION_STATUSES = frozenset(
    (
        OperationStatus.COMPLETED,
        OperationStatus.FAILED,
        OperationStatus.CANCELLED,
        OperationStatus.LATE,
    )
)


@dataclass(frozen=True, slots=True)
class SuspensionRequest:
    """适配器交给 Kernel 的挂起请求：**只有这些字段是 Kernel 的语义**。

    `reason` 是宿主自己的分类（记忆确认、工具授权、异步操作……），Kernel 原样写进中断事件，
    不解释、也不按它分支——分支只看 `kind`。
    """

    interrupt_id: UUID
    reason: str
    #: 给用户看的问题；Human 挂起时它就是收口文案。
    prompt: str
    #: 文本渠道（企微等）的排版版本；缺省回落到 `prompt`。
    text_prompt: str = ""
    kind: SuspensionKind = SuspensionKind.HUMAN_INPUT
    #: 恢复载荷要满足的 JSON schema；缺省表示只接受自由文本回答。
    input_schema: Mapping[str, Any] = field(default_factory=dict)
    #: 工具级挂起的待定操作（同一回合可多个，按 `call_id` 聚合）。
    pending_tool_calls: tuple[OperationWatermark, ...] = ()
    deadline_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError("suspension reason must not be blank")
        if not self.prompt.strip():
            raise ValueError("suspension prompt must not be blank")
        if self.kind is SuspensionKind.TOOL_OPERATION and not self.pending_tool_calls:
            raise ValueError("TOOL_OPERATION suspension requires pending tool calls")
        if self.kind is SuspensionKind.HUMAN_INPUT and self.pending_tool_calls:
            # Human 只在完整模型回合边界挂起，那一轮的工具早已收口：带待定操作就是两种挂起混写。
            raise ValueError("HUMAN_INPUT suspension must not carry pending tool calls")
        call_ids = [watermark.call_id for watermark in self.pending_tool_calls]
        if len(call_ids) != len(set(call_ids)):
            raise ValueError("pending tool calls must be unique per call_id")


@dataclass(frozen=True, slots=True)
class SuspensionResume:
    """一次挂起的恢复载荷：适配器把回答/外部结果整理成它，再交回同一个执行的 `run()`。

    `messages` 是恢复后要追加进历史的消息（用户回答、或已就绪的工具结果）；`consumed` 是这一
    次消费到的操作水位——结果可能乱序到达，每条只推进自己那个 `call_id`。
    """

    interrupt_id: UUID
    kind: SuspensionKind
    messages: tuple[ModelMessage, ...] = ()
    consumed: tuple[OperationWatermark, ...] = ()


def resume_checkpoint(checkpoint: RunnerCheckpoint, resume: SuspensionResume) -> RunnerCheckpoint:
    """把恢复载荷并入 checkpoint：**只移除已终态的 operation**。

    返回的 `pending_tool_calls` 非空就是还没等齐——调用方据此继续等待而不是恢复模型回合；
    为空才代表这一挂起可以继续。三条口径：

    - 乱序结果只推进自己那一条，不影响别的 `call_id`；
    - 未知 `call_id`（重复投递、或执行已终态后的迟到结果）**不扩表**，由适配器按 `event_id`
      记 late/事件，本函数只做 checkpoint 记账；
    - 一次恢复里同一 `call_id` 出现多条时，取 `receipt_seq` 最大的那条。
    """
    if not checkpoint.pending_tool_calls:
        return checkpoint
    observed: dict[str, OperationWatermark] = {}
    for watermark in resume.consumed:
        previous = observed.get(watermark.call_id)
        if previous is None or watermark.receipt_seq >= previous.receipt_seq:
            observed[watermark.call_id] = watermark
    pending: list[OperationWatermark] = []
    for watermark in checkpoint.pending_tool_calls:
        update = observed.pop(watermark.call_id, None)
        if update is None:
            pending.append(watermark)
        elif update.status not in TERMINAL_OPERATION_STATUSES:
            pending.append(update)
    return RunnerCheckpoint(
        checkpoint.turns,
        checkpoint.tool_calls,
        checkpoint.input_tokens,
        checkpoint.output_tokens,
        checkpoint.consumed_event_seq,
        tuple(pending),
    )


class ToolSuspensionPort(Protocol):
    """挂起端口的**唯一**契约：看过这一回合后给出待挂起请求，并在挂起时落库。"""

    @property
    def suspend_request(self) -> SuspensionRequest | None: ...

    async def suspend(self, progress: RunnerCheckpoint) -> None: ...


__all__ = [
    "TERMINAL_OPERATION_STATUSES",
    "SuspensionKind",
    "SuspensionRequest",
    "SuspensionResume",
    "ToolSuspensionPort",
    "resume_checkpoint",
]
