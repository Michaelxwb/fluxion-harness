"""任务列表/详情/Timeline 查询契约（B-108 / RULE-api-001）。

真实边界：真实 ASGI HTTP → 真实 PostgreSQL（Task/Event/children）。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from conftest import TenantContext
from helpers import create_schedule_payload, persist_task
from httpx import AsyncClient
from muad_agent_worker.application.task_events import TaskEventType, append_event
from muad_agent_worker.scheduler.service import ScheduleService


def _headers(tenant: TenantContext) -> dict[str, str]:
    return {"X-Tenant-Id": tenant.tenant_id}


async def _create_schedule(tenant: TenantContext) -> uuid.UUID:
    """schedule_id 有真实外键，必须建真实 Schedule 才能挂 Task。"""
    async with tenant.session_factory() as session:
        schedule = await ScheduleService(session, tenant.settings).create_schedule(
            tenant.tenant_id, create_schedule_payload(tenant)
        )
        await session.commit()
    return schedule.id


async def _append_events(
    tenant: TenantContext, task_id: uuid.UUID, event_types: list[str]
) -> None:
    async with tenant.session_factory() as session:
        async with session.begin():
            for name in event_types:
                await append_event(
                    session,
                    tenant_id=tenant.tenant_id,
                    task_id=task_id,
                    event_type=TaskEventType[name],
                )


async def test_detail_includes_timeline_in_seq_order(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """详情返回 Timeline，且按 seq 升序（不是按插入顺序碰运气）。"""
    task = await persist_task(tenant)
    await _append_events(tenant, task.id, ["CREATED", "CLAIMED", "RETRY", "COMPLETED"])

    detail = await client.get(f"/internal/tasks/{task.id}", headers=_headers(tenant))

    assert detail.status_code == 200, detail.text
    timeline = detail.json()["data"]["timeline"]
    assert [item["event_type"] for item in timeline] == [
        "CREATED",
        "CLAIMED",
        "RETRY",
        "COMPLETED",
    ]
    assert [item["seq"] for item in timeline] == [1, 2, 3, 4]


async def test_detail_includes_children_and_snapshot(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """详情包含子任务与冻结快照摘要。"""
    root_id = uuid.uuid4()
    parent = await persist_task(tenant, root_id=root_id, status="WAITING")
    child = await persist_task(
        tenant,
        root_id=root_id,
        parent_id=parent.id,
        item_key="item-1",
        status="COMPLETED",
    )

    detail = await client.get(f"/internal/tasks/{parent.id}", headers=_headers(tenant))

    assert detail.status_code == 200, detail.text
    data = detail.json()["data"]
    assert [item["task_id"] for item in data["children"]] == [str(child.id)]
    assert data["execution_snapshot"]["schema_version"] == 1
    assert data["snapshot_hash"].startswith("sha256:")


async def test_detail_is_tenant_isolated(client: AsyncClient, tenant: TenantContext) -> None:
    """跨租户查询视为不存在，不泄露存在性。"""
    task = await persist_task(tenant)

    foreign = await client.get(
        f"/internal/tasks/{task.id}", headers={"X-Tenant-Id": f"{tenant.tenant_id}-other"}
    )

    assert foreign.status_code == 404
    assert foreign.json()["code"] == "COMMON_NOT_FOUND"


async def test_unknown_task_detail_returns_404(client: AsyncClient, tenant: TenantContext) -> None:
    missing = await client.get(f"/internal/tasks/{uuid.uuid4()}", headers=_headers(tenant))
    assert missing.status_code == 404


async def test_list_filters_by_schedule_id(client: AsyncClient, tenant: TenantContext) -> None:
    """schedule_id 精确过滤。"""
    schedule_id = await _create_schedule(tenant)
    wanted = await persist_task(tenant, schedule_id=schedule_id)
    await persist_task(tenant, schedule_id=await _create_schedule(tenant))

    listing = await client.get(
        "/internal/tasks", params={"schedule_id": str(schedule_id)}, headers=_headers(tenant)
    )

    assert listing.status_code == 200, listing.text
    page = listing.json()["data"]
    assert page["total"] == 1
    assert page["items"][0]["task_id"] == str(wanted.id)


async def test_deadline_filter_is_independent_from_create_time(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """deadline_from/to 作用于 deadline_at；start_time/end_time 作用于 create_time，两者不混用。"""
    now = datetime.now(UTC)
    early_created_late_deadline = await persist_task(
        tenant,
        create_time=now - timedelta(hours=3),
        deadline_at=now + timedelta(hours=10),
    )
    late_created_soon_deadline = await persist_task(
        tenant,
        create_time=now - timedelta(hours=1),
        deadline_at=now + timedelta(hours=1),
    )

    by_deadline = await client.get(
        "/internal/tasks",
        params={"deadline_from": (now + timedelta(hours=5)).isoformat()},
        headers=_headers(tenant),
    )
    assert by_deadline.status_code == 200, by_deadline.text
    deadline_ids = [item["task_id"] for item in by_deadline.json()["data"]["items"]]
    assert deadline_ids == [str(early_created_late_deadline.id)]

    by_create = await client.get(
        "/internal/tasks",
        params={"start_time": (now - timedelta(hours=2)).isoformat()},
        headers=_headers(tenant),
    )
    assert by_create.status_code == 200, by_create.text
    create_ids = [item["task_id"] for item in by_create.json()["data"]["items"]]
    assert create_ids == [str(late_created_soon_deadline.id)]


async def test_deadline_filter_includes_endpoints(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """起止边界含端点。"""
    now = datetime.now(UTC)
    boundary = now + timedelta(hours=2)
    task = await persist_task(tenant, deadline_at=boundary)
    await persist_task(tenant, deadline_at=boundary + timedelta(hours=1))

    listing = await client.get(
        "/internal/tasks",
        params={"deadline_from": boundary.isoformat(), "deadline_to": boundary.isoformat()},
        headers=_headers(tenant),
    )

    assert listing.status_code == 200, listing.text
    assert [item["task_id"] for item in listing.json()["data"]["items"]] == [str(task.id)]


async def test_page_size_above_100_is_rejected(client: AsyncClient, tenant: TenantContext) -> None:
    over = await client.get("/internal/tasks", params={"page_size": 101}, headers=_headers(tenant))
    assert over.status_code == 422

    at_limit = await client.get(
        "/internal/tasks", params={"page_size": 100}, headers=_headers(tenant)
    )
    assert at_limit.status_code == 200


async def test_responses_carry_no_secrets(client: AsyncClient, tenant: TenantContext) -> None:
    """列表与详情响应都不携带密钥。"""
    task = await persist_task(tenant)

    detail = await client.get(f"/internal/tasks/{task.id}", headers=_headers(tenant))
    listing = await client.get("/internal/tasks", headers=_headers(tenant))

    for response in (detail, listing):
        body = response.text.lower()
        assert "api_key" not in body
        assert "bot_secret" not in body
        assert "authorization" not in body
