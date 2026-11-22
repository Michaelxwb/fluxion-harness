"""ContextPlan: explicit current input, pinned skill instructions, older/working layers."""

from __future__ import annotations

from muad_agent_core.context.compactor import compact_plan, trim_older_history
from muad_agent_core.context.plan import (
    MessageOrigin,
    RunInput,
    plan_context,
    render_context,
    with_summary,
)
from muad_agent_core.model import ModelMessage, ModelRole, ModelToolCall
from muad_contracts.platform_settings import MicroSettings, SnipSettings


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

    pinned_text = [
        message.content for group in plan.pinned for message in group
    ]
    assert '{"manifest": {}, "instructions": "follow me"}' in pinned_text
    assert "now" in pinned_text
    assert plan.coverage_seq == 0, "older has no seq"
    assert plan.protected_event_ids == ("event-7",)


def test_coverage_seq_uses_older_only() -> None:
    older = ModelMessage(
        role=ModelRole.USER,
        content="old",
        source_event_seq=3,
    )
    current = ModelMessage(
        role=ModelRole.USER,
        content="current",
        message_id="current",
        source_event_seq=99,
    )
    plan = plan_context(
        (older, current),
        run_input=RunInput(message_id="current", content="current"),
    )

    assert plan.coverage_seq == 3
    assert plan.protected_event_ids == ()


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


def test_compaction_only_touches_working_and_older_budget() -> None:
    older = tuple(
        ModelMessage(role=ModelRole.USER, content=f"old-{index}") for index in range(4)
    )
    current = ModelMessage(role=ModelRole.USER, content="current", message_id="current")
    working_msgs: list[ModelMessage] = []
    for index in range(4):
        working_msgs.extend(
            (
                ModelMessage(
                    role=ModelRole.ASSISTANT,
                    content="",
                    tool_calls=(ModelToolCall(f"c-{index}", "echo", {}),),
                ),
                ModelMessage(
                    role=ModelRole.TOOL,
                    content="x" * 200,
                    tool_call_id=f"c-{index}",
                ),
            )
        )
    working = tuple(working_msgs)
    load = _load_skill_exchange("keep", seq=5)
    plan = plan_context(
        (older[0], older[1], older[2], older[3], *load, current, *working),
        run_input=RunInput(message_id="current", content="current"),
        active_skills=("keep",),
    )

    compacted, layers = compact_plan(
        plan,
        snip_settings=SnipSettings(enabled=True, max_groups=1, keep_head_groups=0, keep_tail_groups=1),
        micro_settings=MicroSettings(enabled=False),
        history_budget_messages=1,
    )

    assert compacted.prefix == plan.prefix
    assert compacted.pinned == plan.pinned
    assert len(compacted.older) == 1
    assert sum(layer.fired for layer in layers) >= 2


def test_render_context_keeps_prefix_older_pinned_working_order() -> None:
    prefix = (ModelMessage(role=ModelRole.SYSTEM, content="system"),)
    older = (ModelMessage(role=ModelRole.USER, content="old"),)
    pinned = (ModelMessage(role=ModelRole.USER, content="current"),)
    working = (ModelMessage(role=ModelRole.TOOL, content="result"),)
    from muad_agent_core.context.plan import ContextPlan

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


def test_with_summary_replaces_the_previous_summary_instead_of_appending_it() -> None:
    from muad_agent_core.context.plan import ContextPlan

    plan = ContextPlan(
        prefix=(
            ModelMessage(role=ModelRole.SYSTEM, content="system"),
            ModelMessage(role=ModelRole.SYSTEM, content="[历史摘要] old"),
        ),
        older=((ModelMessage(role=ModelRole.USER, content="old"),),),
        pinned=((ModelMessage(role=ModelRole.USER, content="current"),),),
        working=(),
    )

    updated = with_summary(plan, ModelMessage(role=ModelRole.SYSTEM, content="[历史摘要] new"))

    assert updated.older == ()
    assert [message.content for message in updated.prefix] == ["system", "[历史摘要] new"]
    assert [message.content for message in render_context(updated)] == [
        "system",
        "[历史摘要] new",
        "current",
    ]


def test_trim_older_history_is_only_the_fallback_layer() -> None:
    from muad_agent_core.context.plan import ContextPlan

    older = tuple(
        (ModelMessage(role=ModelRole.USER, content=f"old-{index}"),) for index in range(3)
    )
    plan = ContextPlan(prefix=(), older=older, pinned=(), working=())

    trimmed, layer = trim_older_history(plan, 1)

    assert len(trimmed.older) == 1
    assert layer is not None and layer.layer == "budget" and layer.fired is True


def test_current_input_is_found_only_by_stable_message_id() -> None:
    current = ModelMessage(
        role=ModelRole.USER, content="same text", message_id="run-input-1"
    )
    impostor = ModelMessage(role=ModelRole.USER, content="same text")

    matched = plan_context(
        (impostor, current),
        run_input=RunInput(message_id="run-input-1", content="same text"),
    )
    unmatched = plan_context(
        (impostor, current),
        run_input=RunInput(message_id="missing", content="same text"),
    )

    assert matched.pinned and matched.pinned[0] == (current,)
    assert not unmatched.pinned
    assert len(unmatched.older) == 2
