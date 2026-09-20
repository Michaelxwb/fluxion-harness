"""[B-102] EventWriter→PostgreSQL：并发不重号/回滚无残留/顺序稳定/submission 归属。"""

from __future__ import annotations

import asyncio
import uuid

import pytest
import sqlalchemy as sa
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import (
    CanonicalEvent,
    Conversation,
    RunSubmission,
)


@pytest.fixture()
async def conversation():
    tenant = f"ev-{uuid.uuid4()}"
    conv_id = uuid.uuid4()
    async with get_session_factory()() as session:
        session.add(
            Conversation(
                id=conv_id,
                tenant_id=tenant,
                user_id=uuid.uuid4(),
                agent_id=uuid.uuid4(),
                status="ACTIVE",
                last_seq=0,
            )
        )
        await session.commit()
    yield {"tenant_id": tenant, "conversation_id": conv_id}
    async with get_session_factory()() as session:
        await session.execute(
            CanonicalEvent.__table__.delete().where(
                CanonicalEvent.conversation_id == conv_id
            )
        )
        await session.execute(
            Conversation.__table__.delete().where(Conversation.id == conv_id)
        )
        await session.commit()
    # conversation 清理在 submission 之前（FK：canonical_event.submission_id）



async def _write(
    conv: dict, run_id: uuid.UUID, event_type: str, *, submission_id=None
) -> int:
    from muad_agent_runtime.application.run_events import EventWriter

    async with get_session_factory()() as session:
        writer = EventWriter(session)
        seq = await writer.append(
            tenant_id=conv["tenant_id"],
            conversation_id=conv["conversation_id"],
            run_id=run_id,
            event_type=event_type,
            payload={"n": 1},
            submission_id=submission_id,
        )
        await session.commit()
    return seq


async def test_b102_concurrent_appends_no_duplicate_seq(conversation) -> None:
    """[B-102] 并发 append 不重号：conversation.last_seq 行锁单调分配。"""
    run_id = uuid.uuid4()
    seqs = await asyncio.gather(
        *[_write(conversation, run_id, f"EVENT_{i}") for i in range(8)]
    )
    assert sorted(seqs) == list(range(1, 9))

    async with get_session_factory()() as session:
        rows = (
            await session.execute(
                CanonicalEvent.__table__.select()
                .where(CanonicalEvent.conversation_id == conversation["conversation_id"])
                .order_by(CanonicalEvent.seq)
            )
        ).all()
        assert [row.seq for row in rows] == list(range(1, 9))  # 历史顺序稳定
        last_seq = await session.scalar(
            sa.select(Conversation.last_seq).where(
                Conversation.id == conversation["conversation_id"]
            )
        )
        assert last_seq == 8


async def test_b102_rollback_leaves_no_event(conversation) -> None:
    """[B-102] 回滚不留下事件且 last_seq 不推进。"""
    from muad_agent_runtime.application.run_events import EventWriter

    run_id = uuid.uuid4()
    async with get_session_factory()() as session:
        writer = EventWriter(session)
        await writer.append(
            tenant_id=conversation["tenant_id"],
            conversation_id=conversation["conversation_id"],
            run_id=run_id,
            event_type="USER_MESSAGE",
            payload={},
        )
        await session.rollback()

    async with get_session_factory()() as session:
        count = await session.scalar(
            sa.select(sa.func.count()).select_from(CanonicalEvent).where(
                CanonicalEvent.conversation_id == conversation["conversation_id"]
            )
        )
        last_seq = await session.scalar(
            sa.select(Conversation.last_seq).where(
                Conversation.id == conversation["conversation_id"]
            )
        )
    assert count == 0
    assert last_seq == 0


async def test_b102_seq_continues_across_submissions(conversation) -> None:
    """[B-102] 跨 Run/resume（不同 submission）序号保持单调。"""
    run_one = uuid.uuid4()
    run_two = uuid.uuid4()

    # submission FK：先落 run_submission 行并取真实 id
    async with get_session_factory()() as session:
        row_one = RunSubmission(
            tenant_id=conversation["tenant_id"],
            idempotency_key=f"k-{uuid.uuid4()}",
            endpoint="create-run",
            request_fingerprint="sha256:" + "2" * 64,
            actor_user_id=uuid.uuid4(),
            status="CLOSED",
        )
        row_two = RunSubmission(
            tenant_id=conversation["tenant_id"],
            idempotency_key=f"k-{uuid.uuid4()}",
            endpoint="resume-run",
            request_fingerprint="sha256:" + "3" * 64,
            actor_user_id=uuid.uuid4(),
            status="CLOSED",
        )
        session.add_all([row_one, row_two])
        await session.commit()
        submission_one, submission_two = row_one.id, row_two.id

    seq1 = await _write(conversation, run_one, "USER_MESSAGE", submission_id=submission_one)
    seq2 = await _write(conversation, run_one, "ASSISTANT_MESSAGE", submission_id=submission_one)
    seq3 = await _write(conversation, run_two, "USER_MESSAGE", submission_id=submission_two)

    assert (seq1, seq2, seq3) == (1, 2, 3)

    async with get_session_factory()() as session:
        events = (
            await session.execute(
                CanonicalEvent.__table__.select()
                .where(CanonicalEvent.submission_id == submission_one)
                .order_by(CanonicalEvent.seq)
            )
        ).all()
        assert {row.submission_id for row in events} == {submission_one}
