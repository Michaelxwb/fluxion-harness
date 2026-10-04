"""[E-03] 压缩审计事件的落库形状（FEAT-05）。

真实边界：真实 PostgreSQL 的 `runtime.canonical_event`，事件经既有 `EventWriter`（
`conversation.last_seq` 行锁）追加。
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from muad_agent_runtime.application.context_builder import DbBackedContextBuilder
from muad_agent_runtime.application.context_events import (
    CONTEXT_COMPACTED_EVENT,
    latest_summary,
    record_compaction,
    record_summary,
)
from muad_agent_runtime.application.run_events import EventWriter
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import CanonicalEvent, Conversation

from agent_runtime.conftest import TenantContext


async def _conversation(tenant: TenantContext) -> Conversation:
    row = Conversation(
        id=uuid.uuid4(),
        tenant_id=tenant.tenant_id,
        user_id=tenant.platform_user_id,
        agent_id=tenant.agent_id,
        last_seq=0,
    )
    async with get_session_factory()() as session:
        session.add(row)
        await session.commit()
    return row


async def _events(tenant: TenantContext, conversation_id: uuid.UUID) -> list[CanonicalEvent]:
    async with get_session_factory()() as session:
        rows = await session.execute(
            sa.select(CanonicalEvent)
            .where(
                CanonicalEvent.tenant_id == tenant.tenant_id,
                CanonicalEvent.conversation_id == conversation_id,
            )
            .order_by(CanonicalEvent.seq)
        )
        return list(rows.scalars().all())


async def test_e03_compaction_writes_exactly_one_audit_row(
    tenant: TenantContext, database_guard: None
) -> None:
    conversation = await _conversation(tenant)
    run_id = uuid.uuid4()
    layers = [
        {"layer": "snip", "fired": True, "groups": 4, "bytes_saved": 1234},
        {"layer": "micro", "fired": False, "groups": 0, "bytes_saved": 0},
    ]

    async with get_session_factory()() as session:
        seq = await record_compaction(
            EventWriter(session),
            tenant_id=tenant.tenant_id,
            conversation_id=conversation.id,
            run_id=run_id,
            layers=layers,
        )
        await session.commit()

    rows = await _events(tenant, conversation.id)
    assert len(rows) == 1, "压缩发生一次恰好多一行"
    assert rows[0].event_type == CONTEXT_COMPACTED_EVENT
    assert rows[0].seq == seq == 1, "seq 由 conversation 行锁分配"
    payload = rows[0].payload_json
    assert payload["layers"]["snip"] == {
        "layer": "snip",
        "fired": True,
        "groups": 4,
        "bytes_saved": 1234,
    }, "层级/省下字节/组数逐项落库"
    assert "summary_event_seq" not in payload, "没有摘要时不得凭空带上引用"


async def test_e03_summary_event_carries_the_authoritative_history(
    tenant: TenantContext, database_guard: None
) -> None:
    conversation = await _conversation(tenant)
    run_id = uuid.uuid4()
    summary = {
        "user_goal": "把硬丢换成分级压缩",
        "constraints": ["默认只开前两层"],
        "progress": ["落地 snip"],
        "open_items": ["摘要默认关"],
        "artifacts": [{"artifact_id": "a1", "tool": "read_attachment", "note": "原文"}],
    }

    async with get_session_factory()() as session:
        compacted_seq = await record_summary(
            EventWriter(session),
            tenant_id=tenant.tenant_id,
            conversation_id=conversation.id,
            run_id=run_id,
            covers_up_to_seq=7,
            summary=summary,
            transcript_artifact_id="t-1",
            bytes_before=90_000,
            bytes_after=1_200,
        )
        compaction_seq = await record_compaction(
            EventWriter(session),
            tenant_id=tenant.tenant_id,
            conversation_id=conversation.id,
            run_id=run_id,
            layers=[{"layer": "summary", "fired": True, "groups": 0, "bytes_saved": 88_800}],
            summary_event_seq=compacted_seq,
        )
        await session.commit()

    rows = await _events(tenant, conversation.id)
    assert [row.seq for row in rows] == [1, 2], "两次追加共用一个 seq 链"
    assert compaction_seq == 2, "压缩事件拿到的就是下一个 seq"
    assert rows[1].payload_json["summary_event_seq"] == 1, "压缩事件指回它的摘要事件"

    async with get_session_factory()() as session:
        latest = await latest_summary(session, tenant.tenant_id, conversation.id)
    assert latest is not None
    assert latest.seq == 1
    assert latest.payload_json["covers_up_to_seq"] == 7
    assert latest.payload_json["summary"] == summary
    assert latest.payload_json["transcript_artifact_id"] == "t-1"


async def test_e03_plain_history_load_writes_no_compaction_row(
    tenant: TenantContext, database_guard: None
) -> None:
    """没压缩就不落行：普通的历史装配不得凭空产出压缩审计事件。"""
    conversation = await _conversation(tenant)
    async with get_session_factory()() as session:
        await EventWriter(session).append(
            tenant_id=tenant.tenant_id,
            conversation_id=conversation.id,
            run_id=uuid.uuid4(),
            event_type="USER_MESSAGE",
            payload={"text": "你好"},
        )
        await session.commit()

    builder = DbBackedContextBuilder(session_factory=get_session_factory)
    messages = await builder.load_history(
        tenant_id=tenant.tenant_id, conversation_id=conversation.id, user_id=None
    )
    assert [message.content for message in messages] == ["你好"]

    kinds = [row.event_type for row in await _events(tenant, conversation.id)]
    assert kinds == ["USER_MESSAGE"], "历史装配是只读路径，不得写事件"
