"""Schedule 分页/详情与管理状态转换（B-110 / RULE-api-001）。

真实边界：真实 ASGI HTTP → ScheduleService → 真实 PostgreSQL CAS。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from conftest import TenantContext
from helpers import create_schedule_payload, persist_task
from httpx import AsyncClient


# Runtime 代表 Schedule owner 调用：Internal 变更接口必须带 X-Actor-User-Id（API-07/08）。
OWNER = uuid.UUID("5a1d0c1e-0000-4000-8000-00000000a001")


def _headers(tenant: TenantContext, **extra: str) -> dict[str, str]:
    return {"X-Tenant-Id": tenant.tenant_id, "X-Actor-User-Id": str(OWNER), **extra}


def _schedule_body(tenant: TenantContext, **overrides: object) -> dict[str, object]:
    body = create_schedule_payload(tenant, actor_user_id=OWNER).model_dump(mode="json")
    body.update(overrides)
    return body


async def _create(client: AsyncClient, tenant: TenantContext, **overrides: object) -> str:
    response = await client.post(
        "/internal/schedules", json=_schedule_body(tenant, **overrides), headers=_headers(tenant)
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["schedule_id"]


async def _set_status(tenant: TenantContext, schedule_id: str, status: str) -> None:
    async with tenant.session_factory() as session:
        await session.execute(
            sa.text("UPDATE task.task_schedule SET status = :status WHERE id = :id"),
            {"status": status, "id": schedule_id},
        )
        await session.commit()


async def test_list_returns_pagination_envelope(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """列表必须是分页封套，不是裸数组（裸数组会让前端 Page 解析成 undefined）。"""
    await _create(client, tenant)

    listing = await client.get("/internal/schedules", headers=_headers(tenant))

    assert listing.status_code == 200, listing.text
    page = listing.json()["data"]
    assert isinstance(page, dict), f"期望分页封套，得到 {type(page).__name__}"
    assert set(page) >= {"items", "page", "page_size", "total"}
    assert page["total"] == 1
    assert len(page["items"]) == 1


async def test_list_filters_by_status_including_terminal(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """状态筛选覆盖 COMPLETED 与 MISSED。"""
    active_id = await _create(client, tenant)
    completed_id = await _create(client, tenant)
    missed_id = await _create(client, tenant)
    await _set_status(tenant, completed_id, "COMPLETED")
    await _set_status(tenant, missed_id, "MISSED")

    completed = await client.get(
        "/internal/schedules", params={"status": "COMPLETED"}, headers=_headers(tenant)
    )
    assert completed.status_code == 200, completed.text
    assert [item["schedule_id"] for item in completed.json()["data"]["items"]] == [completed_id]

    missed = await client.get(
        "/internal/schedules", params={"status": "MISSED"}, headers=_headers(tenant)
    )
    assert missed.status_code == 200, missed.text
    assert [item["schedule_id"] for item in missed.json()["data"]["items"]] == [missed_id]

    active = await client.get(
        "/internal/schedules", params={"status": "ACTIVE"}, headers=_headers(tenant)
    )
    assert [item["schedule_id"] for item in active.json()["data"]["items"]] == [active_id]


async def test_list_filters_by_agent_id(client: AsyncClient, tenant: TenantContext) -> None:
    agent_id = uuid.uuid4()
    wanted = await _create(client, tenant, agent_id=str(agent_id))
    await _create(client, tenant)

    listing = await client.get(
        "/internal/schedules", params={"agent_id": agent_id}, headers=_headers(tenant)
    )

    assert listing.status_code == 200, listing.text
    assert [item["schedule_id"] for item in listing.json()["data"]["items"]] == [wanted]


async def test_list_paginates(client: AsyncClient, tenant: TenantContext) -> None:
    for _ in range(3):
        await _create(client, tenant)

    first = await client.get(
        "/internal/schedules", params={"page": 1, "page_size": 2}, headers=_headers(tenant)
    )
    assert first.status_code == 200, first.text
    assert first.json()["data"]["total"] == 3
    assert len(first.json()["data"]["items"]) == 2

    over = await client.get(
        "/internal/schedules", params={"page_size": 101}, headers=_headers(tenant)
    )
    assert over.status_code == 422


async def test_detail_returns_schedule_and_is_tenant_isolated(
    client: AsyncClient, tenant: TenantContext
) -> None:
    schedule_id = await _create(client, tenant)

    detail = await client.get(f"/internal/schedules/{schedule_id}", headers=_headers(tenant))
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["schedule_id"] == schedule_id

    foreign = await client.get(
        f"/internal/schedules/{schedule_id}",
        headers={"X-Tenant-Id": f"{tenant.tenant_id}-other"},
    )
    assert foreign.status_code == 404

    unknown = await client.get(f"/internal/schedules/{uuid.uuid4()}", headers=_headers(tenant))
    assert unknown.status_code == 404


async def test_repeated_pause_is_idempotent(client: AsyncClient, tenant: TenantContext) -> None:
    """已 PAUSED 再次暂停幂等返回，不报冲突。"""
    schedule_id = await _create(client, tenant)

    first = await client.put(f"/internal/schedules/{schedule_id}/pause", headers=_headers(tenant))
    second = await client.put(f"/internal/schedules/{schedule_id}/pause", headers=_headers(tenant))

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert second.json()["data"]["status"] == "PAUSED"


async def test_terminal_schedule_rejects_pause_and_resume(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """COMPLETED / MISSED 为终态，暂停与恢复都返回 REVISION_CONFLICT。"""
    for status in ("COMPLETED", "MISSED"):
        schedule_id = await _create(client, tenant)
        await _set_status(tenant, schedule_id, status)

        paused = await client.put(
            f"/internal/schedules/{schedule_id}/pause", headers=_headers(tenant)
        )
        assert paused.status_code == 409, f"{status} pause: {paused.text}"
        assert paused.json()["code"] == "REVISION_CONFLICT"

        resumed = await client.put(
            f"/internal/schedules/{schedule_id}/resume", headers=_headers(tenant)
        )
        assert resumed.status_code == 409, f"{status} resume: {resumed.text}"
        assert resumed.json()["code"] == "REVISION_CONFLICT"


async def test_resume_recomputes_next_fire_at_without_backfill(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """恢复按当前时间重算 next_fire_at；错过的触发不补发（不落在过去）。"""
    schedule_id = await _create(client, tenant)
    await client.put(f"/internal/schedules/{schedule_id}/pause", headers=_headers(tenant))

    stale = datetime.now(UTC) - timedelta(days=3)
    async with tenant.session_factory() as session:
        await session.execute(
            sa.text(
                "UPDATE task.task_schedule SET status = 'PAUSED', next_fire_at = :stale WHERE id = :id"
            ),
            {"stale": stale, "id": schedule_id},
        )
        await session.commit()

    resumed = await client.put(
        f"/internal/schedules/{schedule_id}/resume", headers=_headers(tenant)
    )

    assert resumed.status_code == 200, resumed.text
    next_fire = datetime.fromisoformat(resumed.json()["data"]["next_fire_at"])
    assert next_fire > datetime.now(UTC), f"恢复后 next_fire_at 仍在过去: {next_fire}"


async def test_repeated_delete_is_idempotent(client: AsyncClient, tenant: TenantContext) -> None:
    """已删除再次删除幂等返回成功。"""
    schedule_id = await _create(client, tenant)

    first = await client.delete(f"/internal/schedules/{schedule_id}", headers=_headers(tenant))
    second = await client.delete(f"/internal/schedules/{schedule_id}", headers=_headers(tenant))

    assert first.status_code == 200, first.text
    assert second.status_code == 200, f"重复删除应幂等，得到 {second.status_code}"


async def test_delete_does_not_remove_existing_tasks(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """删除 Schedule 不影响已创建的 Task。"""
    schedule_id = await _create(client, tenant)
    task = await persist_task(tenant, schedule_id=uuid.UUID(schedule_id), actor_user_id=OWNER)

    deleted = await client.delete(f"/internal/schedules/{schedule_id}", headers=_headers(tenant))
    assert deleted.status_code == 200, deleted.text

    detail = await client.get(f"/internal/tasks/{task.id}", headers=_headers(tenant))
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["task_id"] == str(task.id)
