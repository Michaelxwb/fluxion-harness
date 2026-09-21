"""Schedule 创建幂等、更新与时区计算（B-109 / RULE-time-001）。

真实边界：真实 ASGI HTTP → 真实 PostgreSQL → 真实 IANA 时区计算。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from conftest import TenantContext
from helpers import create_schedule_payload, persist_task
from httpx import AsyncClient
from muad_agent_worker.scheduler.service import compute_next_fire_at
from muad_contracts import ScheduleSpec
from pydantic import ValidationError

IDEMPOTENCY_HEADER = "Idempotency-Key"

COUNT_SCHEDULES = sa.text("SELECT count(*) FROM task.task_schedule WHERE tenant_id = :tenant_id")


def _headers(tenant: TenantContext, **extra: str) -> dict[str, str]:
    return {"X-Tenant-Id": tenant.tenant_id, **extra}


async def _scalar(tenant: TenantContext, statement: sa.TextClause) -> int:
    async with tenant.session_factory() as session:
        return (await session.execute(statement, {"tenant_id": tenant.tenant_id})).scalar()


def _schedule_body(tenant: TenantContext) -> dict[str, object]:
    return create_schedule_payload(tenant).model_dump(mode="json")


async def test_same_key_creates_only_one_schedule(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """同 Idempotency-Key 重复提交只建一条 Schedule，并重放首次响应。"""
    body = _schedule_body(tenant)
    headers = _headers(tenant, **{IDEMPOTENCY_HEADER: "sched-key-1"})

    first = await client.post("/internal/schedules", json=body, headers=headers)
    second = await client.post("/internal/schedules", json=body, headers=headers)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["data"]["schedule_id"] == second.json()["data"]["schedule_id"]
    assert await _scalar(tenant, COUNT_SCHEDULES) == 1


async def test_invalid_cron_is_rejected(client: AsyncClient, tenant: TenantContext) -> None:
    """非法 cron 表达式必须被拒（不能 500）。"""
    body = _schedule_body(tenant)
    body["schedule"] = {
        "type": "CRON",
        "cron": "definitely not a cron",
        "timezone": "Asia/Shanghai",
    }

    response = await client.post("/internal/schedules", json=body, headers=_headers(tenant))

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "COMMON_VALIDATION_ERROR"
    assert await _scalar(tenant, COUNT_SCHEDULES) == 0


async def test_update_increments_revision(client: AsyncClient, tenant: TenantContext) -> None:
    """更新递增 revision，并重算 next_fire_at。"""
    created = await client.post(
        "/internal/schedules", json=_schedule_body(tenant), headers=_headers(tenant)
    )
    assert created.status_code == 200, created.text
    schedule_id = created.json()["data"]["schedule_id"]
    assert created.json()["data"]["revision"] == 1

    updated = await client.put(
        f"/internal/schedules/{schedule_id}",
        json={
            "name": "renamed",
            "schedule": {"type": "CRON", "cron": "0 10 * * *", "timezone": "Asia/Shanghai"},
        },
        headers=_headers(tenant),
    )

    assert updated.status_code == 200, updated.text
    data = updated.json()["data"]
    assert data["revision"] == 2
    assert data["name"] == "renamed"
    assert data["cron_expr"] == "0 10 * * *"


async def test_update_rejects_terminal_schedule(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """COMPLETED / MISSED 为终态，不可更新。"""
    created = await client.post(
        "/internal/schedules", json=_schedule_body(tenant), headers=_headers(tenant)
    )
    schedule_id = created.json()["data"]["schedule_id"]

    async with tenant.session_factory() as session:
        await session.execute(
            sa.text("UPDATE task.task_schedule SET status = 'MISSED' WHERE id = :id"),
            {"id": schedule_id},
        )
        await session.commit()

    rejected = await client.put(
        f"/internal/schedules/{schedule_id}",
        json={"name": "nope"},
        headers=_headers(tenant),
    )

    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["code"] == "REVISION_CONFLICT"


async def test_update_does_not_touch_existing_task_snapshots(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """更新只影响将来触发，已创建 Task 的快照保持不变。"""
    created = await client.post(
        "/internal/schedules", json=_schedule_body(tenant), headers=_headers(tenant)
    )
    schedule_id = uuid.UUID(created.json()["data"]["schedule_id"])

    task = await persist_task(tenant, schedule_id=schedule_id)
    async with tenant.session_factory() as session:
        before = (
            await session.execute(
                sa.text("SELECT execution_snapshot_json FROM task.task_execution WHERE id = :id"),
                {"id": task.id},
            )
        ).scalar()

    updated = await client.put(
        f"/internal/schedules/{schedule_id}",
        json={"schedule": {"type": "CRON", "cron": "0 11 * * *", "timezone": "Asia/Shanghai"}},
        headers=_headers(tenant),
    )
    assert updated.status_code == 200, updated.text

    async with tenant.session_factory() as session:
        after = (
            await session.execute(
                sa.text("SELECT execution_snapshot_json FROM task.task_execution WHERE id = :id"),
                {"id": task.id},
            )
        ).scalar()
    assert after == before


async def test_unknown_schedule_update_returns_404(
    client: AsyncClient, tenant: TenantContext
) -> None:
    response = await client.put(
        f"/internal/schedules/{uuid.uuid4()}", json={"name": "x"}, headers=_headers(tenant)
    )
    assert response.status_code == 404


def test_dst_boundary_uses_target_zone_offset() -> None:
    """跨 DST 的 next_fire_at 按目标时区计算（美东 2026-03-08 起为 EDT）。"""
    base = datetime(2026, 3, 8, 0, 0, tzinfo=UTC)
    spec = ScheduleSpec(type="CRON", cron="0 9 * * *", timezone="America/New_York")

    next_fire = compute_next_fire_at(spec, base)

    # 2026-03-08 09:00 EDT == 13:00 UTC（若误用 EST 会是 14:00 UTC）
    assert next_fire.astimezone(UTC) == datetime(2026, 3, 8, 13, 0, tzinfo=UTC)


def test_once_schedule_uses_run_at() -> None:
    run_at = datetime(2026, 4, 1, 9, 30, tzinfo=UTC)
    spec = ScheduleSpec(type="ONCE", run_at=run_at, timezone="UTC")
    assert compute_next_fire_at(spec, datetime(2026, 3, 1, tzinfo=UTC)) == run_at


@pytest.mark.parametrize("timezone", ["Not/AZone", "Mars/Olympus"])
def test_invalid_iana_timezone_is_rejected_by_contract(timezone: str) -> None:
    with pytest.raises(ValidationError):
        ScheduleSpec(type="CRON", cron="0 9 * * *", timezone=timezone)


async def test_next_fire_at_is_recomputed_on_update(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """更新后 next_fire_at 按新规则重算。"""
    created = await client.post(
        "/internal/schedules", json=_schedule_body(tenant), headers=_headers(tenant)
    )
    schedule_id = created.json()["data"]["schedule_id"]

    updated = await client.put(
        f"/internal/schedules/{schedule_id}",
        json={"schedule": {"type": "CRON", "cron": "0 3 * * *", "timezone": "UTC"}},
        headers=_headers(tenant),
    )

    assert updated.status_code == 200, updated.text
    next_fire = datetime.fromisoformat(updated.json()["data"]["next_fire_at"])
    assert next_fire.hour == 3
    assert next_fire > datetime.now(UTC) - timedelta(minutes=1)
