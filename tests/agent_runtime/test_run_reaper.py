"""[E-04] 真实 Reaper→PostgreSQL lease→CAS：过期 RUNNING 回收，新 Run 可创建。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from muad_agent_runtime.application.run_service import RUN_ABANDONED, reap_abandoned_runs
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import Conversation, RunRecord

TENANT = f"reap-{uuid.uuid4()}"


@pytest.fixture(autouse=True)
async def _cleanup():
    yield
    async with get_session_factory()() as session:
        await session.execute(
            RunRecord.__table__.delete().where(RunRecord.tenant_id == TENANT)
        )
        await session.execute(
            Conversation.__table__.delete().where(Conversation.tenant_id == TENANT)
        )
        await session.commit()


async def _seed(status: str, *, expired: bool) -> uuid.UUID:
    conv_id, run_id = uuid.uuid4(), uuid.uuid4()
    now = datetime.now(UTC)
    lease = now - timedelta(hours=1) if expired else now + timedelta(hours=1)
    async with get_session_factory()() as session:
        session.add(
            Conversation(
                id=conv_id,
                tenant_id=TENANT,
                user_id=uuid.uuid4(),
                agent_id=uuid.uuid4(),
                status="ACTIVE",
                last_seq=0,
            )
        )
        await session.flush()
        await session.execute(
            sa.text(
                'INSERT INTO runtime.run_record (id, tenant_id, conversation_id, user_id, agent_id,'
                ' status, input_text, trace_id, cancel_requested, lease_until, lease_owner)'
                ' VALUES (:id, :tenant, :conv, :uid, :aid, :status, \'x\', :trace, false,'
                ' :lease, \'old-owner\')'
            ).bindparams(
                id=run_id,
                tenant=TENANT,
                conv=conv_id,
                uid=uuid.uuid4(),
                aid=uuid.uuid4(),
                status=status,
                trace=uuid.uuid4().hex,
                lease=lease,
            )
        )
        await session.commit()
    return run_id


async def test_e04_reaper_cas_on_expired_running_only() -> None:
    """[E-04] 仅过期 RUNNING 被 CAS 为 FAILED/RUN_ABANDONED；新 Run 可创建（conversation 释放）。"""
    expired = await _seed("RUNNING", expired=True)
    fresh = await _seed("RUNNING", expired=False)
    completed = await _seed("COMPLETED", expired=True)
    waiting = await _seed("WAITING_INPUT", expired=True)

    await reap_abandoned_runs(get_session_factory())
    ids = {expired, fresh, completed, waiting}
    # 其它测试可能残留过期 run；只断言我们的三个的状态
    async with get_session_factory()() as session:
        rows = (
            await session.execute(
                sa.select(RunRecord).where(RunRecord.id.in_(ids))
            )
        ).scalars().all()
        by_id = {row.id: row for row in rows}
        assert by_id[expired].status == "FAILED"
        assert by_id[expired].error_code == RUN_ABANDONED
        assert by_id[fresh].status == "RUNNING"  # 未过期不回收
        assert by_id[completed].status == "COMPLETED"  # 终态不动
        assert by_id[waiting].status == "WAITING_INPUT"  # WAITING_INPUT 不误扫

        # 旧 conversation 释放：新 Run 可创建（无 active RUNNING）
        conv_active = (
            await session.execute(
                sa.select(sa.func.count()).select_from(RunRecord).where(
                    RunRecord.conversation_id == by_id[expired].conversation_id,
                    RunRecord.status.in_(("RUNNING",)),
                )
            )
        ).scalar()
        assert conv_active == 0
