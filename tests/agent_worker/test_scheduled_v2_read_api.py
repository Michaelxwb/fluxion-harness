"""定时任务读面（scheduled-task-v2 TASK-023 / API-07/08/09 + 任务中心读面）。

真实边界：真实 ASGI HTTP → 真实 PostgreSQL。断言只关心"读出来的事实"：
可见范围、Revision 投影、Fire 与 DST 事件分开、等待载荷的可见性、产物版本与分页。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from muad_agent_worker.infrastructure.db import get_session_factory
from muad_agent_worker.infrastructure.models.task import (
    ScheduleFire,
    TaskExecution,
    TaskHumanInteraction,
    TaskSchedule,
    TaskScheduleRevision,
)

from agent_worker.conftest import TenantContext
from agent_worker.helpers import internal_service_headers

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
HASH = "sha256:" + "a" * 64
OWNER = uuid.uuid4()
AGENT = uuid.uuid4()


def headers(
    tenant: TenantContext,
    *,
    actor: uuid.UUID = OWNER,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    return internal_service_headers(
        tenant.tenant_id, **{"X-Actor-User-Id": str(actor), **(extra or {})}
    )


async def seed_schedule(
    tenant: TenantContext,
    *,
    status: str = "ACTIVE",
    mode: str = "AGENTIC",
    delivery_intent: str = "NONE",
    owner: uuid.UUID = OWNER,
    agent: uuid.UUID = AGENT,
    attention_reason: str | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    schedule_id, revision_id = uuid.uuid4(), uuid.uuid4()
    spec: dict[str, Any] = {
        "mode": "AGENTIC",
        "goal": "汇总客户数据",
        "tool_allowlist": ["MCP:crm:customer.report"],
        "budget": {"max_steps": 12, "max_tool_calls": 20, "max_tokens": 0},
        "memory_mode": "READ_ONLY",
    }
    if mode == "REMINDER":
        spec = {"mode": "REMINDER", "reminder_text": "开会"}
    elif mode == "SKILL":
        spec = {"mode": "SKILL", "skill_id": str(uuid.uuid4()), "input_template": {}}
    async with tenant.session_factory() as session, session.begin():
        schedule = TaskSchedule(
            id=schedule_id,
            tenant_id=tenant.tenant_id,
            owner_user_id=owner,
            agent_id=agent,
            name="A 客户周报",
            status=status,
            config_version=3,
            next_fire_at=NOW + timedelta(days=1),
            attention_reason=attention_reason,
            attention_since=NOW if attention_reason else None,
            created_by=owner,
        )
        session.add(schedule)
        await session.flush()
        session.add(
            TaskScheduleRevision(
                id=revision_id,
                tenant_id=tenant.tenant_id,
                schedule_id=schedule_id,
                revision_no=2,
                mode=mode,
                instruction_raw="每周一给 A 客户发周报",
                normalized_goal="汇总 A 客户上周数据",
                recurrence_kind="CRON",
                cron_expr="0 9 * * 1",
                timezone="Asia/Shanghai",
                schedule_policy={"concurrency_policy": "QUEUE"},
                context_json={"parameters": {"customer": "A"}, "output": {"format": "markdown"}},
                execution_spec=spec,
                delivery_intent=delivery_intent,
                preflight_json={},
                created_by=owner,
            )
        )
        await session.flush()
        schedule.current_revision_id = revision_id
    return schedule_id, revision_id


async def seed_fire(
    tenant: TenantContext, schedule_id: uuid.UUID, revision_id: uuid.UUID, *, at: datetime
) -> uuid.UUID:
    fire_id = uuid.uuid4()
    async with tenant.session_factory() as session, session.begin():
        session.add(
            ScheduleFire(
                id=fire_id,
                tenant_id=tenant.tenant_id,
                schedule_id=schedule_id,
                schedule_revision_id=revision_id,
                scheduled_at=at,
                status="SKIPPED",
                skip_reason="MISFIRE",
                missed_count=2,
            )
        )
    return fire_id


async def seed_execution(
    tenant: TenantContext,
    schedule_id: uuid.UUID,
    revision_id: uuid.UUID,
    *,
    status: str = "QUEUED",
    wait_kind: str | None = None,
    wait_payload: dict[str, Any] | None = None,
) -> uuid.UUID:
    execution_id = uuid.uuid4()
    async with tenant.session_factory() as session, session.begin():
        session.add(
            TaskExecution(
                id=execution_id,
                tenant_id=tenant.tenant_id,
                schedule_id=schedule_id,
                schedule_revision_id=revision_id,
                scheduled_at=NOW,
                agent_id=AGENT,
                actor_user_id=OWNER,
                task_type="AGENTIC",
                trigger_type="SCHEDULED",
                status=status,
                wait_kind=wait_kind,
                wait_payload_json=wait_payload,
                input_json={},
                execution_snapshot_json={"schema_version": 4},
                snapshot_hash=HASH,
                snapshot_bytes=2048,
                idempotency_key=f"k-{execution_id}",
            )
        )
    return execution_id


async def seed_interaction(
    tenant: TenantContext, execution_id: uuid.UUID, *, status: str = "OPEN"
) -> uuid.UUID:
    interaction_id = uuid.uuid4()
    async with tenant.session_factory() as session, session.begin():
        session.add(
            TaskHumanInteraction(
                id=interaction_id,
                tenant_id=tenant.tenant_id,
                execution_id=execution_id,
                owner_user_id=OWNER,
                agent_id=AGENT,
                interaction_type="INPUT",
                status=status,
                question="请选择客户",
                input_schema={"type": "object"},
                deadline_at=NOW + timedelta(hours=1),
            )
        )
    return interaction_id


async def test_detail_projects_the_current_revision(tenant: TenantContext, client: AsyncClient) -> None:
    schedule_id, _ = await seed_schedule(tenant)

    response = await client.get(f"/internal/schedules/{schedule_id}", headers=headers(tenant))

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["revision_no"] == 2
    assert data["mode"] == "AGENTIC"
    assert data["timezone"] == "Asia/Shanghai"
    assert data["config_version"] == 3
    assert data["dependencies"] == ["MCP:crm:customer.report"]
    assert data["budget"] == {"max_steps": 12, "max_tool_calls": 20, "max_tokens": 0}
    assert data["memory_mode"] == "READ_ONLY"
    assert data["delivery_intent"] == "NONE"
    assert data["policy"]["concurrency_policy"] == "QUEUE"
    assert data["context"]["parameters"] == {"customer": "A"}


async def test_list_reports_mode_last_fire_and_pending_human(
    tenant: TenantContext, client: AsyncClient
) -> None:
    schedule_id, revision_id = await seed_schedule(tenant)
    await seed_fire(tenant, schedule_id, revision_id, at=NOW)
    execution_id = await seed_execution(
        tenant, schedule_id, revision_id, status="WAITING_HUMAN", wait_kind=None
    )
    await seed_interaction(tenant, execution_id)

    response = await client.get("/internal/schedules", headers=headers(tenant))

    assert response.status_code == 200, response.text
    item = response.json()["data"]["items"][0]
    assert item["mode"] == "AGENTIC"
    assert item["timezone"] == "Asia/Shanghai"
    assert item["last_fire"]["status"] == "SKIPPED"
    assert item["last_fire"]["missed_count"] == 2
    assert item["pending_human"] is True
    assert item["needs_attention"] is False


async def test_fire_history_keeps_dst_events_apart_from_fires(
    tenant: TenantContext, client: AsyncClient
) -> None:
    schedule_id, revision_id = await seed_schedule(tenant)
    await seed_fire(tenant, schedule_id, revision_id, at=NOW)
    async with get_session_factory()() as session, session.begin():
        await session.execute(
            sa.text(
                "INSERT INTO task.task_event "
                "(tenant_id, entity_type, entity_id, seq, event_type, payload_json, occurred_at) "
                "VALUES (:tenant, 'SCHEDULE', :schedule, 1, 'DST_GAP_SKIPPED', "
                "CAST(:payload AS jsonb), :at)"
            ),
            {
                "tenant": tenant.tenant_id,
                "schedule": schedule_id,
                "payload": '{"local_date": "2026-03-08", "local_time": "02:30:00"}',
                "at": NOW,
            },
        )

    response = await client.get(
        f"/internal/schedules/{schedule_id}/fires", headers=headers(tenant)
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert [item["status"] for item in data["items"]] == ["SKIPPED"]
    assert [event["event_type"] for event in data["dst_events"]] == ["DST_GAP_SKIPPED"]
    assert data["dst_events"][0]["local_date"] == "2026-03-08"


async def test_execution_detail_hides_wait_payload_outside_the_wait(
    tenant: TenantContext, client: AsyncClient
) -> None:
    schedule_id, revision_id = await seed_schedule(tenant)
    waiting = await seed_execution(
        tenant,
        schedule_id,
        revision_id,
        status="WAITING_EXTERNAL",
        wait_kind="ASYNC_OPERATION",
        wait_payload={
            "operations": [
                {"operation_id": str(uuid.uuid4()), "call_id": "c1", "status": "SUBMITTED"}
            ]
        },
    )
    resumed = await seed_execution(
        tenant,
        schedule_id,
        revision_id,
        status="QUEUED",
        wait_kind=None,
        wait_payload={"human_interaction_id": str(uuid.uuid4())},
    )

    shown = await client.get(f"/internal/executions/{waiting}", headers=headers(tenant))
    masked = await client.get(f"/internal/executions/{resumed}", headers=headers(tenant))

    assert shown.status_code == 200, shown.text
    assert shown.json()["data"]["wait_kind"] == "ASYNC_OPERATION"
    assert shown.json()["data"]["wait_payload"]["operations"]
    assert masked.status_code == 200, masked.text
    assert masked.json()["data"]["wait_kind"] is None
    assert masked.json()["data"]["wait_payload"] is None


async def test_execution_detail_lists_output_artifacts_with_version_and_checksum(
    tenant: TenantContext, client: AsyncClient
) -> None:
    schedule_id, revision_id = await seed_schedule(tenant)
    execution_id = await seed_execution(tenant, schedule_id, revision_id, status="COMPLETED")
    artifact_id = uuid.uuid4()
    async with get_session_factory()() as session, session.begin():
        await session.execute(
            sa.text(
                "INSERT INTO runtime.artifact "
                "(id, tenant_id, run_id, task_id, artifact_type, storage_key, media_type, size, "
                " checksum, metadata_json) "
                "VALUES (:id, :tenant, NULL, :task, 'OUTPUT', :key, 'text/markdown', 12, "
                " :checksum, CAST(:metadata AS jsonb))"
            ),
            {
                "id": artifact_id,
                "tenant": tenant.tenant_id,
                "task": execution_id,
                "key": f"tasks/{tenant.tenant_id}/{execution_id}/{artifact_id}/v1",
                "checksum": HASH,
                "metadata": '{"filename": "report.md", "version": 1, "versions": []}',
            },
        )

    response = await client.get(f"/internal/executions/{execution_id}", headers=headers(tenant))

    assert response.status_code == 200, response.text
    artifacts = response.json()["data"]["result_artifacts"]
    assert [(item["name"], item["version"], item["checksum"]) for item in artifacts] == [
        ("report.md", "1", HASH)
    ]


async def test_pending_mixes_human_and_attention_with_paging(
    tenant: TenantContext, client: AsyncClient
) -> None:
    schedule_id, revision_id = await seed_schedule(
        tenant, status="NEEDS_ATTENTION", attention_reason="TOOL_MISSING"
    )
    execution_id = await seed_execution(tenant, schedule_id, revision_id, status="WAITING_HUMAN")
    await seed_interaction(tenant, execution_id)

    response = await client.get(
        "/internal/task-center/pending", headers=headers(tenant), params={"page_size": 10}
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    kinds = sorted(item["kind"] for item in data["items"])
    assert kinds == ["HUMAN", "NEEDS_ATTENTION"]
    assert data["total"] == 2

    humans_only = await client.get(
        "/internal/task-center/pending", headers=headers(tenant), params={"kind": "HUMAN"}
    )
    assert [item["kind"] for item in humans_only.json()["data"]["items"]] == ["HUMAN"]

    badges = await client.get("/internal/task-center/badges", headers=headers(tenant))
    assert badges.json()["data"] == {"pending_human": 1, "needs_attention": 1}


async def test_read_surface_is_tenant_and_owner_scoped(
    tenant: TenantContext, client: AsyncClient
) -> None:
    schedule_id, _ = await seed_schedule(tenant)

    other_actor = await client.get(
        f"/internal/schedules/{schedule_id}", headers=headers(tenant, actor=uuid.uuid4())
    )
    other_tenant = await client.get(
        f"/internal/schedules/{schedule_id}", headers=headers(tenant, extra={"X-Tenant-Id": "other"})
    )

    assert other_actor.status_code == 404
    assert other_tenant.status_code in (400, 404)


async def test_page_size_above_the_cap_is_rejected(tenant: TenantContext, client: AsyncClient) -> None:
    over = await client.get(
        "/internal/schedules", headers=headers(tenant), params={"page_size": 101}
    )
    assert over.status_code == 422
