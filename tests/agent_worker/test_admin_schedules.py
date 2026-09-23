"""B-125：Schedule Admin 详情、启停与删除（设计 API-17 / API-13 / API-16）。

真实边界：真实内部 HTTP（ASGI）→ Admin guard → ScheduleService → 真实 PostgreSQL。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from conftest import TenantContext
from helpers import create_schedule_payload
from httpx import AsyncClient
from muad_agent_worker.scheduler.service import ScheduleService
from muad_contracts import ScheduleSpec

INTERNAL_HEADER = "X-Internal-Service"
TENANT_HEADER = "X-Tenant-Id"
TOKEN = "test-internal-service-token"


@pytest.fixture(autouse=True)
def internal_service_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", TOKEN)


def _headers(tenant_id: str) -> dict[str, str]:
    return {INTERNAL_HEADER: TOKEN, TENANT_HEADER: tenant_id}


async def _create_schedule(
    tenant: TenantContext, *, schedule: ScheduleSpec | None = None
) -> uuid.UUID:
    payload = create_schedule_payload(tenant, schedule=schedule)
    async with tenant.session_factory() as session:
        created = await ScheduleService(session, tenant.settings).create_schedule(
            tenant.tenant_id, payload
        )
        await session.commit()
    return created.id


async def test_b125_missing_internal_identity_is_forbidden(client: AsyncClient) -> None:
    response = await client.get("/internal/admin/schedules")
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


async def test_b125_list_filters_completed_and_cross_tenant_hidden(
    client: AsyncClient, tenant: TenantContext
) -> None:
    active_id = await _create_schedule(tenant)
    once_id = await _create_schedule(
        tenant, schedule=ScheduleSpec(type="ONCE", run_at=datetime.now(UTC), timezone="UTC")
    )
    headers = _headers(tenant.tenant_id)

    listed = await client.get("/internal/admin/schedules", headers=headers)
    assert listed.status_code == 200, listed.text
    assert listed.json()["data"]["total"] == 2

    filtered = await client.get(
        "/internal/admin/schedules", headers=headers, params={"status": "ACTIVE"}
    )
    ids = {item["schedule_id"] for item in filtered.json()["data"]["items"]}
    assert ids == {str(active_id), str(once_id)}

    detail = await client.get(f"/internal/admin/schedules/{active_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["data"]["schedule_id"] == str(active_id)

    other = await client.get(
        f"/internal/admin/schedules/{active_id}", headers=_headers("test-other-tenant")
    )
    assert other.status_code == 404
    assert other.json()["code"] == "COMMON_NOT_FOUND"


async def test_b125_pause_and_resume_cas(client: AsyncClient, tenant: TenantContext) -> None:
    schedule_id = await _create_schedule(tenant)
    headers = _headers(tenant.tenant_id)

    paused = await client.put(f"/internal/admin/schedules/{schedule_id}/pause", headers=headers)
    assert paused.status_code == 200, paused.text
    paused_data = paused.json()["data"]
    assert paused_data["status"] == "PAUSED"
    assert paused_data["next_fire_at"] is not None, "暂停保留 next_fire_at"

    replay = await client.put(f"/internal/admin/schedules/{schedule_id}/pause", headers=headers)
    assert replay.json()["data"]["status"] == "PAUSED"

    resumed = await client.put(f"/internal/admin/schedules/{schedule_id}/resume", headers=headers)
    assert resumed.status_code == 200, resumed.text
    resumed_data = resumed.json()["data"]
    assert resumed_data["status"] == "ACTIVE"
    assert resumed_data["next_fire_at"] > datetime.now(UTC).isoformat()

    detail = await client.get(f"/internal/admin/schedules/{schedule_id}", headers=headers)
    assert detail.json()["data"]["status"] == "ACTIVE"


async def test_b125_terminal_schedule_rejects_pause_without_state_change(
    client: AsyncClient, tenant: TenantContext
) -> None:
    schedule_id = await _create_schedule(
        tenant, schedule=ScheduleSpec(type="ONCE", run_at=datetime.now(UTC), timezone="UTC")
    )
    async with tenant.session_factory() as session:
        from sqlalchemy import update

        from muad_agent_worker.infrastructure.models.task import TaskSchedule

        await session.execute(
            update(TaskSchedule)
            .where(TaskSchedule.id == schedule_id)
            .values(status="MISSED", next_fire_at=None, completed_at=None)
        )
        await session.commit()
    headers = _headers(tenant.tenant_id)

    response = await client.put(f"/internal/admin/schedules/{schedule_id}/pause", headers=headers)
    assert response.status_code == 409
    assert response.json()["code"] == "REVISION_CONFLICT"

    detail = await client.get(f"/internal/admin/schedules/{schedule_id}", headers=headers)
    assert detail.json()["data"]["status"] == "MISSED", "失败不得写状态"


async def test_b125_delete_is_idempotent_and_keeps_tasks(
    client: AsyncClient, tenant: TenantContext
) -> None:
    schedule_id = await _create_schedule(tenant)
    headers = _headers(tenant.tenant_id)

    first = await client.delete(f"/internal/admin/schedules/{schedule_id}", headers=headers)
    assert first.status_code == 200
    assert first.json()["data"] == {"schedule_id": str(schedule_id), "deleted": True}

    second = await client.delete(f"/internal/admin/schedules/{schedule_id}", headers=headers)
    assert second.status_code == 200
    assert second.json()["data"] == {"schedule_id": str(schedule_id), "deleted": True}

    listed = await client.get("/internal/admin/schedules", headers=headers)
    assert listed.json()["data"]["total"] == 0


async def test_b125_unknown_schedule_returns_not_found(
    client: AsyncClient, tenant: TenantContext
) -> None:
    headers = _headers(tenant.tenant_id)
    unknown = uuid.uuid4()

    detail = await client.get(f"/internal/admin/schedules/{unknown}", headers=headers)
    assert detail.status_code == 404
    assert detail.json()["code"] == "COMMON_NOT_FOUND"

    paused = await client.put(f"/internal/admin/schedules/{unknown}/pause", headers=headers)
    assert paused.status_code == 404

    deleted = await client.delete(f"/internal/admin/schedules/{unknown}", headers=headers)
    assert deleted.status_code == 404
