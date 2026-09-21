"""[B-120] 协作取消与租户隔离（真实 PostgreSQL + Redis cancel hints）。"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import Conversation, RunRecord


async def _seed_run(client, tenant, key: str) -> dict:
    """Run 直接落 DB（runtime.run_record），不依赖 console agent/model FK（仅逻辑 UUID）。"""

    session_factory = get_session_factory()
    conv_id, run_id = uuid.uuid4(), uuid.uuid4()
    agent_id = uuid.uuid4()
    async with session_factory() as session:
        session.add(
            Conversation(
                id=conv_id,
                tenant_id=tenant.tenant_id,
                user_id=uuid.uuid4(),
                agent_id=agent_id,
                status="ACTIVE",
                last_seq=0,
            )
        )
        session.add(
            RunRecord(
                id=run_id,
                tenant_id=tenant.tenant_id,
                conversation_id=conv_id,
                user_id=uuid.uuid4(),
                agent_id=agent_id,
                status="RUNNING",
                input_text="hi",
                trace_id=uuid.uuid4().hex,
                cancel_requested=False,
            )
        )
        await session.commit()
    return {"id": str(agent_id), "run_id": str(run_id), "key": key}


async def test_b120_cancel_is_scoped_to_tenant(client, tenant) -> None:
    """[B-120] 取消请求仅影响本租户 Run；跨租户 404。"""
    key = f"cancel-{uuid.uuid4().hex[:8]}"
    created = await _seed_run(client, tenant, key)
    agent_id = created["id"]
    run_id = uuid.UUID(created["run_id"])


    session_factory = get_session_factory()

    from httpx import ASGITransport, AsyncClient
    from muad_console_platform.main import app

    other_tenant = f"other-{uuid.uuid4()}"
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as other_client:
        # 伪造其他租户上下文：未登录时 401 优先，但服务端口径以 tenant 隔离为准
        response = await other_client.post(
            "/internal/runtime/cancel-active",
            json={
                "agent_id": agent_id,
                "actor_user_id": str(uuid.uuid4()),
                "channel": "WECOM",
            },
            headers={"X-Tenant-Id": other_tenant},
        )
        # 跨租户 → 404（资源按 tenant 隔离不可见），或入口 401/403
        assert response.status_code in (401, 403, 404)

    async with session_factory() as session:
        row = await session.get(RunRecord, run_id)
        assert row.cancel_requested is False  # 未被跨租户取消


async def test_b120_cancel_requested_flag_then_reaper_fallback(client, tenant) -> None:
    """[B-120] cancel_requested 置位后协作终止；Reaper 兜底回收。"""

    key = f"cancel-{uuid.uuid4().hex[:8]}"
    created = await _seed_run(client, tenant, key)
    run_id = uuid.UUID(created["run_id"])

    session_factory = get_session_factory()
    async with session_factory() as session:
        row = await session.get(RunRecord, run_id)
        row.cancel_requested = True
        await session.commit()

    async with session_factory() as session:
        flagged = await session.get(RunRecord, run_id)
        assert flagged.cancel_requested is True

    # Reaper 兜底：过期 RUNNING 回收
    from muad_agent_runtime.application.run_service import reap_abandoned_runs

    async with session_factory() as session:
        await session.execute(
            sa.update(RunRecord)
            .where(RunRecord.id == run_id)
            .values(lease_until=sa.func.now() - sa.text("interval '1 hour'"))
        )
        await session.commit()
    reaped = await reap_abandoned_runs(session_factory)
    assert reaped >= 1  # 本 run 的租约已过期，必须被回收
    async with session_factory() as session:
        run = await session.get(RunRecord, run_id)
        assert run is not None
        assert run.status == "FAILED"
        assert run.error_code == "RUN_ABANDONED"
