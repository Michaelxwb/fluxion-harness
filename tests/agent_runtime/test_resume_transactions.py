"""[B-119] 显式与自动 Resume 的提交幂等（真实 PostgreSQL run_submission + run_record）。"""

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

TENANT = f"resume-{uuid.uuid4()}"


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


async def _seed_waiting_run() -> tuple[uuid.UUID, uuid.UUID]:
    conv_id, run_id = uuid.uuid4(), uuid.uuid4()
    async with get_session_factory()() as session:
        session.add(
            Conversation(
                id=conv_id,
                tenant_id=TENANT,
                user_id=uuid.uuid4(),
                agent_id=uuid.uuid4(),
                status="WAITING_INPUT",
                last_seq=3,
            )
        )
        session.add(
            RunRecord(
                id=run_id,
                tenant_id=TENANT,
                conversation_id=conv_id,
                user_id=uuid.uuid4(),
                agent_id=uuid.uuid4(),
                status="WAITING_INPUT",
                input_text="hi",
                trace_id=uuid.uuid4().hex,
                cancel_requested=False,
            )
        )
        await session.commit()
    return run_id, conv_id


async def test_b119_resume_idempotency_key_scopes_by_run_and_input() -> None:
    """[B-119] resume 指纹包含 run_id+input：同键同输入重放；不同输入 CONFLICT。"""
    run_id, conv_id = await _seed_waiting_run()
    service = RunSubmissionService(get_session_factory())

    key = f"resume-{uuid.uuid4()}"
    fp = submission_fingerprint(run_id=run_id, key_payload={"text": "answer"})
    await service.record_submission(
        tenant_id=TENANT,
        idempotency_key=key,
        endpoint="resume-run",
        actor_user_id=uuid.uuid4(),
        run_id=run_id,
        conversation_id=conv_id,
        request_fingerprint=fp,
        first_seq=4,
        last_seq=6,
    )

    replay = await service.find_replay(
        tenant_id=TENANT, idempotency_key=key, endpoint="resume-run", fingerprint=fp
    )
    assert replay is not None
    assert replay["run_id"] == str(run_id)

    from muad_api import AppError
    from muad_api.error_codes import ErrorCode

    with pytest.raises(AppError) as exc:
        await service.find_replay(
            tenant_id=TENANT,
            idempotency_key=key,
            endpoint="resume-run",
            fingerprint=submission_fingerprint(run_id=run_id, key_payload={"text": "other"}),
        )
    assert exc.value.code == ErrorCode.COMMON_CONFLICT


async def test_b119_missing_idempotency_key_and_input_id_is_validation_error() -> None:
    """[B-119] 显式 Idempotency-Key 与 input.id 均缺失 → COMMON_VALIDATION_ERROR。"""
    from muad_api import AppError
    from muad_api.error_codes import ErrorCode

    from muad_agent_runtime.application.run_submission import (
        require_resume_idempotency,
    )

    with pytest.raises(AppError) as exc:
        require_resume_idempotency(idempotency_key=None, input_id=None)
    assert exc.value.code == ErrorCode.COMMON_VALIDATION_ERROR

    # 任一存在即可
    assert require_resume_idempotency(idempotency_key="k", input_id=None) == "k"
    assert require_resume_idempotency(idempotency_key=None, input_id="msg-1") == "msg-1"
