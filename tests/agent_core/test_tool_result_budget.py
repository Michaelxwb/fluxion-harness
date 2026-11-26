"""TASK-004: derived tool-result round budget and preview bounds."""

from __future__ import annotations

from muad_agent_core.tools.result_budget import (
    PREVIEW_CAP_BYTES,
    RoundCandidate,
    derive_tool_result_budget,
    select_round_persists,
)
from muad_contracts.platform_settings import CompactionSettings, ToolResultSettings


def test_budget_is_derived_from_history_budget_without_new_settings() -> None:
    budget = derive_tool_result_budget(
        CompactionSettings(
            history_budget_tokens=20_000,
            tool_result=ToolResultSettings(persist_threshold_bytes=4_000),
        )
    )
    assert budget.persist_threshold_bytes == 4_000
    assert budget.round_budget_bytes == 10_000
    assert budget.preview_head_bytes == 1_000
    assert budget.preview_tail_bytes == 1_000


def test_preview_is_capped_and_zero_threshold_has_no_preview() -> None:
    huge = derive_tool_result_budget(
        CompactionSettings(
            history_budget_tokens=1_000,
            tool_result=ToolResultSettings(persist_threshold_bytes=PREVIEW_CAP_BYTES * 8),
        )
    )
    assert huge.preview_head_bytes == PREVIEW_CAP_BYTES // 4
    zero = derive_tool_result_budget(
        CompactionSettings(
            history_budget_tokens=1_000,
            tool_result=ToolResultSettings(persist_threshold_bytes=0),
        )
    )
    assert (zero.preview_head_bytes, zero.preview_tail_bytes) == (0, 0)


def test_largest_results_are_externalized_until_the_round_budget_is_met() -> None:
    selected = select_round_persists(
        [
            RoundCandidate("a", 5_000),
            RoundCandidate("b", 4_000),
            RoundCandidate("c", 3_000),
        ],
        persist_threshold_bytes=10_000,
        round_budget_bytes=8_000,
    )
    assert selected == ("a",)
