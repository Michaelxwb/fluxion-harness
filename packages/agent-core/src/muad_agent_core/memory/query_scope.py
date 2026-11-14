"""Scope inheritance from real user messages; taxonomy aliases only, no NER."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from muad_contracts.memory_taxonomy import SCOPE_DIMENSIONS

#: 出现这些词就丢弃更早消息里的适用条件（明确换任务）。
TASK_SWITCH_MARKERS = ("换任务", "换个任务", "新任务", "新的任务", "从头开始", "重新开始")
#: 只看最近这么多条用户消息（工具结果与记忆正文由调用方排除）。
RECENT_LIMIT = 6


@dataclass(frozen=True, slots=True)
class QueryScope:
    state: str
    scope: dict[str, str] = field(default_factory=dict)
    ambiguous: bool = False


def _codes(text: str, dimension: str) -> list[str]:
    folded = text.casefold()
    return [
        code
        for code, aliases in SCOPE_DIMENSIONS[dimension].items()
        if any(alias.casefold() in folded for alias in aliases)
    ]


def scope_from_messages(messages: Sequence[str]) -> QueryScope:
    """Newest wins per dimension; a task switch drops everything older."""

    scope: dict[str, str] = {}
    switched = False
    for text in reversed(tuple(messages)[-RECENT_LIMIT:]):
        if not text:
            continue
        if any(marker in text for marker in TASK_SWITCH_MARKERS):
            switched = True
            continue
        if switched:
            continue
        for dimension in SCOPE_DIMENSIONS:
            if dimension in scope:
                continue
            codes = _codes(text, dimension)
            if len(codes) > 1:
                # 同一维度出现两个不同显式值：无法比较，按未知处理并请求澄清。
                return QueryScope("UNKNOWN", {}, ambiguous=True)
            if codes:
                scope[dimension] = codes[0]
    return QueryScope("EXPLICIT" if scope else "ABSENT", scope)
