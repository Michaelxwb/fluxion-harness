"""B-128：Console Schedule 列表/详情/启停/删除 API（API-12 ~ API-16）。

真实边界：真实 Console HTTP/session → 真实 Worker Admin HTTP → 真实 PostgreSQL。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
from console_platform.conftest import TenantContext
from httpx import AsyncClient
from muad_agent_worker.infrastructure.db import get_session_factory as worker_session_factory
from muad_agent_worker.infrastructure.models.task import TaskExecution, TaskSchedule
from muad_agent_worker.main import app as worker_app
from sqlalchemy import text, update


async def _create_schedule(tenant_id: str, *, cron: str = "0 9 * * *") -> uuid.UUID:
    body = {
        "name": "weekday policy check",
        "agent_id": str(uuid.uuid4()),
        "actor_user_id": str(uuid.uuid4()),
        "intent_key": "policy_check",
        "skill_id": str(uuid.uuid4()),
        "input_template": {"customer": "A"},
        "schedule": {"type": "CRON", "cron": cron, "timezone": "Asia/Shanghai"},
        "delivery_route": {
            "channel": "WECOM",
            "bot_id": "bot-console",
            "external_user_id": "wotv-console",
        },
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=worker_app), base_url="http://worker"
    ) as worker:
        response = await worker.post(
            "/internal/schedules", json=body, headers={"X-Tenant-Id": tenant_id}
        )
    assert response.status_code == 200, response.text
    return uuid.UUID(response.json()["data"]["schedule_id"])


async def _set_status(schedule_id: uuid.UUID, status: str) -> None:
    async with worker_session_factory()() as session:
        async with session.begin():
            await session.execute(
                update(TaskSchedule)
                .where(TaskSchedule.id == schedule_id)
                .values(status=status)
            )


async def _seed_history_task(tenant_id: str, schedule_id: uuid.UUID) -> uuid.UUID:
    now = datetime.now(UTC)
    task_id = uuid.uuid4()
    async with worker_session_factory()() as session:
        async with session.begin():
            session.add(
                TaskExecution(
                    id=task_id,
                    tenant_id=tenant_id,
                    schedule_id=schedule_id,
                    agent_id=uuid.uuid4(),
                    actor_user_id=uuid.uuid4(),
                    intent_key="policy_check",
                    skill_id=uuid.uuid4(),
                    skill_artifact_id=uuid.uuid4(),
                    trigger_type="SCHEDULED",
                    execution_mode="ASYNC",
                    task_type="SKILL",
                    status="COMPLETED",
                    input_json={},
                    execution_snapshot_schema_version=1,
                    execution_snapshot_json={"schema_version": 1},
                    snapshot_hash="sha256:" + "e" * 64,
                    idempotency_key=f"console-schedule-{task_id}",
                    priority=100,
                    attempt=0,
                    max_attempts=3,
                    not_before=now,
                    deadline_at=now + timedelta(hours=1),
                    finished_at=now,
                    delivery_mode="NONE",
                    delivery_status="NONE",
                    delivery_key=f"task:{task_id}:final",
                    delivery_attempts=0,
                )
            )
    return task_id


async def test_b128_list_filters_completed_and_envelope(
    client: AsyncClient, task_tenant: TenantContext
) -> None:
    active_id = await _create_schedule(task_tenant.tenant_id)
    completed_id = await _create_schedule(task_tenant.tenant_id, cron="30 10 * * *")
    await _set_status(completed_id, "COMPLETED")
    headers = {"X-Tenant-Id": task_tenant.tenant_id}

    listed = await client.get("/api/v1/schedules", headers=headers)
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert {"code", "msg", "data", "trace_id", "request_id", "timestamp"} <= set(body)
    assert body["data"]["total"] == 2

    completed = await client.get(
        "/api/v1/schedules", headers=headers, params={"status": "COMPLETED"}
    )
    assert [item["schedule_id"] for item in completed.json()["data"]["items"]] == [
        str(completed_id)
    ]

    detail = await client.get(f"/api/v1/schedules/{active_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["data"]["schedule_id"] == str(active_id)
    assert detail.json()["data"]["status"] == "ACTIVE"

    missing = await client.get(f"/api/v1/schedules/{uuid.uuid4()}", headers=headers)
    assert missing.status_code == 404
    assert missing.json()["code"] == "COMMON_NOT_FOUND"


async def test_b128_pause_resume_and_terminal_conflict(
    client: AsyncClient, task_tenant: TenantContext
) -> None:
    schedule_id = await _create_schedule(task_tenant.tenant_id)
    headers = {"X-Tenant-Id": task_tenant.tenant_id}

    paused = await client.put(f"/api/v1/schedules/{schedule_id}/pause", headers=headers)
    assert paused.status_code == 200, paused.text
    assert paused.json()["data"]["status"] == "PAUSED"

    resumed = await client.put(f"/api/v1/schedules/{schedule_id}/resume", headers=headers)
    assert resumed.status_code == 200
    assert resumed.json()["data"]["status"] == "ACTIVE"
    assert resumed.json()["data"]["next_fire_at"] is not None

    terminal_id = await _create_schedule(task_tenant.tenant_id, cron="0 11 * * *")
    await _set_status(terminal_id, "COMPLETED")
    conflict = await client.put(f"/api/v1/schedules/{terminal_id}/pause", headers=headers)
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "REVISION_CONFLICT"

    unchanged = await client.get(f"/api/v1/schedules/{terminal_id}", headers=headers)
    assert unchanged.json()["data"]["status"] == "COMPLETED", "暂停失败不得改写原状态"


async def test_b128_delete_keeps_history_tasks(
    client: AsyncClient, task_tenant: TenantContext
) -> None:
    schedule_id = await _create_schedule(task_tenant.tenant_id)
    history_task = await _seed_history_task(task_tenant.tenant_id, schedule_id)
    headers = {"X-Tenant-Id": task_tenant.tenant_id}

    deleted = await client.delete(f"/api/v1/schedules/{schedule_id}", headers=headers)
    assert deleted.status_code == 200
    assert deleted.json()["data"] == {"schedule_id": str(schedule_id), "deleted": True}

    replay = await client.delete(f"/api/v1/schedules/{schedule_id}", headers=headers)
    assert replay.status_code == 200

    listed = await client.get("/api/v1/schedules", headers=headers)
    assert listed.json()["data"]["total"] == 0

    async with worker_session_factory()() as session:
        task = await session.get(TaskExecution, history_task)
    assert task is not None
    assert task.status == "COMPLETED", "删除 Schedule 不得取消历史 Task"
    assert task.schedule_id == schedule_id

    history = await client.get(
        "/api/v1/tasks", headers=headers, params={"schedule_id": str(schedule_id)}
    )
    assert [item["task_id"] for item in history.json()["data"]["items"]] == [str(history_task)]


async def test_b128_requires_authenticated_session(task_tenant: TenantContext) -> None:
    from httpx import ASGITransport

    from muad_console_platform.main import app as console_app

    transport = ASGITransport(app=console_app)
    async with AsyncClient(transport=transport, base_url="http://test") as anonymous:
        response = await anonymous.get("/api/v1/schedules")
    assert response.status_code == 401


async def test_b128_cross_tenant_schedule_hidden(
    client: AsyncClient, task_tenant: TenantContext
) -> None:
    foreign = await _create_schedule("test-console-foreign-schedule")
    headers = {"X-Tenant-Id": task_tenant.tenant_id}
    try:
        detail = await client.get(f"/api/v1/schedules/{foreign}", headers=headers)
        assert detail.status_code == 404
        paused = await client.put(f"/api/v1/schedules/{foreign}/pause", headers=headers)
        assert paused.status_code == 404
    finally:
        session_factory = worker_session_factory()
        async with session_factory() as session:
            await session.execute(
                text("DELETE FROM task.task_schedule WHERE tenant_id = :t"),
                {"t": "test-console-foreign-schedule"},
            )
            await session.execute(
                text("DELETE FROM task.delivery_route WHERE tenant_id = :t"),
                {"t": "test-console-foreign-schedule"},
            )
            await session.commit()
