"""Summary coverage uses the older boundary, never the conversation's max seq."""

from __future__ import annotations

from muad_agent_core.agent.recovery import active_skills, fold_events
from muad_agent_core.context.compactor import compact_plan
from muad_agent_core.context.plan import RunInput, plan_context
from muad_agent_core.model import ModelMessage, ModelRole, ModelToolCall
from muad_contracts.platform_settings import MicroSettings, SnipSettings


def test_coverage_includes_tool_group_max_seq() -> None:
    assistant = ModelMessage(
        role=ModelRole.ASSISTANT,
        content="",
        tool_calls=(ModelToolCall("c1", "search", {}),),
        source_event_seq=4,
    )
    result = ModelMessage(
        role=ModelRole.TOOL,
        content="done",
        tool_call_id="c1",
        source_event_seq=5,
    )
    current = ModelMessage(
        role=ModelRole.USER,
        content="next",
        message_id="current",
        source_event_seq=9,
    )
    plan = plan_context(
        (assistant, result, current),
        run_input=RunInput(message_id="current", content="next"),
    )

    assert plan.coverage_seq == 5
    assert len(plan.older) == 1
    assert plan.pinned[0][0].content == "next"


def test_pinned_event_ids_survive_coverage() -> None:
    load_assistant = ModelMessage(
        role=ModelRole.ASSISTANT,
        content="",
        tool_calls=(ModelToolCall("load-1", "load_skill", {"skill_key": "k"}),),
        source_event_seq=2,
    )
    load_result = ModelMessage(
        role=ModelRole.TOOL,
        content='{"instructions":"keep"}',
        tool_call_id="load-1",
        source_event_id="event-load",
        source_event_seq=3,
    )
    current = ModelMessage(
        role=ModelRole.USER,
        content="now",
        message_id="current",
        source_event_seq=8,
    )
    plan = plan_context(
        (load_assistant, load_result, current),
        run_input=RunInput(message_id="current", content="now"),
        active_skills=("k",),
    )

    assert "event-load" in plan.protected_event_ids
    assert plan.coverage_seq == 0


def test_older_budget_does_not_move_coverage() -> None:
    older = (
        ModelMessage(role=ModelRole.USER, content="old-1", source_event_seq=1),
        ModelMessage(role=ModelRole.ASSISTANT, content="old-answer", source_event_seq=2),
        ModelMessage(role=ModelRole.USER, content="old-2", source_event_seq=3),
    )
    current = ModelMessage(
        role=ModelRole.USER,
        content="now",
        message_id="current",
        source_event_seq=10,
    )
    plan = plan_context(
        (*older, current),
        run_input=RunInput(message_id="current", content="now"),
    )
    compacted, _ = compact_plan(
        plan,
        snip_settings=SnipSettings(enabled=False),
        micro_settings=MicroSettings(enabled=False),
        history_budget_messages=1,
    )

    assert compacted.coverage_seq == plan.coverage_seq == 3
    assert len(compacted.older) == 1


def test_active_skills_rebuilt_from_completed_load_skill_events() -> None:
    fold = fold_events(
        [
            {
                "event_type": "ASSISTANT_TURN",
                "payload": {
                    "text": "",
                    "tool_calls": [
                        {
                            "id": "load-1",
                            "name": "load_skill",
                            "registry_name": "load_skill",
                            "arguments": {"skill_key": "weather"},
                        }
                    ],
                },
            },
            {
                "event_type": "TOOL_CALL",
                "payload": {
                    "state": "STARTED",
                    "tool_call_id": "load-1",
                    "tool_name": "load_skill",
                    "name": "load_skill",
                    "args_hash": "sha256:x",
                    "source": "PLATFORM",
                    "retry_safety": "NON_RETRYABLE",
                },
            },
            {
                "event_type": "TOOL_CALL",
                "payload": {
                    "state": "COMPLETED",
                    "tool_call_id": "load-1",
                    "tool_name": "load_skill",
                    "status": "OK",
                    "content": '{"instructions":"x"}',
                },
            },
        ]
    )

    assert active_skills(fold) == ("weather",)
