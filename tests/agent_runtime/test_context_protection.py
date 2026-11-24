"""Current Run input and loaded Skill instructions survive history fitting."""

from __future__ import annotations

import uuid

import pytest
from muad_agent_core.context.compactor import fit_history
from muad_agent_core.context.plan import RunInput, plan_context, render_context
from muad_agent_core.model import ModelMessage, ModelRole
from muad_agent_runtime.application.context_builder import (
    ActiveSkillContext,
    DbBackedContextBuilder,
    _append_confirmation,
)
from muad_agent_runtime.application.run_events import EventWriter
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import Conversation

from agent_runtime.conftest import TenantContext


def test_no_active_skill_context_preserves_history_order() -> None:
    history = (
        ModelMessage(ModelRole.USER, "earlier input", source_event_seq=1),
        ModelMessage(ModelRole.USER, "confirmation note"),
        ModelMessage(ModelRole.USER, "current input", source_event_seq=3),
    )
    assert ActiveSkillContext().merge(history) == history


def test_confirmation_merge_preserves_current_input_identity() -> None:
    original = ModelMessage(
        ModelRole.USER,
        "current goal",
        message_id="input-1",
        origin="USER_INPUT",
        source_event_id="event-10",
        source_event_seq=10,
    )
    entries = [(0, original)]
    _append_confirmation(entries, "[confirmed]", "USER_MESSAGE")
    merged = entries[0][1]
    assert merged.message_id == original.message_id
    assert merged.source_event_id == original.source_event_id
    assert merged.source_event_seq == original.source_event_seq
    assert merged.origin == original.origin
    plan = plan_context((merged,), run_input=RunInput("input-1", "current goal"))
    assert not plan.older
    assert merged in render_context(plan)


async def _seed_skill_history(tenant: TenantContext) -> tuple[uuid.UUID, uuid.UUID]:
    conversation = Conversation(
        id=uuid.uuid4(),
        tenant_id=tenant.tenant_id,
        user_id=tenant.platform_user_id,
        agent_id=tenant.agent_id,
        last_seq=0,
    )
    run_id = uuid.uuid4()
    async with get_session_factory()() as session:
        session.add(conversation)
        await session.flush()
        writer = EventWriter(session)

        async def append(kind: str, payload: dict[str, object]) -> None:
            await writer.append(
                tenant_id=tenant.tenant_id,
                conversation_id=conversation.id,
                run_id=run_id,
                event_type=kind,
                payload=payload,
            )

        await append("USER_MESSAGE", {"text": "goal", "message_id": "goal"})
        for index in range(8):
            tool = "load_skill" if index < 2 else "lookup"
            await append(
                "ASSISTANT_TURN",
                {
                    "tool_calls": [
                        {
                            "id": f"call-{index}",
                            "name": tool,
                            "registry_name": tool,
                            "arguments": {"skill_key": "weather" if index == 0 else "denied"},
                        }
                    ]
                },
            )
            await append(
                "TOOL_CALL",
                {
                    "state": "COMPLETED",
                    "tool_call_id": f"call-{index}",
                    "tool_name": tool,
                    "status": "ERROR" if index == 1 else "OK",
                    "content": "required instructions" if index == 0 else "result",
                    "preview": "bounded preview",
                },
            )
        await session.commit()
    return conversation.id, run_id


@pytest.mark.asyncio
async def test_restored_skill_instructions_survive_request_fit(
    tenant: TenantContext,
) -> None:
    conversation_id, run_id = await _seed_skill_history(tenant)
    builder = DbBackedContextBuilder(session_factory=get_session_factory)
    history = await builder.load_history(
        tenant_id=tenant.tenant_id,
        conversation_id=conversation_id,
        user_id=tenant.platform_user_id,
        budget_tokens=100_000,
    )
    context = await builder.load_skill_context(
        tenant_id=tenant.tenant_id,
        conversation_id=conversation_id,
        run_id=run_id,
    )
    assert context.keys == ("weather",), "failed loads do not activate Skill instructions"
    merged = context.merge(history.messages)
    assert context.merge(merged) == merged, "restored groups must not duplicate existing rounds"
    plan = plan_context(merged, run_input=RunInput("goal", "goal"), active_skills=context.keys)
    compacted, _ = fit_history(plan, budget_tokens=1)
    assert any(
        "required instructions" in str(message.content)
        for message in render_context(compacted)
    )
    assert len(plan.pinned) == 2
    for tenant_id, target_run in (("unrelated-tenant", run_id), (tenant.tenant_id, uuid.uuid4())):
        other = await builder.load_skill_context(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            run_id=target_run,
        )
        assert other.keys == () and other.groups == ()
