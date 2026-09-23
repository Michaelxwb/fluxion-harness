"""B-127：Console Task 列表/详情/取消 API（API-09/10/11）。

真实边界：真实 Console HTTP/session（认证 + CSRF）→ 真实 Worker Admin HTTP
→ 真实 PostgreSQL；Console 不直连 task schema。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from console_platform.conftest import TenantContext
from httpx import ASGITransport, AsyncClient
from muad_agent_worker.infrastructure.db import get_session_factory as worker_session_factory
from muad_agent_worker.infrastructure.models.task import TaskExecution
from muad_console_platform.main import app as console_app
from sqlalchemy import text


async def _seed_task(
    tenant_id: str,
    *,
    status: str = "QUEUED",
    trigger_type: str = "IMMEDIATE",
    deadline_at: datetime | None = None,
    finished_at: datetime | None = None,
) -> uuid.UUID:
    now = datetime.now(UTC)
    task_id = uuid.uuid4()
    async with worker_session_factory()() as session:
        async with session.begin():
            session.add(
                TaskExecution(
                    id=task_id,
                    tenant_id=tenant_id,
                    agent_id=uuid.uuid4(),
                    actor_user_id=uuid.uuid4(),
                    intent_key="policy_check",
                    skill_id=uuid.uuid4(),
                    skill_artifact_id=uuid.uuid4(),
                    trigger_type=trigger_type,
                    execution_mode="ASYNC",
                    task_type="SKILL",
                    status=status,
                    input_json={},
                    execution_snapshot_schema_version=1,
                    execution_snapshot_json={"schema_version": 1},
                    snapshot_hash="sha256:" + "d" * 64,
                    idempotency_key=f"console-api-{task_id}",
                    priority=100,
                    attempt=0,
                    max_attempts=3,
                    not_before=now,
                    deadline_at=deadline_at or (now + timedelta(hours=1)),
                    finished_at=finished_at,
                    delivery_mode="NONE",
                    delivery_status="NONE",
                    delivery_key=f"task:{task_id}:final",
                    delivery_attempts=0,
                )
            )
    return task_id


async def test_b127_requires_authenticated_session() -> None:
    transport = ASGITransport(app=console_app)
    async with AsyncClient(transport=transport, base_url="http://test") as anonymous:
        response = await anonymous.get("/api/v1/tasks")
    assert response.status_code == 401


async def test_b127_list_detail_and_envelope(
    client: AsyncClient, task_tenant: TenantContext
) -> None:
    first = await _seed_task(task_tenant.tenant_id)
    second = await _seed_task(task_tenant.tenant_id, status="COMPLETED", finished_at=datetime.now(UTC))

    listed = await client.get(
        "/api/v1/tasks", headers={"X-Tenant-Id": task_tenant.tenant_id}
    )
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert {"code", "msg", "data", "trace_id", "request_id", "timestamp"} <= set(body)
    assert body["data"]["total"] == 2
    ids = {item["task_id"] for item in body["data"]["items"]}
    assert ids == {str(first), str(second)}

    detail = await client.get(
        f"/api/v1/tasks/{first}", headers={"X-Tenant-Id": task_tenant.tenant_id}
    )
    assert detail.status_code == 200
    assert detail.json()["data"]["task_id"] == str(first)
    assert detail.json()["data"]["timeline"] == []
    assert detail.json()["data"]["children"] == []

    missing = await client.get(
        f"/api/v1/tasks/{uuid.uuid4()}", headers={"X-Tenant-Id": task_tenant.tenant_id}
    )
    assert missing.status_code == 404
    assert missing.json()["code"] == "COMMON_NOT_FOUND"


async def test_b127_filters_match_worker_contract(
    client: AsyncClient, task_tenant: TenantContext
) -> None:
    scheduled = await _seed_task(task_tenant.tenant_id, trigger_type="SCHEDULED")
    await _seed_task(task_tenant.tenant_id, trigger_type="IMMEDIATE")
    expired = await _seed_task(
        task_tenant.tenant_id,
        deadline_at=datetime.now(UTC) + timedelta(hours=10),
    )
    headers = {"X-Tenant-Id": task_tenant.tenant_id}

    by_trigger = await client.get(
        "/api/v1/tasks", headers=headers, params={"trigger_type": "SCHEDULED"}
    )
    assert [item["task_id"] for item in by_trigger.json()["data"]["items"]] == [str(scheduled)]

    by_deadline = await client.get(
        "/api/v1/tasks",
        headers=headers,
        params={"deadline_from": (datetime.now(UTC) + timedelta(hours=5)).isoformat()},
    )
    assert [item["task_id"] for item in by_deadline.json()["data"]["items"]] == [str(expired)]

    by_status = await client.get("/api/v1/tasks", headers=headers, params={"status": "COMPLETED"})
    assert by_status.json()["data"]["total"] == 0


async def test_b127_cancel_and_conflict_not_faked(
    client: AsyncClient, task_tenant: TenantContext
) -> None:
    queued = await _seed_task(task_tenant.tenant_id)
    completed = await _seed_task(
        task_tenant.tenant_id, status="COMPLETED", finished_at=datetime.now(UTC)
    )
    headers = {"X-Tenant-Id": task_tenant.tenant_id}

    cancelled = await client.post(f"/api/v1/tasks/{queued}/cancel", headers=headers)
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["data"] == {
        "task_id": str(queued),
        "status": "CANCELLED",
        "cancel_requested": True,
    }

    conflict = await client.post(f"/api/v1/tasks/{completed}/cancel", headers=headers)
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "REVISION_CONFLICT"
    assert "CANCELLED" not in conflict.text or "COMPLETED" in conflict.text

    refreshed = await client.get(f"/api/v1/tasks/{completed}", headers=headers)
    assert refreshed.json()["data"]["status"] == "COMPLETED", "冲突不得改写终态"


async def test_b127_cross_tenant_task_is_not_visible(
    client: AsyncClient, task_tenant: TenantContext
) -> None:
    foreign = await _seed_task("test-console-foreign-tenant")
    headers = {"X-Tenant-Id": task_tenant.tenant_id}
    try:
        detail = await client.get(f"/api/v1/tasks/{foreign}", headers=headers)
        assert detail.status_code == 404
        cancel = await client.post(f"/api/v1/tasks/{foreign}/cancel", headers=headers)
        assert cancel.status_code == 404
    finally:
        session_factory = worker_session_factory()
        async with session_factory() as session:
            await session.execute(
                text("DELETE FROM task.task_execution WHERE tenant_id = :t"),
                {"t": "test-console-foreign-tenant"},
            )
            await session.commit()


async def test_b127_csrf_required_for_cancel(
    client: AsyncClient, task_tenant: TenantContext
) -> None:
    task_id = await _seed_task(task_tenant.tenant_id)
    headers = {"X-Tenant-Id": task_tenant.tenant_id}
    cookie_token = client.cookies.get("muad_csrf")
    assert cookie_token

    transport = ASGITransport(app=console_app)
    async with AsyncClient(transport=transport, base_url="http://test") as hijacked:
        hijacked.cookies.set("muad_session", str(client.cookies.get("muad_session")))
        hijacked.cookies.set("muad_csrf", cookie_token)
        response = await hijacked.post(f"/api/v1/tasks/{task_id}/cancel", headers=headers)
    assert response.status_code == 403


async def test_b127_forged_tenant_header_cannot_reach_other_tenant(
    client: AsyncClient, task_tenant: TenantContext
) -> None:
    """租户取自登录账号：改写 X-Tenant-Id 也看不到/取消不了别的租户的 Task。"""
    foreign_tenant = "test-console-forged-tenant"
    foreign = await _seed_task(foreign_tenant)
    own = await _seed_task(task_tenant.tenant_id)
    headers = {"X-Tenant-Id": foreign_tenant}
    try:
        detail = await client.get(f"/api/v1/tasks/{foreign}", headers=headers)
        cancel = await client.post(f"/api/v1/tasks/{foreign}/cancel", headers=headers)
        listing = await client.get("/api/v1/tasks", headers=headers)
    finally:
        async with worker_session_factory()() as session:
            await session.execute(
                text("DELETE FROM task.task_event WHERE tenant_id = :t"), {"t": foreign_tenant}
            )
            await session.execute(
                text("DELETE FROM task.task_execution WHERE tenant_id = :t"), {"t": foreign_tenant}
            )
            await session.commit()

    assert detail.status_code == 404
    assert cancel.status_code == 404
    assert [item["task_id"] for item in listing.json()["data"]["items"]] == [str(own)]
