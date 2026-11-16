"""Task-owned 异步结果的水位：**按 `call_id` 逐条推进**，补扫与恢复共用同一份事实。

五条口径（design §3.1.5「ASYNC 竞态协议」、§3.2.4 工具级挂起）：

- **每个调用一条水位**：`wait_payload_json.operations[]` 的每一项只描述一个 `call_id`，
  `status`/`receipt_seq`/`consumed_event_id` 各自独立。没有全局水位，所以一个乱序早到的
  结果不会把别的调用一起跳过；一个还没到的结果也不会被别人的终态掩盖。
- **就绪以 Task-owned 收件箱为准**：`view.result` 存在就是结果已到。operation 行的状态列
  可能停在 `RESULT_RECEIVED`/`MATERIALIZED`（结果到达与物化是两件事），拿它判"未就绪"
  会让执行永远等下去。
- **重复结果按 `event_id` 幂等**：同一个 `event_id` 推进多少次都得到同一水位；
  `receipt_seq` 只增不减，迟到的旧结果不会把水位推回去。
- **调用身份唯一**：崩后按 `source_tool_call_id` 找回 operation（重提交由 Runtime 幂等返回
  同一行），`input_hash` 由 Runtime 比对——换参数是显式 `TOOL_OPERATION_INPUT_MISMATCH`，
  绝不静默开第二个业务调用。
- **只计算、不写行**：写水位的是 Worker 自己的事务（`WorkerLoop._wait` 的等待状态、
  `AsyncResultSweeper` 的补扫推进），本模块只做有界读与纯计算。
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from muad_contracts import (
    CompletionMode,
    OperationStatus,
    OperationWatermark,
    TaskToolOperationList,
    TaskToolOperationView,
    TerminalStatus,
)

from ...infrastructure.task_operation_client import PAGE_SIZE, operation_result_content
from .suspension import TERMINAL_OPERATION_VALUES, pending_payload


class OperationReader(Protocol):
    """Runtime API-30 的只读面（`TaskOperationClient` 结构上满足它）。"""

    async def get(
        self, *, tenant_id: str, actor_user_id: UUID, operation_id: UUID
    ) -> TaskToolOperationView: ...

    async def list_operations(
        self,
        *,
        tenant_id: str,
        actor_user_id: UUID,
        execution_id: UUID,
        ready: bool | None = None,
        page: int = 1,
        page_size: int = PAGE_SIZE,
    ) -> TaskToolOperationList: ...


class OperationSubmitter(OperationReader, Protocol):
    """提交面：崩溃恢复要按调用身份找回 operation 并让 Runtime 复检 `input_hash`。"""

    async def submit(
        self,
        *,
        tenant_id: str,
        actor_user_id: UUID,
        execution_id: UUID,
        call_id: str,
        tool_name: str,
        arguments: Mapping[str, Any],
        completion_mode: CompletionMode = CompletionMode.JOIN,
    ) -> TaskToolOperationView: ...


@dataclass(frozen=True, slots=True)
class DeclaredCall:
    """模型回合里声明过的一次工具调用（`task_event.ASSISTANT_TURN.tool_calls[]` 的一条）。

    `completed` 为假说明这一回合**没有**对应的 tool 结果事件——提交后、回合收口前就崩了，
    调用是否已经落到 Runtime 未知。这条路径必须按调用身份重提交（幂等 + `input_hash` 复检），
    绝不能假定"没记录就是没发生"。
    """

    call_id: str
    tool_name: str
    arguments: Mapping[str, Any]
    completed: bool = False


def watermarks_from_payload(payload: Mapping[str, Any] | None) -> tuple[OperationWatermark, ...]:
    """从落库的 `wait_payload_json` 读回每个调用自己的水位。

    写入端（`interpret_execution` / `pending_payload`）已保证 `operations[]` 是非空对象数组，
    这里只是读回；非对象项忽略——坏载荷的后果是"没有水位可推进"（补扫只轮转、不改事实）。
    """
    if not isinstance(payload, Mapping):
        return ()
    entries = payload.get("operations")
    if not isinstance(entries, list):
        return ()
    return tuple(
        OperationWatermark.model_validate(entry) for entry in entries if isinstance(entry, Mapping)
    )


def payload_from_watermarks(watermarks: Sequence[OperationWatermark]) -> dict[str, object]:
    """水位 → `wait_payload_json`：与 `suspension.pending_payload` 同一形状，一个来源。"""
    return pending_payload(watermarks)


def settled(watermark: OperationWatermark) -> bool:
    """这一条调用是否已经拿到终态（`LATE` 也算终态：执行已收口，只登记不恢复）。"""
    return watermark.status.value in TERMINAL_OPERATION_VALUES


def all_settled(watermarks: Sequence[OperationWatermark]) -> bool:
    """**全部**调用终态才继续——部分完成不等于可以恢复模型回合。"""
    return bool(watermarks) and all(settled(item) for item in watermarks)


def watermark_of(view: TaskToolOperationView) -> OperationWatermark:
    """operation 读模型 → 该调用自己的水位（结果没到就是 operation 当前状态）。"""
    result = view.result
    if result is None:
        return OperationWatermark(
            operation_id=view.operation_id,
            call_id=view.source_tool_call_id,
            status=view.status,
        )
    return OperationWatermark(
        operation_id=view.operation_id,
        call_id=view.source_tool_call_id,
        status=_settled_status(result.late, result.terminal_status),
        receipt_seq=result.task_event_seq,
        consumed_event_id=result.event_id,
    )


def _settled_status(late: bool, terminal: TerminalStatus) -> OperationStatus:
    """结果事实 → 水位状态：迟到一律记 `LATE`，否则取结果自身的终态。"""
    return OperationStatus.LATE if late else OperationStatus(terminal.value)


def advance_watermarks(
    watermarks: Sequence[OperationWatermark],
    views: Mapping[str, TaskToolOperationView],
) -> tuple[OperationWatermark, ...]:
    """按 `call_id` 逐条推进水位；缺失/未就绪的调用原地不动，`receipt_seq` 只增不减。"""
    advanced: list[OperationWatermark] = []
    for watermark in watermarks:
        view = views.get(watermark.call_id)
        if view is None or view.result is None:
            advanced.append(watermark)
            continue
        candidate = watermark_of(view)
        if candidate.receipt_seq < watermark.receipt_seq:
            advanced.append(watermark)
            continue
        advanced.append(
            watermark.model_copy(
                update={
                    "status": candidate.status,
                    "receipt_seq": candidate.receipt_seq,
                    "consumed_event_id": candidate.consumed_event_id,
                }
            )
        )
    return tuple(advanced)


def views_by_call(views: Sequence[TaskToolOperationView]) -> dict[str, TaskToolOperationView]:
    """按调用身份索引：`call_id` 是水位与恢复的共同键（design §3.5.3 末条）。"""
    return {view.source_tool_call_id: view for view in views}


async def read_operations(
    operations: OperationReader,
    *,
    tenant_id: str,
    actor_user_id: UUID,
    execution_id: UUID,
) -> dict[str, TaskToolOperationView]:
    """按 owner 读该执行的全部 operation（一页有界；读不全按未就绪处理，保守正确）。"""
    listing: TaskToolOperationList = await operations.list_operations(
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        execution_id=execution_id,
        page=1,
        page_size=PAGE_SIZE,
    )
    return views_by_call(listing.items)


@dataclass(frozen=True, slots=True)
class ReconciledOperations:
    """崩后重建的一次对账：还没终态的水位 + 已就绪结果（按 `call_id` 的模型可见正文）。"""

    pending: tuple[OperationWatermark, ...]
    ready: dict[str, str]


async def reconcile_operations(
    operations: OperationSubmitter,
    *,
    views: Mapping[str, TaskToolOperationView],
    tenant_id: str,
    actor_user_id: UUID,
    execution_id: UUID,
    declared: Sequence[DeclaredCall],
    async_calls: Collection[str],
    pending: Sequence[OperationWatermark],
) -> ReconciledOperations:
    """按调用身份把"声明过但结果没进历史"的调用找回来。

    覆盖两个崩溃窗口：

    - **提交后、回合收口前**崩溃：事件里只有 assistant 声明、没有 tool 结果 ⇒ 重提交同一
      `call_id`（Runtime 幂等返回同一 operation，换参数报 `TOOL_OPERATION_INPUT_MISMATCH`），
      不创建第二个业务调用；
    - **回合收口后、checkpoint 前**崩溃：事件里的 tool 结果还是提交回执 ⇒ 按 `call_id` 命中
      operation 后换成真实结果，不重复提交。

    已终态的调用进 `ready`（由恢复方替换成 tool 消息），未终态的进 `pending`（补写等待）。
    """

    ready: dict[str, str] = {}
    unsettled: dict[str, OperationWatermark] = {
        watermark.call_id: watermark
        for watermark in advance_watermarks(pending, views)
        if not settled(watermark)
    }
    for call in declared:
        view = views.get(call.call_id)
        if not call.completed:
            if call.tool_name not in async_calls or call.call_id in unsettled:
                continue
            # 崩溃窗口：round 收口前就断了，不知道提交是否已落库。重提交同一调用身份——
            # Runtime 幂等返回同一 operation，换参数则显式 TOOL_OPERATION_INPUT_MISMATCH。
            view = await operations.submit(
                tenant_id=tenant_id,
                actor_user_id=actor_user_id,
                execution_id=execution_id,
                call_id=call.call_id,
                tool_name=call.tool_name,
                arguments=dict(call.arguments),
            )
        if view is None:
            continue
        if view.result is not None:
            unsettled.pop(call.call_id, None)
            ready[call.call_id] = operation_result_content(view.result)
            continue
        unsettled[call.call_id] = watermark_of(view)
    return ReconciledOperations(
        pending=tuple(unsettled[key] for key in sorted(unsettled)), ready=ready
    )


__all__ = [
    "DeclaredCall",
    "OperationReader",
    "OperationSubmitter",
    "ReconciledOperations",
    "advance_watermarks",
    "all_settled",
    "payload_from_watermarks",
    "read_operations",
    "reconcile_operations",
    "settled",
    "views_by_call",
    "watermark_of",
    "watermarks_from_payload",
]
