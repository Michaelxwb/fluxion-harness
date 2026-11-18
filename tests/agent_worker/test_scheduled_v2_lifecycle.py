"""TASK-013 · 暂停、恢复与两档取消的完整状态矩阵（S-04/S-05/S-06、E-05/E-06/E-13/E-15、B-12）。

真实边界：真实 FastAPI 路由 + 真实 PostgreSQL 行锁/CAS + 真实 recurrence 引擎；远端
operation 取消用注入的端口（生产装配是 Runtime API-30 的 HTTP 客户端）。

矩阵断言不看代码看行：
- `started_at IS NULL` 是「从未开始」的唯一判据（claim 赢了就保留，不按状态一刀切）；
- `FUTURE_ONLY` 保留 RUNNING/WAITING_*/OPEN interaction，只有 `ALL_ACTIVE` 才收尾；
- 取消不回滚既成事实：Fire/Execution/事件保留，远端取消失败只记证据。
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from muad_agent_worker.application.remote_cancellation import ToolCancelError
from muad_agent_worker.application.schedule_lifecycle import RemoteCancel
from muad_agent_worker.application.task_events import EntityType
from muad_agent_worker.infrastructure.models.task import (
    ScheduleFire,
    TaskEvent,
    TaskExecution,
    TaskHumanInteraction,
    TaskSchedule,
    TaskScheduleRevision,
)
from muad_agent_worker.main import app
from muad_agent_worker.scheduler.client import ScheduleMaterialsError
from muad_contracts import ScheduleMode

from agent_worker.conftest import TenantContext
from agent_worker.helpers import fetch_events, internal_service_headers, persist_task
from agent_worker.test_scheduled_v2_confirm import confirm as confirm_draft
from agent_worker.test_scheduled_v2_confirm import seed_draft
from agent_worker.test_scheduled_v2_snapshot import FakeMaterialsClient, make_materials

pytestmark = pytest.mark.integration

TZ = "Asia/Shanghai"
OWNER = uuid.UUID("5a1d0c1e-0000-4000-8000-00000000b013")
AGENT = uuid.UUID("5a1d0c1e-0000-4000-8000-0000000a9013")


# --------------------------------------------------------------------- seeds


@dataclass(frozen=True)
class Seeded:
    schedule_id: uuid.UUID
    revision_id: uuid.UUID
    agent_id: uuid.UUID
    owner_user_id: uuid.UUID


def headers(
    tenant: TenantContext, *, actor: uuid.UUID = OWNER, key: str | None = None
) -> dict[str, str]:
    return internal_service_headers(
        tenant.tenant_id,
        **{"X-Actor-User-Id": str(actor), "Idempotency-Key": key or f"act-{uuid.uuid4()}"},
    )


async def seed_schedule(
    tenant: TenantContext,
    *,
    status: str = "ACTIVE",
    kind: str = "CRON",
    cron: str | None = "0 8 * * *",
    run_at: datetime | None = None,
    next_fire_at: datetime | None = None,
    occurrence_count: int = 0,
    config_version: int = 1,
    delivery_intent: str = "NONE",
) -> Seeded:
    now = datetime.now(UTC)
    schedule_id, revision_id = uuid.uuid4(), uuid.uuid4()
    once = kind == "ONCE"
    async with tenant.session_factory() as session, session.begin():
        schedule = TaskSchedule(
            id=schedule_id,
            tenant_id=tenant.tenant_id,
            owner_user_id=OWNER,
            agent_id=AGENT,
            name="每天提醒",
            status=status,
            config_version=config_version,
            next_fire_at=next_fire_at or (now + timedelta(hours=1)),
            occurrence_count=occurrence_count,
            created_by=OWNER,
        )
        session.add(schedule)
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
                recurrence_kind=kind,
                cron_expr=None if once else cron,
                run_at=run_at.astimezone(UTC) if once and run_at else None,
                timezone=TZ,
                schedule_policy={},
                context_json={},
                payload_kind="REMINDER", payload_json={"kind": "REMINDER", "text": "开会"},
                delivery_intent=delivery_intent,
                preflight_json={},
                created_by=OWNER,
            )
        )
        await session.flush()
        schedule.current_revision_id = revision_id
    return Seeded(schedule_id, revision_id, AGENT, OWNER)


async def seed_fire(
    tenant: TenantContext,
    seeded: Seeded,
    *,
    scheduled_at: datetime | None = None,
    status: str = "EXECUTION_CREATED",
) -> uuid.UUID:
    fire_id = uuid.uuid4()
    async with tenant.session_factory() as session, session.begin():
        session.add(
            ScheduleFire(
                id=fire_id,
                tenant_id=tenant.tenant_id,
                schedule_id=seeded.schedule_id,
                schedule_revision_id=seeded.revision_id,
                scheduled_at=scheduled_at or datetime.now(UTC),
                status=status,
            )
        )
    return fire_id


async def seed_execution(
    tenant: TenantContext,
    seeded: Seeded,
    *,
    fire_id: uuid.UUID | None = None,
    status: str = "QUEUED",
    started_at: datetime | None = None,
    wait_kind: str | None = None,
    wait_payload: dict[str, Any] | None = None,
    lease_owner: str | None = None,
    parent: TaskExecution | None = None,
    item_key: str | None = None,
) -> TaskExecution:
    now = datetime.now(UTC)
    overrides: dict[str, Any] = {
        "agent_id": seeded.agent_id,
        "actor_user_id": seeded.owner_user_id,
        "trigger_type": "SCHEDULED",
        "schedule_id": seeded.schedule_id,
        "schedule_revision_id": seeded.revision_id,
        "scheduled_at": now,
        "status": status,
        "started_at": started_at,
        "wait_kind": wait_kind,
        "wait_payload_json": wait_payload,
        "lease_owner": lease_owner,
        "lease_until": now + timedelta(minutes=5) if lease_owner else None,
        "not_before": now - timedelta(seconds=30),
    }
    if parent is not None:
        overrides.update(
            parent_id=parent.id,
            root_id=parent.root_id or parent.id,
            item_key=item_key,
            schedule_id=parent.schedule_id,
        )
    else:
        overrides["schedule_fire_id"] = fire_id
    task = await persist_task(tenant, **overrides)
    if fire_id is not None:
        async with tenant.session_factory() as session, session.begin():
            await session.execute(
                sa.update(ScheduleFire).where(ScheduleFire.id == fire_id).values(execution_id=task.id)
            )
    return task


async def seed_interaction(tenant: TenantContext, execution: TaskExecution) -> uuid.UUID:
    interaction_id = uuid.uuid4()
    async with tenant.session_factory() as session, session.begin():
        session.add(
            TaskHumanInteraction(
                id=interaction_id,
                tenant_id=tenant.tenant_id,
                execution_id=execution.id,
                owner_user_id=execution.actor_user_id,
                agent_id=execution.agent_id,
                interaction_type="QUESTION",
                status="OPEN",
                question="哪份报表？",
                deadline_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
    return interaction_id


# ------------------------------------------------------------------ readers


async def read_schedule(tenant: TenantContext, schedule_id: uuid.UUID) -> TaskSchedule:
    async with tenant.session_factory() as session:
        schedule = await session.get(TaskSchedule, schedule_id)
    assert schedule is not None
    return schedule


async def read_execution(tenant: TenantContext, execution_id: uuid.UUID) -> TaskExecution:
    async with tenant.session_factory() as session:
        execution = await session.get(TaskExecution, execution_id)
        assert execution is not None
        await session.refresh(execution)
    return execution


async def read_interaction(tenant: TenantContext, interaction_id: uuid.UUID) -> TaskHumanInteraction:
    async with tenant.session_factory() as session:
        interaction = await session.get(TaskHumanInteraction, interaction_id)
    assert interaction is not None
    return interaction


async def read_fire(tenant: TenantContext, fire_id: uuid.UUID) -> ScheduleFire:
    async with tenant.session_factory() as session:
        fire = await session.get(ScheduleFire, fire_id)
    assert fire is not None
    return fire


async def event_types(
    tenant: TenantContext, entity_id: uuid.UUID, entity_type: EntityType
) -> list[str]:
    return [event.event_type for event in await fetch_events(tenant, entity_id, entity_type=entity_type)]


async def async_events(tenant: TenantContext, operation_id: uuid.UUID) -> list[TaskEvent]:
    return await fetch_events(tenant, operation_id, entity_type=EntityType.ASYNC)


def _operation_id(execution: TaskExecution) -> uuid.UUID:
    operations = (execution.wait_payload_json or {}).get("operations") or []
    return uuid.UUID(str(operations[0]["operation_id"]))


# ------------------------------------------------------------------- client


async def action(
    client: AsyncClient,
    tenant: TenantContext,
    schedule_id: uuid.UUID,
    name: str,
    *,
    body: dict[str, Any] | None = None,
    key: str | None = None,
    actor: uuid.UUID = OWNER,
):
    return await client.post(
        f"/internal/schedules/{schedule_id}/{name}",
        json=body or {},
        headers=headers(tenant, actor=actor, key=key),
    )


def use_materials(fake: FakeMaterialsClient) -> None:
    app.state.schedule_materials_client = fake


def clear_materials() -> None:
    if hasattr(app.state, "schedule_materials_client"):
        del app.state.schedule_materials_client


def reminder_materials(schedule: Seeded) -> FakeMaterialsClient:
    del schedule
    return FakeMaterialsClient(materials=make_materials(mode=ScheduleMode.REMINDER))


# ------------------------------------------------------------ pause matrix


async def test_pause_cancels_only_never_started_executions(
    client: AsyncClient, tenant: TenantContext
) -> None:
    seeded = await seed_schedule(tenant)
    fire = await seed_fire(tenant, seeded)
    never_started = await seed_execution(tenant, seeded, fire_id=fire, status="QUEUED")
    reclaimed = await seed_execution(
        tenant, seeded, status="QUEUED", started_at=datetime.now(UTC) - timedelta(minutes=3)
    )
    running = await seed_execution(
        tenant, seeded, status="RUNNING", started_at=datetime.now(UTC), lease_owner="w"
    )
    waiting_external = await seed_execution(
        tenant,
        seeded,
        status="WAITING_EXTERNAL",
        started_at=datetime.now(UTC),
        wait_kind="EXTERNAL_POLL",
        wait_payload={"external_ref": {"id": "poll-1"}, "not_before": None},
    )
    waiting_human = await seed_execution(
        tenant, seeded, status="WAITING_HUMAN", started_at=datetime.now(UTC)
    )
    interaction = await seed_interaction(tenant, waiting_human)
    before = await read_schedule(tenant, seeded.schedule_id)

    response = await action(client, tenant, seeded.schedule_id, "pause", body={"reason": "USER_REQUEST"})

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "PAUSED"
    schedule = await read_schedule(tenant, seeded.schedule_id)
    assert schedule.status == "PAUSED"
    assert schedule.next_fire_at == before.next_fire_at, "pause 不重算/不清空指针"
    assert (await read_execution(tenant, never_started.id)).status == "CANCELLED"
    assert (await read_fire(tenant, fire)).status == "CANCELLED"
    assert (await read_execution(tenant, reclaimed.id)).status == "QUEUED", "已开始的续接保留"
    assert (await read_execution(tenant, running.id)).status == "RUNNING"
    assert (await read_execution(tenant, running.id)).cancel_requested is False
    assert (await read_execution(tenant, waiting_external.id)).status == "WAITING_EXTERNAL"
    assert (await read_execution(tenant, waiting_human.id)).status == "WAITING_HUMAN"
    assert (await read_interaction(tenant, interaction)).status == "OPEN"
    assert await event_types(tenant, seeded.schedule_id, EntityType.SCHEDULE) == ["PAUSED"]
    assert await event_types(tenant, never_started.id, EntityType.EXECUTION) == ["CANCELLED"]
    assert await event_types(tenant, fire, EntityType.FIRE) == ["CANCELLED"]


async def test_pause_is_idempotent(client: AsyncClient, tenant: TenantContext) -> None:
    seeded = await seed_schedule(tenant)

    first = await action(client, tenant, seeded.schedule_id, "pause")
    second = await action(client, tenant, seeded.schedule_id, "pause")

    assert first.status_code == 200 and second.status_code == 200
    assert second.json()["data"]["status"] == "PAUSED"
    assert await event_types(tenant, seeded.schedule_id, EntityType.SCHEDULE) == ["PAUSED"]


@pytest.mark.parametrize("status", ["ENDED", "CANCELLED", "NEEDS_ATTENTION"])
async def test_pause_rejects_non_active_states(
    client: AsyncClient, tenant: TenantContext, status: str
) -> None:
    seeded = await seed_schedule(tenant, status=status)

    response = await action(client, tenant, seeded.schedule_id, "pause")

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "SCHEDULE_STATE_INVALID"
    assert (await read_schedule(tenant, seeded.schedule_id)).status == status


async def test_pause_rejects_scope(client: AsyncClient, tenant: TenantContext) -> None:
    """scope 只属于 cancel：在 pause body 里出现即校验失败，不静默忽略。"""
    seeded = await seed_schedule(tenant)

    response = await action(
        client, tenant, seeded.schedule_id, "pause", body={"scope": "ALL_ACTIVE"}
    )

    assert response.status_code == 422, response.text
    assert (await read_schedule(tenant, seeded.schedule_id)).status == "ACTIVE"


# ----------------------------------------------------------- resume matrix


async def test_resume_recomputes_next_fire_from_now_without_backfill(
    client: AsyncClient, tenant: TenantContext
) -> None:
    past = datetime.now(UTC) - timedelta(hours=6)
    seeded = await seed_schedule(tenant, status="PAUSED", next_fire_at=past, occurrence_count=5)
    use_materials(reminder_materials(seeded))
    try:
        response = await action(client, tenant, seeded.schedule_id, "resume")
    finally:
        clear_materials()

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "ACTIVE"
    schedule = await read_schedule(tenant, seeded.schedule_id)
    assert schedule.next_fire_at is not None
    assert schedule.next_fire_at > datetime.now(UTC), "不补发暂停期"
    assert schedule.occurrence_count == 5, "恢复不伪造发生次数"
    assert await event_types(tenant, seeded.schedule_id, EntityType.SCHEDULE) == ["RESUMED"]


async def test_resume_from_needs_attention_clears_attention(
    client: AsyncClient, tenant: TenantContext
) -> None:
    seeded = await seed_schedule(tenant, status="NEEDS_ATTENTION")
    async with tenant.session_factory() as session, session.begin():
        await session.execute(
            sa.update(TaskSchedule)
            .where(TaskSchedule.id == seeded.schedule_id)
            .values(attention_reason="SKILL_DISABLED", attention_since=datetime.now(UTC))
        )
    use_materials(reminder_materials(seeded))
    try:
        response = await action(client, tenant, seeded.schedule_id, "resume")
    finally:
        clear_materials()

    assert response.status_code == 200, response.text
    schedule = await read_schedule(tenant, seeded.schedule_id)
    assert schedule.status == "ACTIVE"
    assert schedule.attention_reason is None and schedule.attention_since is None


async def test_resume_keeps_paused_when_admission_fails(
    client: AsyncClient, tenant: TenantContext
) -> None:
    seeded = await seed_schedule(tenant, status="PAUSED")
    use_materials(FakeMaterialsClient(errors=[ScheduleMaterialsError("down")]))
    try:
        response = await action(client, tenant, seeded.schedule_id, "resume")
    finally:
        clear_materials()

    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == "SCHEDULE_ADMISSION_FAILED"
    assert body["data"]["admission_code"] == "RESOLVE_UNAVAILABLE"
    schedule = await read_schedule(tenant, seeded.schedule_id)
    assert schedule.status == "PAUSED", "准入失败保持原状态"
    assert await event_types(tenant, seeded.schedule_id, EntityType.SCHEDULE) == []


async def test_resume_once_with_permanent_failure_ends_schedule(
    client: AsyncClient, tenant: TenantContext
) -> None:
    seeded = await seed_schedule(
        tenant, status="PAUSED", kind="ONCE", cron=None, run_at=datetime.now(UTC) + timedelta(hours=2)
    )
    use_materials(FakeMaterialsClient(errors=[ScheduleMaterialsError("disabled", code="SKILL_DISABLED")]))
    try:
        response = await action(client, tenant, seeded.schedule_id, "resume")
    finally:
        clear_materials()

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "ENDED"
    assert data["end_reason"] == "ADMISSION_FAILED"
    assert data["admission_error"]["code"] == "SKILL_DISABLED"
    schedule = await read_schedule(tenant, seeded.schedule_id)
    assert schedule.status == "ENDED" and schedule.end_reason == "ADMISSION_FAILED"
    assert schedule.next_fire_at is None
    assert await event_types(tenant, seeded.schedule_id, EntityType.SCHEDULE) == ["ENDED"]

    use_materials(reminder_materials(seeded))
    try:
        again = await action(client, tenant, seeded.schedule_id, "resume")
    finally:
        clear_materials()
    assert again.status_code == 409, "ENDED 不可恢复"
    assert again.json()["code"] == "SCHEDULE_STATE_INVALID"


async def test_resume_once_in_the_past_ends_as_missed(
    client: AsyncClient, tenant: TenantContext
) -> None:
    seeded = await seed_schedule(
        tenant, status="PAUSED", kind="ONCE", cron=None, run_at=datetime.now(UTC) - timedelta(hours=1)
    )
    use_materials(reminder_materials(seeded))
    try:
        response = await action(client, tenant, seeded.schedule_id, "resume")
    finally:
        clear_materials()

    assert response.status_code == 200, response.text
    assert response.json()["data"]["end_reason"] == "MISSED"
    schedule = await read_schedule(tenant, seeded.schedule_id)
    assert schedule.status == "ENDED" and schedule.next_fire_at is None


async def test_resume_already_active_is_a_no_op(
    client: AsyncClient, tenant: TenantContext
) -> None:
    seeded = await seed_schedule(tenant)
    before = await read_schedule(tenant, seeded.schedule_id)

    response = await action(client, tenant, seeded.schedule_id, "resume")

    assert response.status_code == 200, response.text
    assert response.json()["data"]["status"] == "ACTIVE"
    after = await read_schedule(tenant, seeded.schedule_id)
    assert after.next_fire_at == before.next_fire_at
    assert await event_types(tenant, seeded.schedule_id, EntityType.SCHEDULE) == []


async def test_resume_rejects_config_version_drift(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """准入在锁外：期间被更新（config_version 变化）就报冲突，绝不覆盖新配置。"""
    seeded = await seed_schedule(tenant, status="PAUSED")

    class DriftingMaterials(FakeMaterialsClient):
        async def resolve_schedule_materials(self, tenant_id: str, payload: Any) -> Any:
            async with tenant.session_factory() as session, session.begin():
                await session.execute(
                    sa.update(TaskSchedule)
                    .where(TaskSchedule.id == seeded.schedule_id)
                    .values(config_version=2)
                )
            return await super().resolve_schedule_materials(tenant_id, payload)

    use_materials(DriftingMaterials(materials=make_materials(mode=ScheduleMode.REMINDER)))
    try:
        response = await action(client, tenant, seeded.schedule_id, "resume")
    finally:
        clear_materials()

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "SCHEDULE_VERSION_CONFLICT"
    schedule = await read_schedule(tenant, seeded.schedule_id)
    assert schedule.status == "PAUSED"


# ----------------------------------------------------------- cancel matrix


async def _cancel_seed(
    tenant: TenantContext,
) -> tuple[Seeded, dict[str, Any]]:
    seeded = await seed_schedule(tenant)
    fire = await seed_fire(tenant, seeded)
    queued = await seed_execution(tenant, seeded, fire_id=fire, status="QUEUED")
    started_queued = await seed_execution(
        tenant, seeded, status="QUEUED", started_at=datetime.now(UTC) - timedelta(minutes=1)
    )
    running = await seed_execution(
        tenant, seeded, status="RUNNING", started_at=datetime.now(UTC), lease_owner="w"
    )
    waiting_external = await seed_execution(
        tenant,
        seeded,
        status="WAITING_EXTERNAL",
        started_at=datetime.now(UTC),
        wait_kind="ASYNC_OPERATION",
        wait_payload={
            "operations": [
                {
                    "operation_id": str(uuid.uuid4()),
                    "call_id": "call-1",
                    "receipt_seq": None,
                    "consumed_event_id": None,
                }
            ]
        },
    )
    waiting_human = await seed_execution(
        tenant, seeded, status="WAITING_HUMAN", started_at=datetime.now(UTC)
    )
    interaction = await seed_interaction(tenant, waiting_human)
    return seeded, {
        "fire": fire,
        "queued": queued,
        "started_queued": started_queued,
        "running": running,
        "waiting_external": waiting_external,
        "waiting_human": waiting_human,
        "interaction": interaction,
    }


async def test_future_only_cancel_keeps_running_waiting_and_open_interaction(
    client: AsyncClient, tenant: TenantContext
) -> None:
    seeded, rows = await _cancel_seed(tenant)

    response = await action(
        client, tenant, seeded.schedule_id, "cancel", body={"scope": "FUTURE_ONLY"}
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "CANCELLED" and data["end_reason"] == "USER_CANCELLED"
    assert (await read_execution(tenant, rows["queued"].id)).status == "CANCELLED"
    assert (await read_fire(tenant, rows["fire"])).status == "CANCELLED"
    assert (await read_execution(tenant, rows["started_queued"].id)).status == "QUEUED"
    running = await read_execution(tenant, rows["running"].id)
    assert running.status == "RUNNING" and running.cancel_requested is False
    assert (await read_execution(tenant, rows["waiting_external"].id)).status == "WAITING_EXTERNAL"
    assert (await read_execution(tenant, rows["waiting_human"].id)).status == "WAITING_HUMAN"
    assert (await read_interaction(tenant, rows["interaction"])).status == "OPEN", (
        "FUTURE_ONLY 不能关掉仍被等待的 interaction"
    )
    operation_id = _operation_id(rows["waiting_external"])
    assert await async_events(tenant, operation_id) == []


async def test_all_active_cancel_terminates_everything_and_closes_interaction(
    client: AsyncClient, tenant: TenantContext
) -> None:
    seeded, rows = await _cancel_seed(tenant)

    response = await action(client, tenant, seeded.schedule_id, "cancel", body={"scope": "ALL_ACTIVE"})

    assert response.status_code == 200, response.text
    await _assert_all_active_cancelled(tenant, rows)
    operation_id = _operation_id(rows["waiting_external"])
    events = await async_events(tenant, operation_id)
    assert [(event.event_type, event.payload_json["state"]) for event in events] == [
        ("CANCELLED", "REQUESTED")
    ], "远端取消意图必须先落库（待核实）"


async def _assert_all_active_cancelled(tenant: TenantContext, rows: dict[str, Any]) -> None:
    for key in ("queued", "started_queued", "waiting_external", "waiting_human"):
        assert (await read_execution(tenant, rows[key].id)).status == "CANCELLED", key
    running = await read_execution(tenant, rows["running"].id)
    assert running.status == "RUNNING", "RUNNING 协作停止：等 Worker 在检查点收尾"
    assert running.cancel_requested is True
    assert (await read_interaction(tenant, rows["interaction"])).status == "CANCELLED"
    assert await event_types(tenant, rows["interaction"], EntityType.HUMAN) == ["CANCELLED"]


async def test_cancel_defaults_to_future_only(
    client: AsyncClient, tenant: TenantContext
) -> None:
    seeded, rows = await _cancel_seed(tenant)

    response = await action(client, tenant, seeded.schedule_id, "cancel", body={"reason": "USER_REQUEST"})

    assert response.status_code == 200, response.text
    assert (await read_execution(tenant, rows["running"].id)).status == "RUNNING"
    assert (await read_interaction(tenant, rows["interaction"])).status == "OPEN"


async def test_cancel_is_idempotent_and_keeps_history(
    client: AsyncClient, tenant: TenantContext
) -> None:
    seeded, rows = await _cancel_seed(tenant)

    first = await action(client, tenant, seeded.schedule_id, "cancel", key="cancel-1")
    second = await action(client, tenant, seeded.schedule_id, "cancel", key="cancel-2")

    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["data"] == second.json()["data"]
    assert await event_types(tenant, seeded.schedule_id, EntityType.SCHEDULE) == ["CANCELLED"]
    assert await read_fire(tenant, rows["fire"]) is not None
    assert await read_execution(tenant, rows["running"].id) is not None
    assert len(await fetch_events(tenant, rows["queued"].id, entity_type=EntityType.EXECUTION)) == 1


async def test_cancel_rejects_ended_schedule(
    client: AsyncClient, tenant: TenantContext
) -> None:
    seeded = await seed_schedule(tenant, status="ENDED")

    response = await action(client, tenant, seeded.schedule_id, "cancel")

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "SCHEDULE_STATE_INVALID"


async def test_cancel_replays_first_response_for_the_same_key(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """同键同指纹重放优先于状态：暂停后又恢复，重放仍是首次 PAUSED 快照。"""
    seeded = await seed_schedule(tenant)
    use_materials(reminder_materials(seeded))
    try:
        first = await action(client, tenant, seeded.schedule_id, "pause", key="pause-key")
        resumed = await action(client, tenant, seeded.schedule_id, "resume", key="resume-key")
        replay = await action(client, tenant, seeded.schedule_id, "pause", key="pause-key")
    finally:
        clear_materials()

    assert resumed.json()["data"]["status"] == "ACTIVE"
    assert replay.status_code == 200
    assert replay.json()["data"] == first.json()["data"]
    assert replay.json()["data"]["status"] == "PAUSED"


# ---------------------------------------------- remote cancel evidence path


class _FailingCanceller:
    def __init__(self) -> None:
        self.calls: list[RemoteCancel] = []

    async def cancel(self, *, tenant_id: str, item: RemoteCancel) -> None:
        del tenant_id
        self.calls.append(item)
        raise ToolCancelError("runtime down", code="RESOLVE_UNAVAILABLE")


class _RecordingCanceller:
    def __init__(self) -> None:
        self.calls: list[RemoteCancel] = []

    async def cancel(self, *, tenant_id: str, item: RemoteCancel) -> None:
        del tenant_id
        self.calls.append(item)


async def test_remote_cancel_failure_is_recorded_without_rolling_back(
    client: AsyncClient, tenant: TenantContext
) -> None:
    seeded, rows = await _cancel_seed(tenant)
    canceller = _FailingCanceller()
    app.state.tool_operation_canceller = canceller
    try:
        response = await action(
            client, tenant, seeded.schedule_id, "cancel", body={"scope": "ALL_ACTIVE"}
        )
    finally:
        del app.state.tool_operation_canceller

    assert response.status_code == 200, response.text
    assert len(canceller.calls) == 1
    assert (await read_schedule(tenant, seeded.schedule_id)).status == "CANCELLED", (
        "远端失败不撤销已停下的未来调度"
    )
    operation_id = _operation_id(rows["waiting_external"])
    events = await async_events(tenant, operation_id)
    assert [event.payload_json["state"] for event in events] == ["REQUESTED", "FAILED"]
    assert events[1].payload_json["error_code"] == "RESOLVE_UNAVAILABLE"
    assert events[1].event_type == "FAILED"


async def test_remote_cancel_success_writes_confirmed_evidence(
    client: AsyncClient, tenant: TenantContext
) -> None:
    seeded, rows = await _cancel_seed(tenant)
    canceller = _RecordingCanceller()
    app.state.tool_operation_canceller = canceller
    try:
        response = await action(
            client, tenant, seeded.schedule_id, "cancel", body={"scope": "ALL_ACTIVE"}
        )
    finally:
        del app.state.tool_operation_canceller

    assert response.status_code == 200, response.text
    operation_id = _operation_id(rows["waiting_external"])
    events = await async_events(tenant, operation_id)
    assert [event.payload_json["state"] for event in events] == ["REQUESTED", "CONFIRMED"]


# ------------------------------------------------------- claim / answer CAS


async def test_pause_does_not_overwrite_a_concurrent_claim(
    tenant: TenantContext,
) -> None:
    """claim 先拿到行锁并把它变 RUNNING：pause 的 CAS 必须按新状态保留执行。"""
    from muad_agent_worker.application.schedule_lifecycle import ScheduleService

    seeded = await seed_schedule(tenant)
    fire = await seed_fire(tenant, seeded)
    execution = await seed_execution(tenant, seeded, fire_id=fire, status="QUEUED")

    claimer = tenant.session_factory()
    pause_session = tenant.session_factory()
    try:
        # claimer 先拿到行锁（事务未提交）：pause 的 CAS 必须等它提交后再看真实状态。
        await claimer.execute(
            sa.update(TaskExecution)
            .where(TaskExecution.id == execution.id)
            .values(status="RUNNING", started_at=datetime.now(UTC), lease_owner="w")
        )
        pause = asyncio.create_task(
            ScheduleService(pause_session, now=datetime.now(UTC)).pause(
                seeded.schedule_id, tenant_id=tenant.tenant_id, actor_user_id=OWNER
            )
        )
        await asyncio.sleep(0.2)
        await claimer.commit()
        await asyncio.wait_for(pause, timeout=20)
        await pause_session.commit()
    finally:
        await claimer.close()
        await pause_session.close()

    assert (await read_schedule(tenant, seeded.schedule_id)).status == "PAUSED"
    state = await read_execution(tenant, execution.id)
    assert state.status == "RUNNING", "pause 把并发 claim 抢走的执行当成了未开始"
    assert state.cancel_requested is False
    assert (await read_fire(tenant, fire)).status == "EXECUTION_CREATED"


async def test_cancel_does_not_overwrite_a_concurrent_claim(
    tenant: TenantContext,
) -> None:
    """cancel 与 claim 抢同一行：claim 赢（RUNNING）后 `FUTURE_ONLY` 必须保留它。"""
    from muad_agent_worker.application.schedule_lifecycle import ScheduleService

    seeded = await seed_schedule(tenant)
    fire = await seed_fire(tenant, seeded)
    execution = await seed_execution(tenant, seeded, fire_id=fire, status="QUEUED")

    claimer = tenant.session_factory()
    cancel_session = tenant.session_factory()
    try:
        await claimer.execute(
            sa.update(TaskExecution)
            .where(TaskExecution.id == execution.id)
            .values(status="RUNNING", started_at=datetime.now(UTC), lease_owner="w")
        )
        cancel = asyncio.create_task(
            ScheduleService(cancel_session, now=datetime.now(UTC)).cancel(
                seeded.schedule_id,
                tenant_id=tenant.tenant_id,
                actor_user_id=OWNER,
            )
        )
        await asyncio.sleep(0.2)
        await claimer.commit()
        await asyncio.wait_for(cancel, timeout=20)
        await cancel_session.commit()
    finally:
        await claimer.close()
        await cancel_session.close()

    assert (await read_schedule(tenant, seeded.schedule_id)).status == "CANCELLED"
    state = await read_execution(tenant, execution.id)
    assert state.status == "RUNNING", "取消把并发 claim 的执行一刀切成 CANCELLED"
    assert state.cancel_requested is False
    assert (await read_fire(tenant, fire)).status == "EXECUTION_CREATED"


async def test_cancel_serializes_with_a_concurrent_fire_write(
    tenant: TenantContext,
) -> None:
    """Scheduler 写 Fire/Execution 与取消共用 Schedule 行锁：取消不会漏掉那次 occurrence。"""
    from muad_agent_worker.application.schedule_lifecycle import ScheduleService

    seeded = await seed_schedule(tenant)
    scheduler = tenant.session_factory()
    cancel_session = tenant.session_factory()
    try:
        await scheduler.execute(
            sa.select(TaskSchedule.id)
            .where(TaskSchedule.id == seeded.schedule_id)
            .with_for_update()
        )
        fire_id, execution_id = uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC)
        await scheduler.execute(
            sa.insert(ScheduleFire).values(
                id=fire_id,
                tenant_id=tenant.tenant_id,
                schedule_id=seeded.schedule_id,
                schedule_revision_id=seeded.revision_id,
                scheduled_at=now,
                status="CREATED",
            )
        )
        await scheduler.execute(
            sa.insert(TaskExecution).values(
                id=execution_id,
                tenant_id=tenant.tenant_id,
                agent_id=seeded.agent_id,
                actor_user_id=OWNER,
                trigger_type="SCHEDULED",
                task_type="REMINDER",
                status="QUEUED",
                schedule_id=seeded.schedule_id,
                schedule_fire_id=fire_id,
                schedule_revision_id=seeded.revision_id,
                scheduled_at=now,
                input_json={},
                execution_snapshot_json={},
                snapshot_hash="sha256:" + "a" * 64,
                idempotency_key=f"fire-{uuid.uuid4()}",
                not_before=now,
                deadline_at=now + timedelta(hours=1),
            )
        )
        cancel = asyncio.create_task(
            ScheduleService(cancel_session, now=datetime.now(UTC)).cancel(
                seeded.schedule_id, tenant_id=tenant.tenant_id, actor_user_id=OWNER
            )
        )
        await asyncio.sleep(0.2)
        await scheduler.commit()
        outcome = await asyncio.wait_for(cancel, timeout=20)
        await cancel_session.commit()
    finally:
        await scheduler.close()
        await cancel_session.close()

    assert outcome.schedule is not None and outcome.schedule.status == "CANCELLED"
    assert (await read_execution(tenant, execution_id)).status == "CANCELLED", (
        "取消先决定再等 Fire 提交，会漏掉这次未开始的 occurrence"
    )
    assert (await read_fire(tenant, fire_id)).status == "CANCELLED"


async def test_cancel_rolls_back_schedule_executions_and_events_together(
    tenant: TenantContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """任何一步失败整体回滚：不留半个 CANCELLED 的 Schedule/Execution/事件。"""
    from muad_agent_worker.application import schedule_lifecycle

    seeded = await seed_schedule(tenant)
    fire = await seed_fire(tenant, seeded)
    execution = await seed_execution(tenant, seeded, fire_id=fire, status="QUEUED")

    async def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("event write failed")

    monkeypatch.setattr(schedule_lifecycle, "append_events", boom)

    async with tenant.session_factory() as session:
        service = schedule_lifecycle.ScheduleService(session, now=datetime.now(UTC))
        with pytest.raises(RuntimeError):
            await service.cancel(
                seeded.schedule_id, tenant_id=tenant.tenant_id, actor_user_id=OWNER
            )
        await session.rollback()

    assert (await read_schedule(tenant, seeded.schedule_id)).status == "ACTIVE"
    assert (await read_execution(tenant, execution.id)).status == "QUEUED"
    assert (await read_fire(tenant, fire)).status == "EXECUTION_CREATED"
    assert await event_types(tenant, seeded.schedule_id, EntityType.SCHEDULE) == []


async def test_answer_cannot_resolve_an_interaction_closed_by_cancel(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """取消与回答抢同一行：cancel 先赢后，回答的 CAS（OPEN + WAITING_HUMAN）必须落空。"""
    seeded, rows = await _cancel_seed(tenant)

    response = await action(client, tenant, seeded.schedule_id, "cancel", body={"scope": "ALL_ACTIVE"})
    assert response.status_code == 200, response.text

    async with tenant.session_factory() as session, session.begin():
        resolved = await session.execute(
            sa.update(TaskHumanInteraction)
            .where(
                TaskHumanInteraction.id == rows["interaction"],
                TaskHumanInteraction.status == "OPEN",
                TaskHumanInteraction.execution_id.in_(
                    sa.select(TaskExecution.id).where(
                        TaskExecution.status == "WAITING_HUMAN"
                    )
                ),
            )
            .values(status="RESOLVED", response_json={"choice": "A"})
        )
        assert resolved.rowcount == 0, "已关闭的 interaction 不得被回答复活"
    interaction = await read_interaction(tenant, rows["interaction"])
    assert interaction.status == "CANCELLED" and interaction.response_json is None


async def test_cancel_after_deadline_timeout_does_not_rewrite_terminal_state(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """deadline 先收尾（FAILED + TIMED_OUT）：取消不得改写终态，也不重复关 interaction。"""
    seeded = await seed_schedule(tenant)
    fire = await seed_fire(tenant, seeded)
    execution = await seed_execution(
        tenant, seeded, fire_id=fire, status="WAITING_HUMAN", started_at=datetime.now(UTC)
    )
    interaction = await seed_interaction(tenant, execution)
    async with tenant.session_factory() as session, session.begin():
        await session.execute(
            sa.update(TaskExecution)
            .where(TaskExecution.id == execution.id)
            .values(status="FAILED", failure_class="HUMAN_TIMEOUT", finished_at=datetime.now(UTC))
        )
        await session.execute(
            sa.update(TaskHumanInteraction)
            .where(TaskHumanInteraction.id == interaction)
            .values(status="TIMED_OUT", resolved_at=datetime.now(UTC))
        )

    response = await action(
        client, tenant, seeded.schedule_id, "cancel", body={"scope": "ALL_ACTIVE"}
    )

    assert response.status_code == 200, response.text
    state = await read_execution(tenant, execution.id)
    assert state.status == "FAILED" and state.failure_class == "HUMAN_TIMEOUT"
    closed = await read_interaction(tenant, interaction)
    assert closed.status == "TIMED_OUT"
    assert await event_types(tenant, interaction, EntityType.HUMAN) == [], (
        "已超时的 interaction 不得再被取消改写"
    )


# ------------------------------------------------------------ update / pause


async def test_pause_then_update_keeps_paused_and_new_revision(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """更新与暂停共用 Schedule 行锁：更新追加 Revision 但不得把 PAUSED 复活成 ACTIVE。"""
    seeded = await seed_schedule(tenant)
    paused = await action(client, tenant, seeded.schedule_id, "pause")
    assert paused.json()["data"]["status"] == "PAUSED"
    async with tenant.session_factory() as session:
        schedule = await session.get(TaskSchedule, seeded.schedule_id)
    assert schedule is not None
    draft = await seed_draft(
        tenant,
        owner=OWNER,
        agent=seeded.agent_id,
        kind="UPDATE",
        schedule=schedule,
        base_config_version=1,
        body={
            "instruction": "每天 9 点提醒我开会",
            "recurrence": {"kind": "CRON", "cron": "0 9 * * *", "timezone": TZ},
            "payload": {"kind": "REMINDER", "text": "开会"},
        },
    )

    confirmed = await confirm_draft(client, tenant, draft, actor=OWNER)

    assert confirmed.status_code == 200, confirmed.text
    after = await read_schedule(tenant, seeded.schedule_id)
    assert after.status == "PAUSED", "更新不得把暂停中的 Schedule 复活"
    assert after.config_version == 2
    assert after.current_revision_id != seeded.revision_id


# ------------------------------------------------------- execution cancel API


async def test_execution_cancel_terminates_one_occurrence_only(
    client: AsyncClient, tenant: TenantContext
) -> None:
    seeded = await seed_schedule(tenant)
    fire = await seed_fire(tenant, seeded)
    execution = await seed_execution(
        tenant, seeded, fire_id=fire, status="WAITING_HUMAN", started_at=datetime.now(UTC)
    )
    interaction = await seed_interaction(tenant, execution)

    response = await client.post(
        f"/internal/executions/{execution.id}/cancel",
        json={"reason": "USER_REQUEST"},
        headers=headers(tenant),
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["execution_id"] == str(execution.id)
    assert data["status"] == "CANCELLED"
    assert data["cancel_requested"] is True
    assert (await read_interaction(tenant, interaction)).status == "CANCELLED"
    assert (await read_fire(tenant, fire)).status == "CANCELLED"
    assert (await read_schedule(tenant, seeded.schedule_id)).status == "ACTIVE", (
        "一次 occurrence 的取消不等于停掉整个 Schedule"
    )


async def test_execution_cancel_is_idempotent(client: AsyncClient, tenant: TenantContext) -> None:
    seeded = await seed_schedule(tenant)
    fire = await seed_fire(tenant, seeded)
    execution = await seed_execution(tenant, seeded, fire_id=fire, status="QUEUED")

    first = await client.post(
        f"/internal/executions/{execution.id}/cancel", json={}, headers=headers(tenant, key="exec-1")
    )
    replay = await client.post(
        f"/internal/executions/{execution.id}/cancel", json={}, headers=headers(tenant, key="exec-1")
    )
    again = await client.post(
        f"/internal/executions/{execution.id}/cancel", json={}, headers=headers(tenant, key="exec-2")
    )

    assert first.status_code == 200 and replay.status_code == 200 and again.status_code == 200
    assert replay.json()["data"] == first.json()["data"]
    assert again.json()["data"]["status"] == "CANCELLED"
    assert len(await fetch_events(tenant, execution.id, entity_type=EntityType.EXECUTION)) == 1


async def test_execution_cancel_cascades_to_children(
    client: AsyncClient, tenant: TenantContext
) -> None:
    seeded = await seed_schedule(tenant)
    fire = await seed_fire(tenant, seeded)
    parent = await seed_execution(tenant, seeded, fire_id=fire, status="RUNNING", lease_owner="w")
    parent = await tenant_reload(tenant, parent)
    child = await seed_execution(
        tenant, seeded, status="QUEUED", parent=parent, item_key="child-1"
    )

    response = await client.post(
        f"/internal/executions/{parent.id}/cancel", json={}, headers=headers(tenant)
    )

    assert response.status_code == 200, response.text
    assert (await read_execution(tenant, child.id)).status == "CANCELLED"


async def tenant_reload(tenant: TenantContext, execution: TaskExecution) -> TaskExecution:
    async with tenant.session_factory() as session:
        return await session.get(TaskExecution, execution.id)


# ------------------------------------------------------------- read surface


async def test_list_detail_and_history_read_the_v2_shape(
    client: AsyncClient, tenant: TenantContext
) -> None:
    seeded = await seed_schedule(tenant)
    fire = await seed_fire(tenant, seeded)
    await seed_execution(tenant, seeded, fire_id=fire, status="COMPLETED")

    listing = await client.get("/internal/schedules", headers=headers(tenant))
    assert listing.status_code == 200, listing.text
    page = listing.json()["data"]
    assert set(page) >= {"items", "page", "page_size", "total"}
    item = page["items"][0]
    assert item["schedule_id"] == str(seeded.schedule_id)
    assert item["status"] == "ACTIVE"
    # 列表项按契约只带模式/时区/版本等投影；Revision 身份在详情里（`revision_no`）。
    assert item["mode"] == "REMINDER"
    assert item["timezone"] == TZ
    assert item["config_version"] == 1
    assert "intent_key" not in item and "schedule_type" not in item, "v2 已移除旧列"

    detail = await client.get(f"/internal/schedules/{seeded.schedule_id}", headers=headers(tenant))
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["config_version"] == 1
    assert detail.json()["data"]["revision_no"] == 1

    history = await client.get(
        f"/internal/schedules/{seeded.schedule_id}/fires", headers=headers(tenant)
    )
    assert history.status_code == 200, history.text
    fires = history.json()["data"]
    assert fires["total"] == 1
    assert fires["items"][0]["fire_id"] == str(fire)
    assert fires["items"][0]["execution_status"] == "COMPLETED"


async def test_retired_create_and_update_routes_are_gone(
    client: AsyncClient, tenant: TenantContext
) -> None:
    seeded = await seed_schedule(tenant)

    created = await client.post("/internal/schedules", json={}, headers=headers(tenant))
    updated = await client.put(
        f"/internal/schedules/{seeded.schedule_id}", json={}, headers=headers(tenant)
    )

    assert created.status_code == 405, created.text
    assert updated.status_code == 405, updated.text


async def test_actions_require_an_idempotency_key(
    client: AsyncClient, tenant: TenantContext
) -> None:
    seeded = await seed_schedule(tenant)

    response = await client.post(
        f"/internal/schedules/{seeded.schedule_id}/pause",
        json={},
        headers=internal_service_headers(
            tenant.tenant_id, **{"X-Actor-User-Id": str(OWNER)}
        ),
    )

    assert response.status_code == 422, response.text
    assert (await read_schedule(tenant, seeded.schedule_id)).status == "ACTIVE"


async def test_lifecycle_actions_are_owner_scoped(
    client: AsyncClient, tenant: TenantContext
) -> None:
    seeded = await seed_schedule(tenant)

    response = await action(client, tenant, seeded.schedule_id, "pause", actor=uuid.uuid4())

    assert response.status_code == 404, response.text
    assert (await read_schedule(tenant, seeded.schedule_id)).status == "ACTIVE"
