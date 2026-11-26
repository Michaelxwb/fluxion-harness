"""Request-boundary fit_history invariants."""

from __future__ import annotations

from muad_agent_core.context.compactor import (
    OMISSION_MARKER_PREFIX,
    fit_history,
    history_bytes,
    split_groups,
)
from muad_agent_core.context.plan import ContextPlan
from muad_agent_core.model import ModelMessage, ModelRole, ModelToolCall


def _plan(*, older_count: int = 4, earlier: int | None = 0) -> ContextPlan:
    older = tuple(
        (
            ModelMessage(
                role=ModelRole.USER if index % 2 == 0 else ModelRole.ASSISTANT,
                content=f"old-{index}",
                source_event_id=f"old-{index}",
                source_event_seq=index + 1,
            ),
        )
        for index in range(older_count)
    )
    current = ModelMessage(
        role=ModelRole.USER,
        content="current",
        message_id="current",
        source_event_id="current",
        source_event_seq=99,
    )
    working = (
        (
            ModelMessage(
                role=ModelRole.ASSISTANT,
                content="",
                tool_calls=(ModelToolCall("call-1", "echo", {}),),
                source_event_seq=100,
            ),
            ModelMessage(
                role=ModelRole.TOOL,
                content="tool result",
                tool_call_id="call-1",
                source_event_seq=101,
            ),
        ),
    )
    return ContextPlan(
        prefix=(),
        older=older,
        pinned=((current,),),
        working=working,
        head_end_seq=2,
        earlier_records=earlier,
    )


def test_fit_protects_head_current_and_latest_round_and_marks_the_gap() -> None:
    plan = _plan()
    compacted, layer = fit_history(plan, budget_tokens=1)

    assert layer is not None and layer.fired
    contents = [message.content for group in compacted.older for message in group]
    assert "old-0" in contents and "old-1" in contents, "head anchor kept"
    assert any(
        OMISSION_MARKER_PREFIX in str(message.content)
        for group in compacted.older
        for message in group
    )
    assert compacted.pinned == plan.pinned
    assert compacted.working == plan.working, "latest complete tool round kept"
    assert layer.dropped_records >= 1
    assert layer.retained_from_seq is not None


def test_no_drop_and_no_gap_returns_the_original_plan() -> None:
    plan = _plan(older_count=2, earlier=0)
    compacted, layer = fit_history(plan, budget_tokens=100_000)

    assert compacted is plan
    assert layer is None
    assert not any(
        OMISSION_MARKER_PREFIX in str(message.content)
        for group in compacted.older
        for message in group
    )


def test_gap_without_loaded_drop_still_gets_a_marker_and_no_coverage() -> None:
    plan = _plan(older_count=2, earlier=5)
    compacted, layer = fit_history(plan, budget_tokens=100_000)

    assert layer is not None and layer.fired and layer.groups == 0
    assert compacted.covered_seq == plan.covered_seq
    assert compacted.material_end_seq == 0
    assert any(
        OMISSION_MARKER_PREFIX in str(message.content)
        for group in compacted.older
        for message in group
    )


def test_groups_never_leave_an_orphan_tool_message() -> None:
    plan = _plan()
    compacted, _ = fit_history(plan, budget_tokens=1)
    groups = split_groups(
        tuple(message for group in compacted.older for message in group)
        + tuple(message for group in compacted.working for message in group)
    )
    declared = {call.id for group in groups for message in group for call in message.tool_calls}
    assert all(
        message.tool_call_id in declared
        for group in groups
        for message in group
        if message.role is ModelRole.TOOL
    )
    assert history_bytes([message for group in compacted.older for message in group]) > 0
