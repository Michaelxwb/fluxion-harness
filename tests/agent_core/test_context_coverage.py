"""Material interval and summary replacement coverage."""

from __future__ import annotations

from muad_agent_core.context.plan import (
    ContextPlan,
    RunInput,
    material_groups,
    plan_context,
    with_summary,
)
from muad_agent_core.model import ModelMessage, ModelRole


def test_material_groups_are_a_contiguous_seq_interval() -> None:
    older = tuple(
        (ModelMessage(role=ModelRole.USER, content=f"m-{index}", source_event_seq=index),)
        for index in range(1, 6)
    )
    # plan_context computes material_end_seq from the actual older groups.
    plan = plan_context(
        tuple(message for group in older for message in group),
        run_input=None,
        covered_seq=1,
        material_scan_end_seq=4,
    )

    assert [message.content for group in material_groups(plan) for message in group] == [
        "m-2",
        "m-3",
        "m-4",
    ]
    assert plan.material_end_seq == 4


def test_summary_replacement_removes_only_material_and_keeps_tail() -> None:
    plan = ContextPlan(
        prefix=(ModelMessage(role=ModelRole.SYSTEM, content="system"),),
        older=(
            (ModelMessage(role=ModelRole.USER, content="material", source_event_seq=1),),
            (ModelMessage(role=ModelRole.USER, content="tail", source_event_seq=5),),
        ),
        pinned=((ModelMessage(role=ModelRole.USER, content="current"),),),
        working=(),
        covered_seq=0,
        material_end_seq=1,
        head_end_seq=1,
    )

    updated = with_summary(plan, ModelMessage(role=ModelRole.SYSTEM, content="[历史摘要] new"))

    assert [message.content for group in updated.older for message in group] == ["tail"]
    assert updated.covered_seq == 1
    assert updated.material_end_seq == 0
    assert updated.pinned == plan.pinned


def test_current_input_is_never_part_of_material() -> None:
    current = ModelMessage(
        role=ModelRole.USER,
        content="current",
        message_id="current",
        source_event_seq=9,
    )
    plan = plan_context(
        (
            ModelMessage(role=ModelRole.USER, content="old", source_event_seq=1),
            current,
        ),
        run_input=RunInput(message_id="current", content="current"),
        covered_seq=0,
        material_scan_end_seq=20,
    )

    assert plan.material_end_seq == 1
    assert current in [message for group in plan.pinned for message in group]
    assert "current" not in [
        message.content for group in material_groups(plan) for message in group
    ]
