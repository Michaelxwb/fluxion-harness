"""TASK-029：Console 的 v2 Schedule/Task 读面与运营动作（S-07/S-13/E-12/B-01/B-10）。

真实边界：真实 Console HTTP/session（认证 + CSRF）→ 真实 Worker ASGI（内部 API-04..09）
→ 真实 PostgreSQL。断言只关心"Console 读出来的事实"：Worker 真值原样投影、时刻带 offset、
Fire 与 DST 事件分开、快照不含内部存储键、动作只经单一入口。

种子直接落 v2 行（`console_tasks.helpers`）：Draft→确认的创建链由 Worker 套件覆盖，
Console 不提供创建入口（E-12）。
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from console_platform.conftest import TenantContext
from httpx import AsyncClient
from muad_agent_worker.infrastructure.db import get_session_factory
from muad_agent_worker.infrastructure.models.task import TaskExecution, TaskHumanInteraction
from muad_agent_worker.main import app as worker_app
from muad_console_platform.infrastructure.db import (
    get_session_factory as console_session_factory,
)
from muad_console_platform.infrastructure.models.control import AgentDefinition, PlatformUser
from muad_contracts import ScheduleMode

from console_tasks.helpers import (
    seed_execution,
    seed_execution_event,
    seed_fire,
    seed_human,
    seed_schedule,
    seed_schedule_event,
)

pytestmark = pytest.mark.integration

TZ = "Asia/Shanghai"
#: `format_console_time` 的出参形态：`YYYY-MM-DD HH:mm:ss+HH:MM`（必须带 offset）。
CONSOLE_TIME = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$")


def _headers(tenant: TenantContext) -> dict[str, str]:
    return {"X-Tenant-Id": tenant.tenant_id}


async def _execution_row(execution_id: uuid.UUID) -> tuple[str, bool]:
    async with get_session_factory()() as session:
        row = (
            await session.execute(
                sa.select(TaskExecution.status, TaskExecution.cancel_requested).where(
                    TaskExecution.id == execution_id
                )
            )
        ).one()
    return str(row[0]), bool(row[1])


async def test_console_list_detail_carry_worker_v2_facts(
    client: AsyncClient, task_tenant: TenantContext
) -> None:
    agent_id = uuid.uuid4()
    schedule_id, _ = await seed_schedule(
        task_tenant.tenant_id, agent_id=agent_id, attention_reason=None
    )
    ended_id, _ = await seed_schedule(
        task_tenant.tenant_id, name="已结束", status="ENDED", end_reason="COMPLETED"
    )
    headers = _headers(task_tenant)

    listed = await client.get("/api/v1/schedules", headers=headers)
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert {"code", "msg", "data", "trace_id", "request_id", "timestamp"} <= set(body)
    assert body["data"]["total"] == 2
    item = next(i for i in body["data"]["items"] if i["schedule_id"] == str(schedule_id))
    assert item["mode"] == "AGENTIC"
    assert item["status"] == "ACTIVE"
    assert item["timezone"] == TZ
    assert item["config_version"] == 3
    assert item["needs_attention"] is False
    assert item["pending_human"] is False
    assert CONSOLE_TIME.match(item["next_fire_at"]), item["next_fire_at"]

    ended = await client.get(
        "/api/v1/schedules", headers=headers, params={"status": "ENDED"}
    )
    assert [i["schedule_id"] for i in ended.json()["data"]["items"]] == [str(ended_id)]

    detail = await client.get(f"/api/v1/schedules/{schedule_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    data = detail.json()["data"]
    assert data["revision_no"] == 2
    assert data["dependencies"] == ["MCP:crm:customer.report"]
    assert data["delivery_intent"] == "NONE"
    assert data["payload"]["kind"] == "AGENT_TURN"
    assert data["session_target"] == "isolated"
    assert data["tools_allow"] == ["MCP:crm:customer.report"]
    assert data["budget_source"] == "PLATFORM"
    assert data["budget"] is None
    assert data["memory_mode"] == "READ_ONLY"
    assert data["policy"]["concurrency_policy"] == "QUEUE"
    assert data["end_reason"] is None

    ended_detail = await client.get(f"/api/v1/schedules/{ended_id}", headers=headers)
    assert ended_detail.json()["data"]["end_reason"] == "COMPLETED"


async def test_console_list_enriches_agent_name_without_n_plus_one(
    client: AsyncClient, task_tenant: TenantContext
) -> None:
    agent_id = uuid.uuid4()
    async with console_session_factory()() as session:
        session.add(
            AgentDefinition(
                id=agent_id,
                tenant_id=task_tenant.tenant_id,
                key=f"v2-agent-{agent_id.hex[:8]}",
                name="周报 Agent",
                description=None,
                instructions="",
                model_id=task_tenant.model_id,
            )
        )
        await session.commit()

    named, _ = await seed_schedule(task_tenant.tenant_id, agent_id=agent_id)
    unnamed, _ = await seed_schedule(task_tenant.tenant_id, name="无登记 Agent")

    listed = await client.get("/api/v1/schedules", headers=_headers(task_tenant))
    assert listed.status_code == 200, listed.text
    items = {i["schedule_id"]: i for i in listed.json()["data"]["items"]}
    assert items[str(named)]["agent_name"] == "周报 Agent"
    assert items[str(unnamed)]["agent_name"] is None


async def test_console_list_enriches_actor_name_and_filters_by_user(
    client: AsyncClient, task_tenant: TenantContext
) -> None:
    owner_id = uuid.uuid4()
    other_id = uuid.uuid4()
    async with console_session_factory()() as session:
        session.add_all(
            [
                PlatformUser(
                    id=owner_id,
                    tenant_id=task_tenant.tenant_id,
                    user_code=f"v2-user-{owner_id.hex[:8]}",
                    display_name="张三",
                ),
                PlatformUser(
                    id=other_id,
                    tenant_id=task_tenant.tenant_id,
                    user_code=f"v2-user-{other_id.hex[:8]}",
                    display_name="李四",
                ),
            ]
        )
        await session.commit()

    mine, _ = await seed_schedule(task_tenant.tenant_id, owner_user_id=owner_id)
    other, _ = await seed_schedule(
        task_tenant.tenant_id, name="别人的任务", owner_user_id=other_id
    )

    listed = await client.get("/api/v1/schedules", headers=_headers(task_tenant))
    assert listed.status_code == 200, listed.text
    items = {i["schedule_id"]: i for i in listed.json()["data"]["items"]}
    assert items[str(mine)]["actor_user_id"] == str(owner_id)
    assert items[str(mine)]["actor_name"] == "张三"
    assert items[str(other)]["actor_name"] == "李四"

    filtered = await client.get(
        "/api/v1/schedules",
        headers=_headers(task_tenant),
        params={"actor_user_id": str(owner_id)},
    )
    assert filtered.status_code == 200, filtered.text
    assert [i["schedule_id"] for i in filtered.json()["data"]["items"]] == [str(mine)]

    detail = await client.get(f"/api/v1/schedules/{mine}", headers=_headers(task_tenant))
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["actor_name"] == "张三"


async def test_console_fire_history_keeps_dst_events_separate(
    client: AsyncClient, task_tenant: TenantContext
) -> None:
    schedule_id, revision_id = await seed_schedule(task_tenant.tenant_id)
    now = datetime(2026, 11, 16, 1, 0, tzinfo=UTC)
    execution_id = await seed_execution(
        task_tenant.tenant_id, schedule_id=schedule_id, status="COMPLETED", finished_at=now
    )
    await seed_fire(
        task_tenant.tenant_id,
        schedule_id,
        revision_id,
        scheduled_at=now,
        status="EXECUTION_CREATED",
        execution_id=execution_id,
    )
    await seed_fire(
        task_tenant.tenant_id,
        schedule_id,
        revision_id,
        scheduled_at=now - timedelta(days=1),
        status="SKIPPED",
        skip_reason="OVERLAP",
        missed_count=2,
    )
    await seed_schedule_event(
        task_tenant.tenant_id,
        schedule_id,
        event_type="DST_GAP_SKIPPED",
        payload={"local_date": "2026-03-08", "local_time": "02:30"},
        occurred_at=now - timedelta(hours=12),
    )

    response = await client.get(
        f"/api/v1/schedules/{schedule_id}/fires", headers=_headers(task_tenant)
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["total"] == 2
    assert [item["status"] for item in data["items"]] == ["EXECUTION_CREATED", "SKIPPED"]
    assert data["items"][0]["execution_id"] == str(execution_id)
    assert data["items"][0]["execution_status"] == "COMPLETED"
    assert data["items"][1]["skip_reason"] == "OVERLAP"
    assert data["items"][1]["missed_count"] == 2
    assert all(CONSOLE_TIME.match(item["scheduled_at"]) for item in data["items"])

    # DST 事实不是 Fire：它只出现在 dst_events，不伪造一条触发记录。
    assert len(data["dst_events"]) == 1
    dst = data["dst_events"][0]
    assert dst["event_type"] == "DST_GAP_SKIPPED"
    assert dst["local_date"] == "2026-03-08"
    assert dst["local_time"] == "02:30"
    assert dst["timezone"] == TZ
    assert CONSOLE_TIME.match(dst["occurred_at"])


async def test_console_actions_use_the_single_route_and_forward_scope(
    client: AsyncClient, task_tenant: TenantContext
) -> None:
    from agent_worker.test_scheduled_v2_snapshot import FakeMaterialsClient, make_materials

    schedule_id, _ = await seed_schedule(task_tenant.tenant_id, mode="REMINDER")
    running = await seed_execution(
        task_tenant.tenant_id,
        schedule_id=schedule_id,
        status="RUNNING",
        started_at=datetime.now(UTC),
    )
    queued = await seed_execution(task_tenant.tenant_id, schedule_id=schedule_id, status="QUEUED")
    headers = _headers(task_tenant)

    paused = await client.post(
        f"/api/v1/schedules/{schedule_id}/actions",
        json={"action": "pause"},
        headers=headers,
    )
    assert paused.status_code == 200, paused.text
    assert paused.json()["data"]["status"] == "PAUSED"
    assert CONSOLE_TIME.match(paused.json()["data"]["next_fire_at"])
    # 暂停只取消"从未开始"的执行，已开始的保留（started_at 是唯一判据）。
    assert (await _execution_row(queued))[0] == "CANCELLED"
    assert (await _execution_row(running))[0] == "RUNNING"

    worker_app.state.schedule_materials_client = FakeMaterialsClient(
        materials=make_materials(mode=ScheduleMode.REMINDER)
    )
    try:
        resumed = await client.post(
            f"/api/v1/schedules/{schedule_id}/actions",
            json={"action": "resume"},
            headers=headers,
        )
    finally:
        del worker_app.state.schedule_materials_client
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["data"]["status"] == "ACTIVE"
    next_fire = datetime.fromisoformat(resumed.json()["data"]["next_fire_at"])
    assert next_fire > datetime.now(UTC) - timedelta(minutes=1), "恢复从 now 重算，不补发"

    cancelled = await client.post(
        f"/api/v1/schedules/{schedule_id}/actions",
        json={"action": "cancel", "scope": "FUTURE_ONLY", "reason": "USER_REQUEST"},
        headers=headers,
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["data"]["status"] == "CANCELLED"
    assert cancelled.json()["data"]["end_reason"] == "USER_CANCELLED"
    assert cancelled.json()["data"]["next_fire_at"] is None
    # FUTURE_ONLY 不动已开始的执行。
    assert (await _execution_row(running))[0] == "RUNNING"


async def test_console_cancel_all_active_closes_human_wait(
    client: AsyncClient, task_tenant: TenantContext
) -> None:
    schedule_id, _ = await seed_schedule(task_tenant.tenant_id, mode="REMINDER")
    waiting = await seed_execution(
        task_tenant.tenant_id, schedule_id=schedule_id, status="WAITING_HUMAN"
    )
    interaction_id = await seed_human(task_tenant.tenant_id, waiting)

    response = await client.post(
        f"/api/v1/schedules/{schedule_id}/actions",
        json={"action": "cancel", "scope": "ALL_ACTIVE"},
        headers=_headers(task_tenant),
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["status"] == "CANCELLED"
    assert (await _execution_row(waiting))[0] == "CANCELLED"
    async with get_session_factory()() as session:
        interaction = await session.get(TaskHumanInteraction, interaction_id)
    assert interaction is not None and interaction.status == "CANCELLED"


async def test_console_has_no_create_or_legacy_action_routes(
    client: AsyncClient, task_tenant: TenantContext
) -> None:
    headers = _headers(task_tenant)
    schedule_id, _ = await seed_schedule(task_tenant.tenant_id)

    created = await client.post("/api/v1/schedules", json={}, headers=headers)
    assert created.status_code == 405, "Console 不提供创建入口（E-12）"
    for method, action in (("put", "pause"), ("put", "resume")):
        legacy = await getattr(client, method)(
            f"/api/v1/schedules/{schedule_id}/{action}", headers=headers
        )
        assert legacy.status_code == 404, "旧动作路由必须已移除，不做双路由"

    invalid = await client.post(
        f"/api/v1/schedules/{schedule_id}/actions", json={"action": "explode"}, headers=headers
    )
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "COMMON_VALIDATION_ERROR"

    scoped_pause = await client.post(
        f"/api/v1/schedules/{schedule_id}/actions",
        json={"action": "pause", "scope": "ALL_ACTIVE"},
        headers=headers,
    )
    assert scoped_pause.status_code == 422, "scope 只属于 cancel"

    deleted = await client.delete(f"/api/v1/schedules/{schedule_id}", headers=headers)
    assert deleted.status_code == 200
    assert deleted.json()["data"] == {"schedule_id": str(schedule_id), "deleted": True}


async def test_console_cross_tenant_schedule_stays_invisible(
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
        assert detail.status_code == 404
        assert fires.status_code == 404
        assert action.status_code == 404
        assert detail.json()["code"] == "SCHEDULE_NOT_FOUND"
    finally:
        from console_tasks.conftest import WORKER_TENANT_CLEANUP

        async with get_session_factory()() as session:
            for statement in WORKER_TENANT_CLEANUP:
                await session.execute(sa.text(statement), {"t": foreign_tenant})
            await session.commit()


async def test_console_task_views_carry_wait_snapshot_and_human(
    client: AsyncClient, task_tenant: TenantContext
) -> None:
    schedule_id, _ = await seed_schedule(task_tenant.tenant_id)
    deadline = datetime(2026, 11, 16, 10, 0, tzinfo=UTC)
    snapshot = {
        "schema_version": 4,
        "mode": "AGENTIC",
        "allowlist": ["MCP:crm:customer.report"],
        "tools": [{"name": "customer.report"}],
        "skills": [
            {
                "skill_id": str(uuid.uuid4()),
                "storage_key": "skills/internal-artifact-path",
            }
        ],
        "memory": {"mode": "READ_ONLY"},
        "budget": {"max_steps": 12},
    }
    waiting_human = await seed_execution(
        task_tenant.tenant_id,
        schedule_id=schedule_id,
        status="WAITING_HUMAN",
        task_type="AGENTIC",
        snapshot=snapshot,
    )
    await seed_execution_event(
        task_tenant.tenant_id,
        waiting_human,
        event_type="WAITING_HUMAN",
        payload={
            "interaction_id": str(uuid.uuid4()),
            "question": "请选择客户",
            "deadline_at": deadline.isoformat(),
        },
    )
    waiting_external = await seed_execution(
        task_tenant.tenant_id,
        schedule_id=schedule_id,
        status="WAITING_EXTERNAL",
        task_type="SKILL",
        wait_kind="EXTERNAL_POLL",
        wait_payload={"poll_ref": "external-1"},
    )
    headers = _headers(task_tenant)

    detail = await client.get(f"/api/v1/tasks/{waiting_human}", headers=headers)
    assert detail.status_code == 200, detail.text
    data = detail.json()["data"]
    assert data["status"] == "WAITING_HUMAN"
    assert data["task_type"] == "AGENTIC"
    assert data["snapshot"]["schema_version"] == 4
    assert data["snapshot"]["mode"] == "AGENTIC"
    assert data["snapshot"]["allowlist"] == ["MCP:crm:customer.report"]
    assert data["snapshot"]["memory_mode"] == "READ_ONLY"
    assert data["snapshot"]["budget"] == {"max_steps": 12}
    assert data["human"]["question"] == "请选择客户"
    assert data["human"]["open"] is True
    assert CONSOLE_TIME.match(data["human"]["deadline_at"])
    assert CONSOLE_TIME.match(data["create_time"])
    # 原始快照里的内部存储键不外泄（只给白名单摘要）。
    assert "execution_snapshot" not in data
    assert "storage_key" not in detail.text
    assert "internal-artifact-path" not in detail.text

    external = await client.get(f"/api/v1/tasks/{waiting_external}", headers=headers)
    external_data = external.json()["data"]
    assert external_data["status"] == "WAITING_EXTERNAL"
    assert external_data["wait_kind"] == "EXTERNAL_POLL"
    assert external_data["wait_payload"] == {"poll_ref": "external-1"}
    assert external_data["human"] is None

    listed = await client.get("/api/v1/tasks", headers=headers)
    assert listed.status_code == 200, listed.text
    items = {item["task_id"]: item for item in listed.json()["data"]["items"]}
    assert items[str(waiting_human)]["task_type"] == "AGENTIC"
    assert items[str(waiting_human)]["status"] == "WAITING_HUMAN"
    assert CONSOLE_TIME.match(items[str(waiting_human)]["deadline_at"])
