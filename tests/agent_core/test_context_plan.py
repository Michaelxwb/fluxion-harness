"""ContextPlan: explicit current input, pinned skill instructions, older/working layers."""

from __future__ import annotations

from muad_agent_core.context.plan import (
    ContextPlan,
    MessageOrigin,
    RunInput,
    material_groups,
    pinned_event_ids,
    plan_context,
    render_context,
    with_summary,
)
from muad_agent_core.model import ModelMessage, ModelRole, ModelToolCall


def _load_skill_exchange(skill_key: str, *, seq: int = 2) -> tuple[ModelMessage, ...]:
    return (
        ModelMessage(
            role=ModelRole.ASSISTANT,
            content="",
            tool_calls=(ModelToolCall("load-1", "load_skill", {"skill_key": skill_key}),),
        ),
        ModelMessage(
            role=ModelRole.TOOL,
            content='{"manifest": {}, "instructions": "follow me"}',
            tool_call_id="load-1",
            source_event_id=f"event-{seq}",
            source_event_seq=seq,
        ),
    )


def test_current_run_input_is_not_replaced_by_older_user_history() -> None:
    old_user = ModelMessage(role=ModelRole.USER, content="old request", message_id="old")
    current = ModelMessage(role=ModelRole.USER, content="current request", message_id="current")
    plan = plan_context(
        (old_user, ModelMessage(role=ModelRole.ASSISTANT, content="old answer"), current),
        run_input=RunInput(message_id="current", content="current request"),
    )

    assert [message.content for group in plan.older for message in group] == [
        "old request",
        "old answer",
    ]
    assert [message.content for group in plan.pinned for message in group] == ["current request"]
    assert plan.working == ()


def test_active_skill_load_group_is_pinned_and_protected() -> None:
    load = _load_skill_exchange("weather", seq=7)
    current = ModelMessage(role=ModelRole.USER, content="now", message_id="current")
    plan = plan_context(
        (ModelMessage(role=ModelRole.USER, content="before"), *load, current),
        run_input=RunInput(message_id="current", content="now"),
        active_skills=("weather",),
    )

    pinned_text = [message.content for group in plan.pinned for message in group]
    assert '{"manifest": {}, "instructions": "follow me"}' in pinned_text
    assert "now" in pinned_text
    assert pinned_event_ids(plan) == ("event-7",)


def test_material_interval_excludes_current_input_and_comes_from_older_only() -> None:
    older = ModelMessage(role=ModelRole.USER, content="old", source_event_seq=3)
    current = ModelMessage(
        role=ModelRole.USER,
        content="current",
        message_id="current",
        source_event_seq=99,
    )
    plan = plan_context(
        (older, current),
        run_input=RunInput(message_id="current", content="current"),
        covered_seq=0,
        material_scan_end_seq=50,
        head_end_seq=3,
        earlier_records=4,
    )

    assert plan.material_end_seq == 3
    assert [message.content for group in material_groups(plan) for message in group] == ["old"]
    assert plan.head_end_seq == 3
    assert plan.earlier_records == 4


def test_pinned_budget_omits_skill_visibly() -> None:
    load = _load_skill_exchange("large", seq=2)
    current = ModelMessage(role=ModelRole.USER, content="now", message_id="current")
    plan = plan_context(
        (*load, current),
        run_input=RunInput(message_id="current", content="now"),
        active_skills=("large",),
        protected_budget_bytes=10,
    )

    assert "large" in plan.pinned_omitted
    assert all(
        message.content != '{"manifest": {}, "instructions": "follow me"}'
        for group in plan.pinned
        for message in group
    )


def test_render_context_keeps_prefix_older_pinned_working_order() -> None:
    prefix = (ModelMessage(role=ModelRole.SYSTEM, content="system"),)
    older = (ModelMessage(role=ModelRole.USER, content="old"),)
    pinned = (ModelMessage(role=ModelRole.USER, content="current"),)
    working = (ModelMessage(role=ModelRole.TOOL, content="result"),)

    rendered = render_context(
        ContextPlan(
            prefix=prefix,
            older=(older,),
            pinned=(pinned,),
            working=(working,),
        )
    )
    assert [message.content for message in rendered] == ["system", "old", "current", "result"]
    assert MessageOrigin.USER_INPUT.value == "USER_INPUT"


def test_with_summary_replaces_only_the_material_interval_and_keeps_tail() -> None:
    plan = ContextPlan(
        prefix=(
            ModelMessage(role=ModelRole.SYSTEM, content="system"),
            ModelMessage(role=ModelRole.SYSTEM, content="[历史摘要] old"),
        ),
        older=(
            (ModelMessage(role=ModelRole.USER, content="material", source_event_seq=1),),
            (ModelMessage(role=ModelRole.ASSISTANT, content="tail", source_event_seq=5),),
        ),
        pinned=((ModelMessage(role=ModelRole.USER, content="current"),),),
        working=(),
        covered_seq=0,
        material_end_seq=2,
        head_end_seq=1,
    )

    updated = with_summary(plan, ModelMessage(role=ModelRole.SYSTEM, content="[历史摘要] new"))

    assert [message.content for message in render_context(updated)] == [
        "system",
        "[历史摘要] new",
        "tail",
        "current",
    ]
    assert updated.covered_seq == 2
    assert updated.material_end_seq == 0
    assert updated.head_end_seq is None
