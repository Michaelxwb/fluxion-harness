"""B-124：Worker Admin API 的权限域与契约（设计 API-17）。

真实边界：真实内部 HTTP（ASGI）→ Admin guard → TaskService → 真实 PostgreSQL；
浏览器不直接访问 Worker，缺内部身份一律 FORBIDDEN。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from muad_agent_worker.infrastructure.db import get_engine
from sqlalchemy import event

from agent_worker.conftest import TenantContext
from agent_worker.helpers import persist_task

INTERNAL_HEADER = "X-Internal-Service"
TENANT_HEADER = "X-Tenant-Id"
TOKEN = "test-internal-service-token"


@pytest.fixture(autouse=True)
def internal_service_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", TOKEN)


def _admin_headers(tenant_id: str) -> dict[str, str]:
    return {INTERNAL_HEADER: TOKEN, TENANT_HEADER: tenant_id}


async def test_b124_missing_internal_identity_is_forbidden(client: AsyncClient) -> None:
    response = await client.get("/internal/admin/tasks")
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"

    wrong = await client.get("/internal/admin/tasks", headers={INTERNAL_HEADER: "wrong"})
    assert wrong.status_code == 403


async def test_b124_admin_list_and_cancel_contract(
    client: AsyncClient, tenant: TenantContext
) -> None:
    first = await persist_task(tenant)
    second = await persist_task(tenant, status="QUEUED")
    headers = _admin_headers(tenant.tenant_id)

    listed = await client.get("/internal/admin/tasks", headers=headers)
    assert listed.status_code == 200, listed.text
    data = listed.json()["data"]
    assert data["total"] == 2
    assert {item["task_id"] for item in data["items"]} == {str(first.id), str(second.id)}
    assert {"page", "page_size", "total", "items"} <= set(data)

    detail = await client.get(f"/internal/admin/tasks/{first.id}", headers=headers)
    assert detail.status_code == 200, detail.text
    detail_data = detail.json()["data"]
    assert detail_data["task_id"] == str(first.id)
    assert detail_data["timeline"] == []
    assert detail_data["children"] == []
    assert detail_data["snapshot_hash"]

    cancelled = await client.post(f"/internal/admin/tasks/{first.id}/cancel", headers=headers)
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["data"] == {
        "task_id": str(first.id),
        "status": "CANCELLED",
        "cancel_requested": True,
    }
    assert "CANCELLING" not in cancelled.text

    replay = await client.post(f"/internal/admin/tasks/{first.id}/cancel", headers=headers)
    assert replay.json()["data"]["status"] == "CANCELLED"


async def test_b124_cross_tenant_access_is_not_found(
    client: AsyncClient, tenant: TenantContext
) -> None:
    task = await persist_task(tenant, finished_at=datetime.now(UTC), status="COMPLETED")
    headers = _admin_headers("test-other-tenant")

    detail = await client.get(f"/internal/admin/tasks/{task.id}", headers=headers)
    assert detail.status_code == 404
    assert detail.json()["code"] == "COMMON_NOT_FOUND"

    cancel = await client.post(f"/internal/admin/tasks/{task.id}/cancel", headers=headers)
    assert cancel.status_code == 404

    listed = await client.get("/internal/admin/tasks", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["data"]["total"] == 0


async def test_b124_admin_query_filters_match_internal_contract(
    client: AsyncClient, tenant: TenantContext
) -> None:
    scheduled = await persist_task(tenant, trigger_type="SCHEDULED")
    await persist_task(tenant, trigger_type="IMMEDIATE")
    headers = _admin_headers(tenant.tenant_id)

    filtered = await client.get(
        "/internal/admin/tasks", headers=headers, params={"trigger_type": "SCHEDULED"}
    )
    assert filtered.status_code == 200
    assert [item["task_id"] for item in filtered.json()["data"]["items"]] == [str(scheduled.id)]


async def test_admin_list_reports_child_progress_without_n_plus_one(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """列表页「子任务进度」：整页一次 `GROUP BY` 取回，查询数不随行数增长。

    N+1 守卫按语句形状无关的方式判：整请求触及 `task_execution` 的语句数有上界
    （分页 SELECT + COUNT + 子任务 GROUP BY = 3）。逐行统计会退化成 2 + N 行。
    """
    parent = await persist_task(tenant, task_type="BATCH", status="RUNNING")
    for item_key, status in (("a", "COMPLETED"), ("b", "FAILED"), ("c", "RUNNING")):
        await persist_task(
            tenant, parent_id=parent.id, root_id=parent.id, item_key=item_key, status=status
        )
    solo = await persist_task(tenant, status="QUEUED")
    headers = _admin_headers(tenant.tenant_id)

    statements: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany) -> None:
        statements.append(statement)

    engine = get_engine().sync_engine
    event.listen(engine, "before_cursor_execute", _record)
    try:
        listed = await client.get("/internal/admin/tasks", headers=headers)
    finally:
        event.remove(engine, "before_cursor_execute", _record)

    assert listed.status_code == 200, listed.text
    items = {item["task_id"]: item for item in listed.json()["data"]["items"]}
    assert items[str(parent.id)]["child_total"] == 3
    assert items[str(parent.id)]["child_finished"] == 2
    # 无子任务的行必须回 0/0，而不是缺键（前端 `undefined/total` 会渲染成 NaN）。
    assert items[str(solo.id)]["child_total"] == 0
    assert items[str(solo.id)]["child_finished"] == 0

    touching = [statement for statement in statements if "task_execution" in statement]
    assert len(touching) <= 3, statements
