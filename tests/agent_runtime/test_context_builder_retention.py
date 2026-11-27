"""TASK-002: read-only, token-bounded history assembly (real PostgreSQL)."""

from __future__ import annotations

import uuid

import pytest
from muad_agent_runtime.application.context_builder import DbBackedContextBuilder
from muad_agent_runtime.application.run_events import EventWriter
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import Conversation

from agent_runtime.conftest import TenantContext


async def _seed(
    tenant: TenantContext,
    *,
    rounds: int,
    current_message_id: str | None = None,
    unconsumed_run_id: uuid.UUID | None = None,
) -> tuple[uuid.UUID, int | None]:
    conversation = Conversation(
        id=uuid.uuid4(),
        tenant_id=tenant.tenant_id,
        user_id=tenant.platform_user_id,
        agent_id=tenant.agent_id,
        last_seq=0,
    )
    run_id = uuid.uuid4()
    current_seq: int | None = None
    async with get_session_factory()() as session:
        session.add(conversation)
        await session.flush()
        writer = EventWriter(session)
        for index in range(rounds):
            await writer.append(
                tenant_id=tenant.tenant_id,
                conversation_id=conversation.id,
                run_id=run_id,
                event_type="USER_MESSAGE",
                payload={"text": f"第 {index} 条用户目标", "message_id": f"m-{index}"},
            )
            await writer.append(
                tenant_id=tenant.tenant_id,
                conversation_id=conversation.id,
                run_id=run_id,
                event_type="ASSISTANT_MESSAGE",
                payload={"text": f"第 {index} 条回答"},
            )
        if current_message_id is not None:
            current_seq = await writer.append(
                tenant_id=tenant.tenant_id,
                conversation_id=conversation.id,
                run_id=run_id,
                event_type="USER_MESSAGE",
                payload={"text": "当前问题", "message_id": current_message_id},
            )
        if unconsumed_run_id is not None:
            await writer.append(
                tenant_id=tenant.tenant_id,
                conversation_id=conversation.id,
                run_id=unconsumed_run_id,
                event_type="TOOL_TASK_ACCEPTED",
                payload={"text": "未消费的外部结果"},
            )
        await session.commit()
    return conversation.id, current_seq


@pytest.mark.asyncio
async def test_retention_keeps_head_tail_and_excludes_current_input_from_material(
    tenant: TenantContext, database_guard: None
) -> None:
    current_message_id = "current-message"
    conversation_id, current_seq = await _seed(
        tenant, rounds=60, current_message_id=current_message_id
    )
    loaded = await DbBackedContextBuilder(session_factory=get_session_factory).load_history(
        tenant_id=tenant.tenant_id,
        conversation_id=conversation_id,
        user_id=None,
        budget_tokens=300,
        current_message_id=current_message_id,
    )

    contents = [str(message.content) for message in loaded.messages]
    assert contents[0] == "第 0 条用户目标", "头部锚点必须保留首个用户目标"
    assert contents[-1] == "当前问题", "当前输入仍在尾部"
    assert current_seq is not None
    assert loaded.material_scan_end_seq < current_seq, "当前输入不得进入材料区间"
    assert loaded.head_end_seq is not None
    assert loaded.earlier_records is not None and loaded.earlier_records > 0

    from muad_agent_core.context.compactor import fit_history
    from muad_agent_core.context.plan import RunInput, plan_context, render_context

    plan = plan_context(
        loaded.messages, run_input=RunInput(current_message_id, "当前问题"),
        head_end_seq=loaded.head_end_seq, earlier_records=loaded.earlier_records,
    )
    compacted, _ = fit_history(plan, budget_tokens=1)
    visible = [message.content for message in render_context(compacted)]
    assert visible.count("第 59 条用户目标") == 1
    assert visible.count("第 59 条回答") == 1
    assert visible[-1] == "当前问题"


@pytest.mark.asyncio
async def test_previous_exchange_projects_tool_results_within_budget(
    tenant: TenantContext, database_guard: None
) -> None:
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
        await writer.append(
            tenant_id=tenant.tenant_id,
            conversation_id=conversation.id,
            run_id=run_id,
            event_type="USER_MESSAGE",
            payload={"text": "查客户资产", "message_id": "previous"},
        )
        await writer.append(
            tenant_id=tenant.tenant_id,
            conversation_id=conversation.id,
            run_id=run_id,
            event_type="ASSISTANT_TURN",
            payload={
                "text": "",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "name": "execute",
                        "registry_name": "execute",
                        "arguments": {"target": "skill", "skill_key": "asset"},
                    }
                ],
            },
        )
        await writer.append(
            tenant_id=tenant.tenant_id,
            conversation_id=conversation.id,
            run_id=run_id,
            event_type="TOOL_CALL",
            payload={
                "state": "COMPLETED",
                "tool_call_id": "call-1",
                "tool_name": "execute",
                "status": "OK",
                "preview": "asset-ip-result",
            },
        )
        await writer.append(
            tenant_id=tenant.tenant_id,
            conversation_id=conversation.id,
            run_id=run_id,
            event_type="ASSISTANT_MESSAGE",
            payload={"text": "上一轮结果"},
        )
        await writer.append(
            tenant_id=tenant.tenant_id,
            conversation_id=conversation.id,
            run_id=run_id,
            event_type="USER_MESSAGE",
            payload={"text": "继续", "message_id": "current"},
        )
        await session.commit()

    loaded = await DbBackedContextBuilder(session_factory=get_session_factory).load_history(
        tenant_id=tenant.tenant_id,
        conversation_id=conversation.id,
        user_id=None,
        budget_tokens=10_000,
        current_message_id="current",
    )

    contents = [str(message.content) for message in loaded.messages]
    assert "asset-ip-result" in contents
    assert "上一轮结果" in contents


@pytest.mark.asyncio
async def test_full_history_is_loaded_without_trimming(
    tenant: TenantContext, database_guard: None
) -> None:
    conversation_id, _ = await _seed(tenant, rounds=6)
    loaded = await DbBackedContextBuilder(session_factory=get_session_factory).load_history(
        tenant_id=tenant.tenant_id,
        conversation_id=conversation_id,
        user_id=None,
        budget_tokens=100_000,
    )

    assert len(loaded.messages) == 12
    assert loaded.earlier_records == 0
    assert loaded.material_scan_end_seq == 12
    assert loaded.head_end_seq == 2


@pytest.mark.asyncio
async def test_unconsumed_run_events_do_not_enter_material_or_gap(
    tenant: TenantContext, database_guard: None
) -> None:
    unconsumed_run_id = uuid.uuid4()
    conversation_id, _ = await _seed(
        tenant, rounds=30, unconsumed_run_id=unconsumed_run_id
    )
    loaded = await DbBackedContextBuilder(session_factory=get_session_factory).load_history(
        tenant_id=tenant.tenant_id,
        conversation_id=conversation_id,
        user_id=None,
        budget_tokens=20_000,
        unconsumed_run_id=unconsumed_run_id,
        consumed_event_seq=0,
    )

    joined = "\n".join(str(message.content) for message in loaded.messages)
    assert "未消费的外部结果" not in joined
    assert all("External tool data" not in str(message.content) for message in loaded.messages)
