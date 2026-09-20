"""[B-106] 租约续约与终态 CAS（真实 PostgreSQL run_record 租约列）。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import Conversation, RunRecord

from muad_agent_runtime.application.run_lease import (
    RunLeaseService,
    current_owner_rejected,
)


@pytest.fixture()
async def running_run():
    tenant = f"lease-{uuid.uuid4()}"
    conversation_id, run_id = uuid.uuid4(), uuid.uuid4()
    owner = f"inst-{uuid.uuid4()}"
    async with get_session_factory()() as session:
        session.add(
            Conversation(
                id=conversation_id,
                tenant_id=tenant,
                user_id=uuid.uuid4(),
                agent_id=uuid.uuid4(),
                status="ACTIVE",
                last_seq=0,
            )
        )
        session.add(
            RunRecord(
                id=run_id,
                tenant_id=tenant,
                conversation_id=conversation_id,
                user_id=uuid.uuid4(),
                agent_id=uuid.uuid4(),
                status="RUNNING",
                input_text="hi",
                trace_id=uuid.uuid4().hex,
                cancel_requested=False,
                lease_owner=owner,
                lease_until=datetime.now(UTC) + timedelta(seconds=60),
            )
        )
        await session.commit()
    yield {"tenant_id": tenant, "conversation_id": conversation_id, "run_id": run_id, "owner": owner}
    async with get_session_factory()() as session:
        await session.execute(
            RunRecord.__table__.delete().where(RunRecord.id == run_id)
        )
        await session.execute(
            Conversation.__table__.delete().where(Conversation.id == conversation_id)
        )
        await session.commit()


async def test_b106_renew_only_by_owner(running_run) -> None:
    """[B-106] 当前 owner 续约成功；其他实例续约被拒。"""
    service = RunLeaseService(get_session_factory)
    assert await service.renew(running_run["run_id"], running_run["owner"]) is True
    assert await service.renew(running_run["run_id"], "other-instance") is False


async def test_b106_terminal_write_cas(running_run) -> None:
    """[B-106] 终态 CAS：仅当前 owner 且 RUNNING 可写终态；第二次写失败。"""
    service = RunLeaseService(get_session_factory)
    assert (
        await service.complete(
            running_run["run_id"],
            owner=running_run["owner"],
            status="COMPLETED",
            error_code=None,
            error_message=None,
        )
        is True
    )
    # 非终态后再次 CAS 失败（幂等拒绝重写）
    assert (
        await service.complete(
            running_run["run_id"],
            owner=running_run["owner"],
            status="COMPLETED",
            error_code=None,
            error_message=None,
        )
        is False
    )


async def test_b106_terminal_write_rejected_for_wrong_owner(running_run) -> None:
    """[B-106] 非 owner 终态写入被拒（current_owner_rejected 可观测）。"""
    service = RunLeaseService(get_session_factory)
    assert (
        await service.complete(
            running_run["run_id"],
            owner="intruder",
            status="COMPLETED",
            error_code=None,
            error_message=None,
        )
        is False
    )
    async with get_session_factory()() as session:
        status = await session.scalar(
            sa.select(RunRecord.status).where(RunRecord.id == running_run["run_id"])
        )
    assert status == "RUNNING"
