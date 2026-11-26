"""Explicit context plan: current Run input, pinned skill instructions, older and working history."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import StrEnum

from ..model.provider import ModelMessage
from .compactor import (
    Group,
    history_bytes,
    split_groups,
    split_protected_prefix,
)
from .summary import is_summary_message


class MessageOrigin(StrEnum):
    SYSTEM = "SYSTEM"
    USER_INPUT = "USER_INPUT"
    USER_ANSWER = "USER_ANSWER"
    TOOL_FOLLOW_UP = "TOOL_FOLLOW_UP"
    BACKGROUND_RESULT = "BACKGROUND_RESULT"
    ASSISTANT = "ASSISTANT"
    TOOL = "TOOL"
    SUMMARY = "SUMMARY"
    MEMORY = "MEMORY"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class RunInput:
    message_id: str
    content: str
    origin: MessageOrigin = MessageOrigin.USER_INPUT
    source: str = "USER"


@dataclass(frozen=True, slots=True)
class ContextPlan:
    prefix: tuple[ModelMessage, ...]
    pinned: tuple[Group, ...]
    older: tuple[Group, ...]
    working: tuple[Group, ...]
    covered_seq: int = 0
    material_end_seq: int = 0
    head_end_seq: int | None = None
    earlier_records: int | None = 0
    pinned_bytes: int = 0
    pinned_omitted: tuple[str, ...] = ()
    run_input: RunInput | None = None
    active_skills: tuple[str, ...] = ()

    def all_messages(self) -> tuple[ModelMessage, ...]:
        return (
            *self.prefix,
            *(message for group in self.older for message in group),
            *(message for group in self.pinned for message in group),
            *(message for group in self.working for message in group),
        )


def plan_context(
    messages: Sequence[ModelMessage],
    *,
    run_input: RunInput | None,
    active_skills: Sequence[str] = (),
    protected_budget_bytes: int = 0,
    covered_seq: int = 0,
    material_scan_end_seq: int | None = None,
    head_end_seq: int | None = None,
    earlier_records: int | None = 0,
) -> ContextPlan:
    """Build the plan from explicit input + active skills; no "first/last user" inference."""
    prefix, body = split_protected_prefix(messages)
    groups = list(split_groups(body))
    current_index = _current_group_index(groups, run_input)
    pinned_indexes = _pinned_indexes(groups, active_skills, current_index)
    older_groups = tuple(
        group
        for index, group in enumerate(groups)
        if index < (current_index if current_index is not None else len(groups))
        and index not in pinned_indexes
    )
    working_groups = tuple(
        group
        for index, group in enumerate(groups)
        if current_index is not None and index > current_index and index not in pinned_indexes
    )
    pinned_groups = tuple(groups[index] for index in sorted(pinned_indexes))

    budget = protected_budget_bytes
    kept: list[Group] = []
    omitted: list[str] = []
    used = 0
    for index in sorted(pinned_indexes, key=lambda value: _pinned_order(value, current_index)):
        group = groups[index]
        size = history_bytes(group)
        if budget > 0 and kept and used + size > budget:
            omitted.extend(_group_skill_keys(group))
            continue
        kept.append(group)
        used += size
    pinned_groups = tuple(kept)
    material_end = _material_end_seq(
        older_groups,
        covered_seq=covered_seq,
        material_scan_end_seq=material_scan_end_seq,
    )
    return ContextPlan(
        prefix=prefix,
        pinned=pinned_groups,
        older=older_groups,
        working=working_groups,
        covered_seq=covered_seq,
        material_end_seq=material_end,
        head_end_seq=head_end_seq,
        earlier_records=earlier_records,
        pinned_bytes=used,
        pinned_omitted=tuple(dict.fromkeys(omitted)),
        run_input=run_input,
        active_skills=tuple(active_skills),
    )


def material_groups(plan: ContextPlan) -> tuple[Group, ...]:
    """`older` 中落在 `(covered_seq, material_end_seq]` 的连续材料组。"""
    if plan.material_end_seq <= plan.covered_seq:
        return ()
    groups: list[Group] = []
    for group in plan.older:
        seqs = [message.source_event_seq or 0 for message in group]
        if not seqs:
            continue
        if min(seqs) > plan.covered_seq and max(seqs) <= plan.material_end_seq:
            groups.append(group)
    return tuple(groups)


def pinned_event_ids(plan: ContextPlan) -> tuple[str, ...]:
    return tuple(
        message.source_event_id
        for group in plan.pinned
        for message in group
        if message.source_event_id
    )


def with_summary(plan: ContextPlan, summary: ModelMessage) -> ContextPlan:
    """只替换材料区间：保留头部、独立尾部与 pinned，并推进 covered_seq。"""
    material = material_groups(plan)
    if not material:
        return plan
    kept_older = tuple(group for group in plan.older if group not in material)
    retained_head = (
        max(
            (
                max(message.source_event_seq or 0 for message in group)
                for group in kept_older
                if plan.head_end_seq is not None
                and max(message.source_event_seq or 0 for message in group) <= plan.head_end_seq
            ),
            default=0,
        )
        or None
    )
    prefix = tuple(message for message in plan.prefix if not is_summary_message(message))
    return replace(
        plan,
        prefix=(*prefix, summary),
        older=kept_older,
        covered_seq=max(plan.covered_seq, plan.material_end_seq),
        material_end_seq=0,
        head_end_seq=retained_head,
    )


def render_context(
    plan: ContextPlan,
    *,
    summary: ModelMessage | None = None,
) -> tuple[ModelMessage, ...]:
    """Render prefix + (summary | older) + pinned + working, preserving group boundaries."""
    rendered: list[ModelMessage] = [*plan.prefix]
    if summary is not None:
        rendered.append(summary)
    else:
        rendered.extend(message for group in plan.older for message in group)
    rendered.extend(message for group in plan.pinned for message in group)
    rendered.extend(message for group in plan.working for message in group)
    return tuple(rendered)


def _current_group_index(
    groups: Sequence[Group], run_input: RunInput | None
) -> int | None:
    if run_input is None:
        return None
    for index, group in enumerate(groups):
        if any(message.message_id == run_input.message_id for message in group):
            return index
    return None


def _pinned_indexes(
    groups: Sequence[Group], active_skills: Sequence[str], current_index: int | None
) -> set[int]:
    indexes: set[int] = set()
    if current_index is not None:
        indexes.add(current_index)
    latest: dict[str, int] = {}
    for index, group in enumerate(groups):
        for message in group:
            for call in message.tool_calls:
                if call.name == "load_skill":
                    key = call.arguments.get("skill_key")
                    if isinstance(key, str) and key in active_skills:
                        latest[key] = index
    indexes.update(latest.values())
    return indexes


def _pinned_order(index: int, current_index: int | None) -> int:
    if current_index is not None and index == current_index:
        return -1
    return index


def _flatten(groups: Sequence[Group]) -> tuple[ModelMessage, ...]:
    return tuple(message for group in groups for message in group)


def _group_skill_keys(group: Group) -> list[str]:
    keys: list[str] = []
    for message in group:
        for call in message.tool_calls:
            if call.name == "load_skill":
                key = call.arguments.get("skill_key")
                if isinstance(key, str):
                    keys.append(key)
    return keys


def _material_end_seq(
    groups: Sequence[Group],
    *,
    covered_seq: int,
    material_scan_end_seq: int | None,
) -> int:
    if material_scan_end_seq is None:
        return 0
    seqs = [
        message.source_event_seq or 0
        for group in groups
        for message in group
    ]
    return max(
        (seq for seq in seqs if covered_seq < seq <= material_scan_end_seq),
        default=0,
    )


__all__ = [
    "ContextPlan",
    "MessageOrigin",
    "RunInput",
    "material_groups",
    "pinned_event_ids",
    "plan_context",
    "render_context",
    "with_summary",
]
