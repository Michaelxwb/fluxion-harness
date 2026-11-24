"""TASK-003: token budget fitting and omission marker."""

from __future__ import annotations

from muad_agent_core.context.compactor import (
    OMISSION_MARKER_PREFIX,
    estimate_message_tokens,
    fit_history,
)
from muad_agent_core.context.plan import ContextPlan
from muad_agent_core.model import ModelMessage, ModelRole


def _plan() -> ContextPlan:
    older = tuple(
        (
            ModelMessage(
                role=ModelRole.USER if index % 2 else ModelRole.ASSISTANT,
                content=f"old-{index} " + "x" * 80,
                source_event_seq=index,
            ),
        )
        for index in range(1, 7)
    )
    current = ModelMessage(
        role=ModelRole.USER,
        content="current question",
        message_id="current",
        source_event_seq=99,
    )
    return ContextPlan(
        prefix=(ModelMessage(role=ModelRole.SYSTEM, content="system prompt"),),
        older=older,
        pinned=((current,),),
        working=(),
        head_end_seq=2,
        earlier_records=0,
    )


def test_fit_keeps_head_and_current_and_marks_dropped_middle() -> None:
    plan = _plan()
    compacted, layer = fit_history(plan, budget_tokens=1)

    contents = [message.content for group in compacted.older for message in group]
    assert "old-1 " + "x" * 80 in contents
    assert "old-2 " + "x" * 80 in contents
    assert compacted.pinned == plan.pinned
    assert layer is not None and layer.fired
    assert layer.dropped_records > 0
    assert any(OMISSION_MARKER_PREFIX in str(message.content) for message in layer.messages)


def test_budget_includes_message_overhead() -> None:
    message = ModelMessage(role=ModelRole.USER, content="abc")
    assert estimate_message_tokens(message) > len("abc")


def test_no_marker_when_everything_fits() -> None:
    plan = _plan()
    compacted, layer = fit_history(plan, budget_tokens=100_000)
    assert compacted is plan
    assert layer is None
