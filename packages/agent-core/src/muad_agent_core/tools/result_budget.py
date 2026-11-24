"""Derived tool-result externalization budget shared by Runtime and Worker."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from muad_contracts.platform_settings import CompactionSettings, default_compaction_settings

#: 预览头/尾的代码常量上限；阈值配得再大也不会把预览撑大。
PREVIEW_CAP_BYTES = 8 * 1024
#: 单轮内联结果的预算占历史预算的比例（不暴露为设置）。
ROUND_RESULT_SHARE = 0.5
#: 预览头/尾各占阈值的比例。
PREVIEW_SHARE = 4


@dataclass(frozen=True, slots=True)
class ToolResultBudget:
    persist_threshold_bytes: int
    round_budget_bytes: int
    preview_head_bytes: int
    preview_tail_bytes: int


@dataclass(frozen=True, slots=True)
class RoundCandidate:
    tool_call_id: str
    size_bytes: int


def derive_tool_result_budget(compaction: CompactionSettings | None = None) -> ToolResultBudget:
    """从冻结压缩配置派生整轮预算与预览长度；不新增设置叶子。"""
    settings = compaction or default_compaction_settings()
    threshold = settings.tool_result.persist_threshold_bytes
    preview_base = min(max(threshold, 0), PREVIEW_CAP_BYTES)
    preview_bytes = preview_base // PREVIEW_SHARE
    return ToolResultBudget(
        persist_threshold_bytes=threshold,
        round_budget_bytes=max(0, int(settings.history_budget_tokens * ROUND_RESULT_SHARE)),
        preview_head_bytes=preview_bytes,
        preview_tail_bytes=preview_bytes,
    )


def select_round_persists(
    candidates: Sequence[RoundCandidate],
    *,
    persist_threshold_bytes: int,
    round_budget_bytes: int,
) -> tuple[str, ...]:
    """超过单条阈值必外置；剩余合计超过整轮预算时按大小从大到小补齐。"""
    over = [item for item in candidates if item.size_bytes > persist_threshold_bytes]
    rest = [item for item in candidates if item.size_bytes <= persist_threshold_bytes]
    remaining = sum(item.size_bytes for item in rest)
    selected = [item.tool_call_id for item in over]
    for item in sorted(rest, key=lambda entry: (-entry.size_bytes, entry.tool_call_id)):
        if remaining <= round_budget_bytes:
            break
        selected.append(item.tool_call_id)
        remaining -= item.size_bytes
    return tuple(selected)


__all__ = [
    "PREVIEW_CAP_BYTES",
    "PREVIEW_SHARE",
    "ROUND_RESULT_SHARE",
    "RoundCandidate",
    "ToolResultBudget",
    "derive_tool_result_budget",
    "select_round_persists",
]
