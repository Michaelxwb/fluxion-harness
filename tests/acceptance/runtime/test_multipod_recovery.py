"""[S-04][E-07][RULE-arch-001] 跨 Pod 重建与断流崩溃恢复（真实 PostgreSQL）。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import Conversation, RunRecord

from muad_agent_runtime.application.run_lease import RunLeaseService
from muad_agent_runtime.application.run_service import reap_abandoned_runs

TENANT = f"pod-{uuid.uuid4()}"


@pytest.fixture(autouse=True)
async def _cleanup():
    yield
    sf = get_session_factory()
    async with sf() as session:
        await session.execute(RunRecord.__table__.delete().where(RunRecord.tenant_id == TENANT))
        await session.execute(Conversation.__table__.delete().where(Conversation.tenant_id == TENANT))
        await session.commit()


async def _seed_running(owner: str, *, expired: bool = False) -> tuple[uuid.UUID, uuid.UUID]:
    conv_id, run_id = uuid.uuid4(), uuid.uuid4()
    now = datetime.now(UTC)
    lease_until = now - timedelta(hours=1) if expired else now + timedelta(minutes=5)
    sf = get_session_factory()
    async with sf() as session:
        session.add(
            Conversation(
                id=conv_id, tenant_id=TENANT, user_id=uuid.uuid4(),
                agent_id=uuid.uuid4(), status="ACTIVE", last_seq=0,
            )
        )
        session.add(
            RunRecord(
                id=run_id, tenant_id=TENANT, conversation_id=conv_id,
                user_id=uuid.uuid4(), agent_id=uuid.uuid4(), status="RUNNING",
                input_text="hi", trace_id=uuid.uuid4().hex, cancel_requested=False,
                lease_owner=owner, lease_until=lease_until,
            )
        )
        await session.commit()
    return run_id, conv_id


async def test_s04_cross_pod_takeover() -> None:
    """[S-04] Pod A lease 过期 → Reaper 回收 → Pod B 创建新 Run（无 sticky session）。"""
    sf = get_session_factory()
    # Pod A 的 Run（lease 过期）
    run_a, conv_a = await _seed_running("pod-a", expired=True)

    # Reaper 回收 Pod A 的过期 Run
    reaped = await reap_abandoned_runs(sf)
    assert reaped >= 1

    async with sf() as session:
        row = await session.get(RunRecord, run_a)
        assert row.status == "FAILED"
        assert row.error_code == "RUN_ABANDONED"

    # Pod B 创建新 Run 在同一 conversation（无 sticky session）
    run_b = uuid.uuid4()
    async with sf() as session:
        session.add(
            RunRecord(
                id=run_b, tenant_id=TENANT, conversation_id=conv_a,
                user_id=uuid.uuid4(), agent_id=uuid.uuid4(), status="RUNNING",
                input_text="from pod B", trace_id=uuid.uuid4().hex,
                cancel_requested=False, lease_owner="pod-b",
            )
        )
        await session.commit()

    async with sf() as session:
        # Pod A 的旧 owner 不能续约 Pod B 的 Run
        lease_service = RunLeaseService(get_session_factory)
        renewed = await lease_service.renew(run_b, "pod-a")
        assert renewed is False
        # Pod B 可以续约自己的 Run
        renewed_b = await lease_service.renew(run_b, "pod-b")
        assert renewed_b is True


async def test_e07_reaper_marks_abandoned_and_get_shows_terminal() -> None:
    """[E-07] 进程终止 → Reaper 回收 → GET 查到终态 FAILED/RUN_ABANDONED。"""
    sf = get_session_factory()
    run_id, conv_id = await _seed_running("pod-dead", expired=True)

    reaped = await reap_abandoned_runs(sf)
    assert reaped >= 1

    async with sf() as session:
        row = await session.get(RunRecord, run_id)
        assert row.status == "FAILED"
        assert row.error_code == "RUN_ABANDONED"
        assert row.end_time is not None

    # RUN_ABANDONED 不作为 HTTP code（仅 DB 状态标记）
    assert row.error_code != "HTTP_500"
