"""S-01: implicit learning signals from real conversation events (TASK-003)."""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from muad_agent_runtime.application.context_builder import HISTORY_EVENT_TYPES
from muad_agent_runtime.application.learning_signals import (
    NEGATION,
    REASK,
    REWRITE,
    classify_signal,
    detect_and_write_signal,
)
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import (
    CanonicalEvent,
    Conversation,
    RunRecord,
)
from muad_contracts import RunStatus


def test_s01_classifier_distinguishes_negation_reask_and_rewrite() -> None:
    previous = "帮我汇总这批客户的采购数据"
    assert classify_signal(previous, "好的，已汇总 10 个客户", "不对，改成表格") == NEGATION
    assert classify_signal(previous, "好的", previous) == REASK
    assert (
        classify_signal(previous, "好的", "帮我汇总这批客户的采购数据，按金额排序") == REWRITE
    )
    assert classify_signal(previous, "好的", "明天天气怎么样") is None


async def _seed_conversation(tenant: str) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    conversation_id = uuid.uuid4()
    previous_run, current_run = uuid.uuid4(), uuid.uuid4()
    async with get_session_factory()() as session:
        session.add(
            Conversation(
                id=conversation_id,
                tenant_id=tenant,
                user_id=uuid.uuid4(),
                agent_id=uuid.uuid4(),
            )
        )
        await session.flush()
        for run_id in (previous_run, current_run):
            session.add(
                RunRecord(
                    id=run_id,
                    tenant_id=tenant,
                    conversation_id=conversation_id,
                    user_id=uuid.uuid4(),
                    agent_id=uuid.uuid4(),
                    status=RunStatus.COMPLETED,
                    input_text="seed",
                    trace_id=f"trace-{run_id}",
                )
            )
        await session.flush()
        events = [
            (previous_run, "USER_MESSAGE", {"text": "帮我汇总这批客户的采购数据"}),
            (previous_run, "ASSISTANT_MESSAGE", {"text": "已汇总 10 个客户"}),
            (current_run, "USER_MESSAGE", {"text": "不对，改成表格"}),
        ]
        for seq, (run_id, event_type, payload) in enumerate(events, start=1):
            session.add(
                CanonicalEvent(
                    tenant_id=tenant,
                    conversation_id=conversation_id,
                    run_id=run_id,
                    seq=seq,
                    event_type=event_type,
                    payload_json=payload,
                )
            )
        await session.execute(
            sa.update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(last_seq=len(events))
        )
        await session.commit()
    return conversation_id, previous_run, current_run


async def _cleanup(tenant: str) -> None:
    async with get_session_factory()() as session:
        await session.execute(sa.delete(CanonicalEvent).where(CanonicalEvent.tenant_id == tenant))
        await session.execute(sa.delete(RunRecord).where(RunRecord.tenant_id == tenant))
        await session.execute(sa.delete(Conversation).where(Conversation.tenant_id == tenant))
        await session.commit()


async def test_s01_signal_is_written_without_changing_run_state(database_guard: None) -> None:
    tenant = f"signal-{uuid.uuid4()}"
    conversation_id, previous_run, current_run = await _seed_conversation(tenant)
    try:
        signal = await detect_and_write_signal(
            get_session_factory(),
            tenant_id=tenant,
            conversation_id=conversation_id,
            run_id=current_run,
        )
        assert signal == NEGATION

        async with get_session_factory()() as session:
            rows = (
                await session.scalars(
                    sa.select(CanonicalEvent).where(
                        CanonicalEvent.tenant_id == tenant,
                        CanonicalEvent.event_type == "LEARNING_SIGNAL",
                    )
                )
            ).all()
            assert len(rows) == 1
            event = rows[0]
            assert event.run_id == current_run
            assert event.payload_json["source_run_id"] == str(previous_run)
            assert event.payload_json["signal_type"] == NEGATION
            assert event.payload_json["tenant_id"] == tenant
            assert event.payload_json["user_id"]
            assert "LEARNING_SIGNAL" not in HISTORY_EVENT_TYPES

            statuses = (
                await session.scalars(
                    sa.select(RunRecord.status).where(RunRecord.tenant_id == tenant)
                )
            ).all()
            assert statuses == [RunStatus.COMPLETED, RunStatus.COMPLETED]

        # 同一 Run 至多一条信号。
        again = await detect_and_write_signal(
            get_session_factory(),
            tenant_id=tenant,
            conversation_id=conversation_id,
            run_id=current_run,
        )
        assert again is None
    finally:
        await _cleanup(tenant)
