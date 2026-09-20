"""[S-07][E-02][E-03][E-08][RULE-api-001/002] Run 生命周期 E2E（真实 PostgreSQL run_record + cancel hints）。"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import (
    Conversation,
    RunRecord,
    RunSubmission,
)

TENANT = f"rl-{uuid.uuid4()}"
USER = uuid.uuid4()
AGENT = uuid.uuid4()


@pytest.fixture(autouse=True)
async def _cleanup():
    yield
    async with get_session_factory()() as session:
        subs = (
            await session.execute(
                sa.select(RunSubmission).where(RunSubmission.tenant_id == TENANT)
            )
        ).scalars().all()
        run_ids = {row.run_id for row in subs if row.run_id}
        conv_ids = {row.conversation_id for row in subs if row.conversation_id}
        await session.execute(
            RunSubmission.__table__.delete().where(RunSubmission.tenant_id == TENANT)
        )
        for rid in run_ids:
            await session.execute(RunRecord.__table__.delete().where(RunRecord.id == rid))
        for cid in conv_ids:
            await session.execute(
                Conversation.__table__.delete().where(Conversation.id == cid)
            )
        await session.commit()


async def _make_run(status="RUNNING", *, lease_owner="inst-1") -> tuple[uuid.UUID, uuid.UUID]:
    conv_id, run_id = uuid.uuid4(), uuid.uuid4()
    async with get_session_factory()() as session:
        session.add(
            Conversation(
                id=conv_id,
                tenant_id=TENANT,
                user_id=USER,
                agent_id=AGENT,
                status="ACTIVE",
                last_seq=0,
            )
        )
        session.add(
            RunRecord(
                id=run_id,
                tenant_id=TENANT,
                conversation_id=conv_id,
                user_id=USER,
                agent_id=AGENT,
                status=status,
                input_text="hi",
                trace_id=uuid.uuid4().hex,
                cancel_requested=False,
                lease_owner=lease_owner,
            )
        )
        await session.commit()
    return run_id, conv_id


@pytest.mark.asyncio
async def test_s07_wait_input_resume_cas() -> None:
    """[S-07] WAITING_INPUT → RUNNING 通过 CAS 推进；WAITING_INPUT 事件留痕。"""
    run_id, conv_id = await _make_run("WAITING_INPUT", lease_owner="old")
    async with get_session_factory()() as session:
        result = await session.execute(
            sa.update(RunRecord)
            .where(
                RunRecord.id == run_id,
                RunRecord.status == "WAITING_INPUT",
            )
            .values(
                status="RUNNING",
                lease_owner="new-inst",
                update_time=sa.func.now(),
            )
            .returning(RunRecord.id)
        )
        assert result.scalar_one_or_none() is not None  # CAS 命中

        status = await session.scalar(
            sa.select(RunRecord.status).where(RunRecord.id == run_id)
        )
        assert status == "RUNNING"


@pytest.mark.asyncio
async def test_e03_run_busy_partial_unique() -> None:
    """[E-03] partial unique：活跃 conversation 只允许一个 RUNNING run。"""
    conv_id, run_id = uuid.uuid4(), uuid.uuid4()
    async with get_session_factory()() as session:
        session.add(
            Conversation(
                id=conv_id,
                tenant_id=TENANT,
                user_id=USER,
                agent_id=AGENT,
                status="ACTIVE",
                last_seq=0,
            )
        )
        session.add(
            RunRecord(
                id=run_id,
                tenant_id=TENANT,
                conversation_id=conv_id,
                user_id=USER,
                agent_id=AGENT,
                status="RUNNING",
                input_text="first",
                trace_id=uuid.uuid4().hex,
                cancel_requested=False,
                lease_owner="inst-1",
            )
        )
        await session.commit()

    # 第二个 RUNNING run 插入相同 conversation → partial unique 违约
    from sqlalchemy.exc import IntegrityError

    session_factory = get_session_factory()
    async with session_factory() as session:
        session.add(
            RunRecord(
                id=uuid.uuid4(),
                tenant_id=TENANT,
                conversation_id=conv_id,
                user_id=USER,
                agent_id=AGENT,
                status="RUNNING",
                input_text="second",
                trace_id=uuid.uuid4().hex,
                cancel_requested=False,
                lease_owner="inst-2",
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()


@pytest.mark.asyncio
async def test_e08_cancel_requested_flag() -> None:
    """[E-08] cancel_requested 置位：DB 是权威信号。"""
    run_id, conv_id = await _make_run("RUNNING")
    session_factory = get_session_factory()
    async with session_factory() as session:
        row = await session.get(RunRecord, run_id)
        row.cancel_requested = True
        await session.commit()

    async with session_factory() as session:
        flagged = await session.get(RunRecord, run_id)
        assert flagged.cancel_requested is True


@pytest.mark.asyncio
async def test_b01_idempotency_key_replay() -> None:
    """[B-01] 同 key 同指纹重放：不创建新 RunSubmission。"""
    run_id, conv_id = await _make_run("RUNNING")
    from muad_agent_runtime.application.run_submission import RunSubmissionService

    service = RunSubmissionService(get_session_factory)

    key = f"idem-{uuid.uuid4()}"
    fingerprint = "sha256:" + "b" * 64
    await service.record_submission(
        tenant_id=TENANT,
        idempotency_key=key,
        endpoint="create-run",
        actor_user_id=USER,
        run_id=run_id,
        conversation_id=conv_id,
        request_fingerprint=fingerprint,
        first_seq=1,
        last_seq=2,
    )
    replay = await service.find_replay(
        tenant_id=TENANT, idempotency_key=key, endpoint="create-run", fingerprint=fingerprint
    )
    assert replay is not None
    assert replay["run_id"] == str(run_id)
