"""B-125：Schedule 管理面（Console 专用的 Worker Admin API，设计 API-17 / §3.4.6）。

Console 从这里读 `owner_user_id`（动作端点按 owner 复核可见性）并执行管理员软删；
运营动作本身走 `/internal/schedules/{id}/pause|resume|cancel`（Console 套件与
`test_scheduled_v2_lifecycle.py` 覆盖）。真实边界：真实内部 HTTP（ASGI）→ Admin guard →
ScheduleService → 真实 PostgreSQL。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from muad_agent_worker.infrastructure.models.task import (
    ScheduleFire,
    TaskExecution,
    TaskSchedule,
    TaskScheduleRevision,
)
from muad_agent_worker.main import app
from muad_contracts import ScheduleMode

from agent_worker.conftest import TenantContext
from agent_worker.test_scheduled_v2_snapshot import FakeMaterialsClient, make_materials

INTERNAL_HEADER = "X-Internal-Service"
TENANT_HEADER = "X-Tenant-Id"
TOKEN = "test-internal-service-token"

OWNER = uuid.UUID("5a1d0c1e-0000-4000-8000-00000000c125")
AGENT = uuid.UUID("5a1d0c1e-0000-4000-8000-0000000a9125")

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def internal_service_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", TOKEN)


def _headers(tenant_id: str) -> dict[str, str]:
    return {INTERNAL_HEADER: TOKEN, TENANT_HEADER: tenant_id}


async def _seed_schedule(
    tenant: TenantContext,
    *,
    status: str = "ACTIVE",
    owner_user_id: uuid.UUID = OWNER,
    name: str = "A 客户周报",
) -> tuple[uuid.UUID, uuid.UUID]:
    schedule_id, revision_id = uuid.uuid4(), uuid.uuid4()
    async with tenant.session_factory() as session, session.begin():
        session.add(
            TaskSchedule(
                id=schedule_id,
                tenant_id=tenant.tenant_id,
                owner_user_id=owner_user_id,
                agent_id=AGENT,
                name=name,
                status=status,
                config_version=1,
                next_fire_at=datetime.now(UTC) + timedelta(hours=1),
                created_by=owner_user_id,
            )
        )
        await session.flush()
        session.add(
            TaskScheduleRevision(
                id=revision_id,
                tenant_id=tenant.tenant_id,
                schedule_id=schedule_id,
                revision_no=1,
                mode="REMINDER",
                instruction_raw="提醒我开会",
                normalized_goal="提醒开会",
                recurrence_kind="CRON",
                cron_expr="0 9 * * *",
                timezone="Asia/Shanghai",
                schedule_policy={},
                context_json={},
                payload_kind="REMINDER", payload_json={"kind": "REMINDER", "text": "开会"},
                delivery_intent="NONE",
                preflight_json={},
                created_by=owner_user_id,
            )
        )
        await session.flush()
        schedule = await session.get(TaskSchedule, schedule_id)
        assert schedule is not None
        schedule.current_revision_id = revision_id
    return schedule_id, revision_id


async def test_b125_missing_internal_identity_is_forbidden(client: AsyncClient) -> None:
    response = await client.get("/internal/admin/schedules")
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


async def test_b125_list_detail_and_filters_are_tenant_scoped(
    client: AsyncClient, tenant: TenantContext
) -> None:
    other_owner = uuid.uuid4()
    active_id, _ = await _seed_schedule(tenant)
    paused_id, _ = await _seed_schedule(tenant, status="PAUSED", owner_user_id=other_owner)
    headers = _headers(tenant.tenant_id)

    listed = await client.get("/internal/admin/schedules", headers=headers)
    assert listed.status_code == 200, listed.text
    assert listed.json()["data"]["total"] == 2

    by_status = await client.get(
        "/internal/admin/schedules", headers=headers, params={"status": "PAUSED"}
    )
    assert [item["schedule_id"] for item in by_status.json()["data"]["items"]] == [
        str(paused_id)
    ]
    by_owner = await client.get(
        "/internal/admin/schedules", headers=headers, params={"actor_user_id": str(OWNER)}
    )
    assert [item["schedule_id"] for item in by_owner.json()["data"]["items"]] == [str(active_id)]

    detail = await client.get(f"/internal/admin/schedules/{active_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["data"]["schedule_id"] == str(active_id)
    # Console 用这一行把动作发给 owner 范围内的端点。
    assert detail.json()["data"]["owner_user_id"] == str(OWNER)

    other_tenant = await client.get(
        f"/internal/admin/schedules/{active_id}", headers=_headers("test-other-tenant")
    )
    assert other_tenant.status_code == 404
    assert other_tenant.json()["code"] == "SCHEDULE_NOT_FOUND"


async def test_b125_pause_and_resume_keep_the_schedule_pointer(
    client: AsyncClient, tenant: TenantContext
) -> None:
    schedule_id, _ = await _seed_schedule(tenant)
    headers = _headers(tenant.tenant_id)

    paused = await client.put(f"/internal/admin/schedules/{schedule_id}/pause", headers=headers)
    assert paused.status_code == 200, paused.text
    assert paused.json()["data"]["status"] == "PAUSED"
    assert paused.json()["data"]["next_fire_at"] is not None, "暂停不清调度指针"

    replay = await client.put(f"/internal/admin/schedules/{schedule_id}/pause", headers=headers)
    assert replay.status_code == 200
    assert replay.json()["data"]["status"] == "PAUSED"

    app.state.schedule_materials_client = FakeMaterialsClient(
        materials=make_materials(mode=ScheduleMode.REMINDER)
    )
    try:
        resumed = await client.put(
            f"/internal/admin/schedules/{schedule_id}/resume", headers=headers
        )
    finally:
        del app.state.schedule_materials_client
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["data"]["status"] == "ACTIVE"
    next_fire = datetime.fromisoformat(resumed.json()["data"]["next_fire_at"])
    assert next_fire > datetime.now(UTC) - timedelta(minutes=1), "恢复从 now 重算，不补发"


async def test_b125_terminal_schedule_rejects_pause_without_state_change(
    client: AsyncClient, tenant: TenantContext
) -> None:
    schedule_id, _ = await _seed_schedule(tenant, status="ENDED")
    headers = _headers(tenant.tenant_id)

    response = await client.put(f"/internal/admin/schedules/{schedule_id}/pause", headers=headers)
    assert response.status_code == 409
    assert response.json()["code"] == "SCHEDULE_STATE_INVALID"

    detail = await client.get(f"/internal/admin/schedules/{schedule_id}", headers=headers)
    assert detail.json()["data"]["status"] == "ENDED", "失败不得写状态"


async def test_b125_delete_is_idempotent_and_keeps_history(
    client: AsyncClient, tenant: TenantContext
) -> None:
    schedule_id, revision_id = await _seed_schedule(tenant)
    now = datetime.now(UTC)
    execution_id, fire_id = uuid.uuid4(), uuid.uuid4()
    async with tenant.session_factory() as session, session.begin():
        session.add(
            TaskExecution(
                id=execution_id,
                tenant_id=tenant.tenant_id,
                schedule_id=schedule_id,
                agent_id=AGENT,
                actor_user_id=OWNER,
                trigger_type="SCHEDULED",
                status="COMPLETED",
                input_json={},
                execution_snapshot_schema_version=4,
                execution_snapshot_json={"schema_version": 4},
                snapshot_hash="sha256:" + "a" * 64,
                idempotency_key=f"admin-{execution_id}",
                not_before=now,
            )
        )
        await session.flush()
        session.add(
            ScheduleFire(
                id=fire_id,
                tenant_id=tenant.tenant_id,
                schedule_id=schedule_id,
                schedule_revision_id=revision_id,
                scheduled_at=now,
                status="EXECUTION_CREATED",
                execution_id=execution_id,
            )
        )
    headers = _headers(tenant.tenant_id)

    first = await client.delete(f"/internal/admin/schedules/{schedule_id}", headers=headers)
    assert first.status_code == 200
    assert first.json()["data"] == {"schedule_id": str(schedule_id), "deleted": True}

    second = await client.delete(f"/internal/admin/schedules/{schedule_id}", headers=headers)
    assert second.status_code == 200
    assert second.json()["data"] == {"schedule_id": str(schedule_id), "deleted": True}

    listed = await client.get("/internal/admin/schedules", headers=headers)
    assert listed.json()["data"]["total"] == 0

    async with tenant.session_factory() as session:
        execution = await session.get(TaskExecution, execution_id)
        fire = await session.get(ScheduleFire, fire_id)
        count = await session.scalar(
            sa.select(sa.func.count())
            .select_from(TaskSchedule)
            .where(TaskSchedule.tenant_id == tenant.tenant_id)
        )
    assert execution is not None and execution.status == "COMPLETED"
    assert fire is not None, "软删不删除 Fire 历史"
    assert count == 1, "软删只打标记，行仍在（列表按 is_deleted 过滤）"


async def test_b125_unknown_schedule_returns_not_found(
    client: AsyncClient, tenant: TenantContext
) -> None:
    headers = _headers(tenant.tenant_id)
    unknown = uuid.uuid4()

    detail = await client.get(f"/internal/admin/schedules/{unknown}", headers=headers)
    assert detail.status_code == 404
    assert detail.json()["code"] == "SCHEDULE_NOT_FOUND"

    paused = await client.put(f"/internal/admin/schedules/{unknown}/pause", headers=headers)
    assert paused.status_code == 404

    deleted = await client.delete(f"/internal/admin/schedules/{unknown}", headers=headers)
    assert deleted.status_code == 404
