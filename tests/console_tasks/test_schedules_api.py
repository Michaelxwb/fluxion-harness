"""B-128：Console Schedule 列表/详情/删除（API-20 ~ API-23 的既有行为回归）。

真实边界：真实 Console HTTP/session → 真实 Worker ASGI → 真实 PostgreSQL。
运营动作（pause/resume/cancel）与 Fire 历史在 `test_scheduled_v2_console_api.py`；
本文件只锁"既有能力不回退"：认证、统一封套、分页过滤、软删保留历史、跨租户不可见。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from console_platform.conftest import TenantContext
from httpx import ASGITransport, AsyncClient
from muad_agent_worker.infrastructure.db import get_session_factory as worker_session_factory
from muad_agent_worker.infrastructure.models.task import TaskExecution
from muad_console_platform.main import app as console_app

from console_tasks.conftest import WORKER_TENANT_CLEANUP
from console_tasks.helpers import seed_execution, seed_fire, seed_schedule


def _headers(tenant: TenantContext) -> dict[str, str]:
    return {"X-Tenant-Id": tenant.tenant_id}


async def test_b128_requires_authenticated_session(task_tenant: TenantContext) -> None:
    transport = ASGITransport(app=console_app)
    async with AsyncClient(transport=transport, base_url="http://test") as anonymous:
        for path in (
            "/api/v1/schedules",
            f"/api/v1/schedules/{uuid.uuid4()}",
            f"/api/v1/schedules/{uuid.uuid4()}/fires",
        ):
            response = await anonymous.get(path)
            assert response.status_code == 401, path


async def test_b128_list_detail_envelope_and_filters(
    client: AsyncClient, task_tenant: TenantContext
) -> None:
    active_id, _ = await seed_schedule(task_tenant.tenant_id)
    paused_id, _ = await seed_schedule(
        task_tenant.tenant_id, name="已暂停", status="PAUSED", next_fire_at=None
    )
    headers = _headers(task_tenant)

    listed = await client.get("/api/v1/schedules", headers=headers)
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert {"code", "msg", "data", "trace_id", "request_id", "timestamp"} <= set(body)
    assert body["data"]["total"] == 2
    assert body["data"]["page"] == 1

    paused = await client.get("/api/v1/schedules", headers=headers, params={"status": "PAUSED"})
    assert [item["schedule_id"] for item in paused.json()["data"]["items"]] == [str(paused_id)]

    detail = await client.get(f"/api/v1/schedules/{active_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["data"]["schedule_id"] == str(active_id)
    assert detail.json()["data"]["status"] == "ACTIVE"

    missing = await client.get(f"/api/v1/schedules/{uuid.uuid4()}", headers=headers)
    assert missing.status_code == 404
    assert missing.json()["code"] == "SCHEDULE_NOT_FOUND"


async def test_b128_delete_is_idempotent_and_keeps_history(
    client: AsyncClient, task_tenant: TenantContext
) -> None:
    schedule_id, revision_id = await seed_schedule(task_tenant.tenant_id)
    now = datetime.now(UTC)
    execution_id = await seed_execution(
        task_tenant.tenant_id, schedule_id=schedule_id, status="COMPLETED", finished_at=now
    )
    await seed_fire(
        task_tenant.tenant_id,
        schedule_id,
        revision_id,
        scheduled_at=now,
        execution_id=execution_id,
    )
    headers = _headers(task_tenant)

    deleted = await client.delete(f"/api/v1/schedules/{schedule_id}", headers=headers)
    assert deleted.status_code == 200
    assert deleted.json()["data"] == {"schedule_id": str(schedule_id), "deleted": True}

    replay = await client.delete(f"/api/v1/schedules/{schedule_id}", headers=headers)
    assert replay.status_code == 200
    assert replay.json()["data"] == {"schedule_id": str(schedule_id), "deleted": True}

    listed = await client.get("/api/v1/schedules", headers=headers)
    assert listed.json()["data"]["total"] == 0
    hidden = await client.get(f"/api/v1/schedules/{schedule_id}", headers=headers)
    assert hidden.status_code == 404

    async with worker_session_factory()() as session:
        task = await session.get(TaskExecution, execution_id)
    assert task is not None
    assert task.status == "COMPLETED", "删除 Schedule 不得改写历史 Execution"
    assert task.schedule_id == schedule_id

    history = await client.get(
        "/api/v1/tasks", headers=headers, params={"schedule_id": str(schedule_id)}
    )
    assert [item["task_id"] for item in history.json()["data"]["items"]] == [str(execution_id)]


async def test_b128_cross_tenant_schedule_hidden(
    client: AsyncClient, task_tenant: TenantContext
) -> None:
    foreign_tenant = f"test-foreign-{uuid.uuid4().hex[:8]}"
    foreign_id, _ = await seed_schedule(foreign_tenant, name="别人的任务")
    headers = _headers(task_tenant)
    try:
        detail = await client.get(f"/api/v1/schedules/{foreign_id}", headers=headers)
        fires = await client.get(f"/api/v1/schedules/{foreign_id}/fires", headers=headers)
        action = await client.post(
            f"/api/v1/schedules/{foreign_id}/actions",
            json={"action": "pause"},
            headers=headers,
        )
        deleted = await client.delete(f"/api/v1/schedules/{foreign_id}", headers=headers)
        assert detail.status_code == 404
        assert fires.status_code == 404
        assert action.status_code == 404
        assert deleted.status_code == 404

        listed = await client.get("/api/v1/schedules", headers=headers)
        assert listed.json()["data"]["total"] == 0
    finally:
        async with worker_session_factory()() as session:
            for statement in WORKER_TENANT_CLEANUP:
                await session.execute(sa.text(statement), {"t": foreign_tenant})
            await session.commit()
