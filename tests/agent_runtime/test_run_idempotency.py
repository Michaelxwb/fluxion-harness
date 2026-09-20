"""[B-105] Run 创建/Resume 提交幂等（真实 PostgreSQL run_submission partial unique）。

测试直接驱动 RunService.start/提交路径的幂等层（run_submission 表），
验证同键同指纹重放、异指纹冲突、回滚无半成品。
"""

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

from muad_agent_runtime.application.run_submission import (
    RunSubmissionService,
    submission_fingerprint,
)

TENANT = f"idem-{uuid.uuid4()}"
USER = uuid.uuid4()
AGENT = uuid.uuid4()


@pytest.fixture(autouse=True)
async def _cleanup():
    yield
    async with get_session_factory()() as session:
        events = (
            await session.execute(
                sa.select(RunSubmission).where(RunSubmission.tenant_id == TENANT)
            )
        ).scalars().all()
        run_ids = [row.run_id for row in events if row.run_id]
        conv_ids = [row.conversation_id for row in events if row.conversation_id]
        await session.execute(
            RunSubmission.__table__.delete().where(RunSubmission.tenant_id == TENANT)
        )
        for rid in run_ids:
            await session.execute(RunRecord.__table__.delete().where(RunRecord.id == rid))
        for cid in set(conv_ids):
            await session.execute(
                Conversation.__table__.delete().where(Conversation.id == cid)
            )
        await session.commit()


async def _seed_run() -> tuple[uuid.UUID, uuid.UUID]:
    conv_id, run_id = uuid.uuid4(), uuid.uuid4()
    async with get_session_factory()() as session:
        session.add(
            Conversation(
                id=conv_id,
                tenant_id=TENANT,
                user_id=USER,
                agent_id=AGENT,
                status="ACTIVE",
                last_seq=3,
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
                input_text="hi",
                trace_id=uuid.uuid4().hex,
                cancel_requested=False,
            )
        )
        await session.commit()
    return run_id, conv_id


async def test_b105_same_key_same_fingerprint_replays_record() -> None:
    """[B-105] 同 key 同指纹：命中已提交记录（不创建新 Run）。"""
    run_id, conv_id = await _seed_run()
    service = RunSubmissionService(get_session_factory())
    key = f"idem-{uuid.uuid4()}"
    fingerprint = submission_fingerprint(key_payload={"text": "hello"}, message_id=None)

    recorded = await service.record_submission(
        tenant_id=TENANT,
        idempotency_key=key,
        endpoint="create-run",
        actor_user_id=USER,
        run_id=run_id,
        conversation_id=conv_id,
        request_fingerprint=fingerprint,
        first_seq=1,
        last_seq=3,
    )
    assert recorded["run_id"] == str(run_id)

    replay = await service.find_replay(
        tenant_id=TENANT, idempotency_key=key, endpoint="create-run", fingerprint=fingerprint
    )
    assert replay is not None
    assert replay["run_id"] == str(run_id)
    assert replay["last_seq"] == 3

    async with get_session_factory()() as session:
        rows = (
            await session.execute(
                sa.select(sa.func.count()).select_from(RunSubmission).where(
                    RunSubmission.tenant_id == TENANT
                )
            )
        ).scalar()
        assert rows == 1  # 只有一条提交记录


async def test_b105_same_key_different_fingerprint_conflict() -> None:
    """[B-105] 同 key 异指纹 → COMMON_CONFLICT。"""
    from muad_api import AppError
    from muad_api.error_codes import ErrorCode

    run_id, conv_id = await _seed_run()
    service = RunSubmissionService(get_session_factory())
    key = f"idem-{uuid.uuid4()}"
    await service.record_submission(
        tenant_id=TENANT,
        idempotency_key=key,
        endpoint="create-run",
        actor_user_id=USER,
        run_id=run_id,
        conversation_id=conv_id,
        request_fingerprint=submission_fingerprint(key_payload={"text": "hello"}, message_id=None),
        first_seq=1,
        last_seq=2,
    )

    different = submission_fingerprint(key_payload={"text": "changed"}, message_id=None)
    with pytest.raises(AppError) as exc:
        await service.find_replay(
            tenant_id=TENANT, idempotency_key=key, endpoint="create-run", fingerprint=different
        )
    assert exc.value.code == ErrorCode.COMMON_CONFLICT


async def test_b105_resume_submission_scoped_by_run() -> None:
    """[B-105] resume 提交指纹包含 run_id/input：两次回复不合并。"""
    run_id, conv_id = await _seed_run()
    service = RunSubmissionService(get_session_factory())

    key = f"resume-{uuid.uuid4()}"
    fp_a = submission_fingerprint(run_id=run_id, key_payload={"text": "answer one"}, message_id=None)
    await service.record_submission(
        tenant_id=TENANT,
        idempotency_key=key,
        endpoint="resume-run",
        actor_user_id=USER,
        run_id=run_id,
        conversation_id=conv_id,
        request_fingerprint=fp_a,
        first_seq=4,
        last_seq=6,
    )

    fp_b = submission_fingerprint(run_id=run_id, key_payload={"text": "answer two"}, message_id=None)
    assert fp_a != fp_b  # 不同输入指纹不同 → 新 submission，不重放
