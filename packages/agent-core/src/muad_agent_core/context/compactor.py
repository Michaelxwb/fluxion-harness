"""Request-boundary context fitting and omission marker.

This module is pure logic: same input, same output; no DB, environment or model calls. The
`ContextPlan` is the only input; `fit_history` is the only place that drops history messages.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from ..model.budget import MESSAGE_PROTOCOL_OVERHEAD_BYTES
from ..model.provider import ModelMessage, ModelRole, text_of

if TYPE_CHECKING:
    from .plan import ContextPlan

Group = tuple[ModelMessage, ...]

#: 被省掉的历史用一条 SYSTEM 消息顶位，形状与语义都区别于真实历史。
OMISSION_MARKER_PREFIX = "[历史裁剪]"


def message_bytes(message: ModelMessage) -> int:
    """单条消息在请求体里的 UTF-8 字节数（内容 + 思维链 + 工具名与参数）。"""
    size = len(text_of(message.content).encode("utf-8"))
    if message.reasoning_content:
        size += len(message.reasoning_content.encode("utf-8"))
    if message.tool_call_id:
        size += len(message.tool_call_id.encode("utf-8"))
    for call in message.tool_calls:
        size += len(call.name.encode("utf-8"))
        size += len(json.dumps(dict(call.arguments), ensure_ascii=False).encode("utf-8"))
    return size


def history_bytes(messages: Sequence[ModelMessage]) -> int:
    """整段历史的字节数——**单遍**，不逐条重复编码。"""
    return sum(message_bytes(message) for message in messages)


def estimate_message_tokens(message: ModelMessage) -> int:
    """保守估算：1 token ≈ 1 UTF-8 字节 + 每条消息协议开销。"""
    return message_bytes(message) + MESSAGE_PROTOCOL_OVERHEAD_BYTES


def estimate_messages_tokens(messages: Sequence[ModelMessage]) -> int:
    return sum(estimate_message_tokens(message) for message in messages)


def _declares(group: Group, tool_call_id: str | None) -> bool:
    if tool_call_id is None:
        return False
    return any(call.id == tool_call_id for message in group for call in message.tool_calls)


def split_groups(messages: Sequence[ModelMessage]) -> tuple[Group, ...]:
    """切成"最小不可分单元"：组从非 TOOL 消息开始，其后的 TOOL 结果并入该组。

    孤儿 TOOL（前面没有任何声明它的 assistant `tool_calls`）整条丢弃——它是历史里既有的坏
    形状，并进任何组都会让压缩产物被供应商直接拒绝。
    """
    groups: list[Group] = []
    current: Group | None = None
    for message in messages:
        if message.role is ModelRole.TOOL:
            if current is not None and _declares(current, message.tool_call_id):
                current = (*current, message)
            continue
        if current is not None:
            groups.append(current)
        current = (message,)
    if current is not None:
        groups.append(current)
    return tuple(groups)


def split_protected_prefix(
    messages: Sequence[ModelMessage],
) -> tuple[tuple[ModelMessage, ...], tuple[ModelMessage, ...]]:
    """切成（受保护前缀, 可压缩的对话区）。

    前缀是开头连续的一段 `role=SYSTEM`：系统提示、memory 注入、摘要前缀都在这一段里。
    """
    index = 0
    while index < len(messages) and messages[index].role is ModelRole.SYSTEM:
        index += 1
    return tuple(messages[:index]), tuple(messages[index:])


@dataclass(frozen=True, slots=True)
class LayerOutcome:
    """一层压缩的结果。`groups` 是丢弃/降级的组数；`dropped_records` 是事件级计数。"""

    layer: str
    fired: bool
    groups: int
    bytes_before: int
    bytes_after: int
    messages: tuple[ModelMessage, ...]
    dropped_records: int = 0
    retained_from_seq: int | None = None

    @property
    def bytes_saved(self) -> int:
        return max(0, self.bytes_before - self.bytes_after)

    def as_layer_payload(self) -> dict[str, Any]:
        return {
            "layer": self.layer,
            "fired": self.fired,
            "groups": self.groups,
            "bytes_saved": self.bytes_saved,
            "dropped_records": self.dropped_records,
            "retained_from_seq": self.retained_from_seq,
        }


def _group_seq(group: Group) -> int:
    return max((message.source_event_seq or 0 for message in group), default=0)


def _group_source_ids(group: Group) -> tuple[str, ...]:
    return tuple(
        message.source_event_id
        for message in group
        if message.source_event_id
    )


def _dropped_record_count(dropped: Sequence[Group]) -> int:
    ids = {source for group in dropped for source in _group_source_ids(group)}
    return len(ids) if ids else len(dropped)


def _latest_complete_tool_round(working: Sequence[Group]) -> int | None:
    for index in range(len(working) - 1, -1, -1):
        if any(message.tool_calls for message in working[index]):
            return index
    return None


def _protected_current_index(plan: ContextPlan) -> int | None:
    """Fallback current-turn index in older when the plan has no current pinned group."""
    if any(
        message.role is ModelRole.USER
        for group in plan.pinned
        for message in group
    ):
        return None
    if plan.run_input is not None:
        return None
    for index in range(len(plan.older) - 1, -1, -1):
        if any(message.role is ModelRole.USER for message in plan.older[index]):
            return index
    return None


def _marker_content(dropped: Sequence[Group], earlier_records: int | None) -> str:
    known = _dropped_record_count(dropped)
    if earlier_records is None:
        count = max(1, known + 1)
        amount = f"至少 {count} 条"
    else:
        amount = f"{known + earlier_records} 条"
    return (
        f"{OMISSION_MARKER_PREFIX} 本次请求省略了中间的 {amount} 对话记录"
        "（消息与工具调用），它们不在当前上下文中；保留的是会话开头目标与最近对话。"
        "需要时请让用户重新提供被省略的内容。"
    )


def fit_history(
    plan: ContextPlan,
    *,
    budget_tokens: int,
) -> tuple[ContextPlan, LayerOutcome | None]:
    """Protect prefix/pinned/head/latest round, then fill the budget from newest to oldest."""
    if isinstance(budget_tokens, bool) or budget_tokens < 1:
        raise ValueError("budget_tokens must be a positive integer")

    older = list(plan.older)
    working = list(plan.working)
    head_indexes = {
        index
        for index, group in enumerate(older)
        if plan.head_end_seq is not None and 0 < _group_seq(group) <= plan.head_end_seq
    }
    if not plan.pinned:
        fallback = _protected_current_index(plan)
    else:
        fallback = None

    latest_round = _latest_complete_tool_round(working)
    protected_working = (
        set(range(latest_round, len(working)))
        if latest_round is not None
        else ({len(working) - 1} if working else set())
    )
    protected_older = set(head_indexes)
    if fallback is not None:
        protected_older.add(fallback)
    protected_groups = [
        *(older[index] for index in protected_older),
        *plan.pinned,
        *(working[index] for index in protected_working),
    ]
    used = sum(estimate_messages_tokens(group) for group in protected_groups)

    kept_older = set(protected_older)
    for index in range(len(older) - 1, -1, -1):
        if index in kept_older:
            continue
        size = estimate_messages_tokens(older[index])
        if used + size <= budget_tokens:
            kept_older.add(index)
            used += size

    kept_working = set(protected_working)
    for index in range(len(working) - 1, -1, -1):
        if index in kept_working:
            continue
        size = estimate_messages_tokens(working[index])
        if used + size <= budget_tokens:
            kept_working.add(index)
            used += size

    dropped_older = [group for index, group in enumerate(older) if index not in kept_older]
    dropped_working = [group for index, group in enumerate(working) if index not in kept_working]
    dropped = [*dropped_older, *dropped_working]
    if not dropped and plan.earlier_records == 0:
        return plan, None

    retained_older = [group for index, group in enumerate(older) if index in kept_older]
    retained_working = [group for index, group in enumerate(working) if index in kept_working]
    marker_position = sum(1 for index in sorted(kept_older) if index in head_indexes)
    older_with_marker = [
        *retained_older[:marker_position],
        (ModelMessage(role=ModelRole.SYSTEM, content=_marker_content(dropped, plan.earlier_records)),),
        *retained_older[marker_position:],
    ]
    before = history_bytes(
        [*(message for group in older for message in group), *(m for group in working for m in group)]
    )
    after_messages = [
        *(message for group in older_with_marker for message in group),
        *(message for group in retained_working for message in group),
    ]
    after = history_bytes(after_messages)
    retained_seqs = [
        _group_seq(group)
        for group in [*retained_older, *retained_working]
        if _group_seq(group) > (plan.head_end_seq or 0)
    ]
    return (
        replace(
            plan,
            older=tuple(older_with_marker),
            working=tuple(retained_working),
            material_end_seq=0,
        ),
        LayerOutcome(
            layer="budget",
            fired=True,
            groups=len(dropped),
            bytes_before=before,
            bytes_after=after,
            messages=tuple(after_messages),
            dropped_records=(
                _dropped_record_count(dropped)
                + (plan.earlier_records if plan.earlier_records is not None else 0)
            ),
            retained_from_seq=min(retained_seqs, default=None),
        ),
    )


@runtime_checkable
class ContextCompactor(Protocol):
    """Request-boundary compression hook; implementations must degrade to no compression."""

    async def compact_plan(self, plan: ContextPlan, *, budget_ratio: float = 1.0) -> ContextPlan: ...


__all__ = [
    "ContextCompactor",
    "Group",
    "LayerOutcome",
    "OMISSION_MARKER_PREFIX",
    "estimate_message_tokens",
    "estimate_messages_tokens",
    "fit_history",
    "history_bytes",
    "message_bytes",
    "split_groups",
    "split_protected_prefix",
]
