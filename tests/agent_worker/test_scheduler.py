from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from conftest import TenantContext
from helpers import (
    FakeResolver,
    build_resolve_response,
    create_schedule_payload,
    fetch_schedule,
)
from muad_agent_worker.infrastructure.models.task import TaskExecution, TaskSchedule
from muad_agent_worker.scheduler.service import SchedulerLoop, ScheduleService
from muad_api import AppError
from muad_contracts import ScheduleSpec
from sqlalchemy import func, select, update


async def _create_schedule(
    tenant: TenantContext,
    *,
    skill_id: uuid.UUID | None = None,
    agent_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    schedule: ScheduleSpec | None = None,
) -> TaskSchedule:
    payload = create_schedule_payload(
        tenant,
        skill_id=skill_id,
        agent_id=agent_id,
        actor_user_id=actor_user_id,
        schedule=schedule,
    )
    async with tenant.session_factory() as session:
        created = await ScheduleService(session, tenant.settings).create_schedule(
            tenant.tenant_id,
            payload,
        )
        await session.commit()
    return created


async def _set_next_fire_at(
    tenant: TenantContext,
    schedule_id: uuid.UUID,
    next_fire_at: datetime,
) -> None:
    async with tenant.session_factory() as session, session.begin():
        await session.execute(
            update(TaskSchedule)
            .where(TaskSchedule.id == schedule_id)
            .values(next_fire_at=next_fire_at)
        )


async def _schedule_tasks(tenant: TenantContext, schedule_id: uuid.UUID) -> list[TaskExecution]:
    async with tenant.session_factory() as session, session.begin():
        tasks = (
            (
                await session.execute(
                    select(TaskExecution).where(TaskExecution.schedule_id == schedule_id)
                )
            )
            .scalars()
            .all()
        )
    return list(tasks)


async def test_create_cron_schedule_computes_next_fire_in_timezone(tenant: TenantContext) -> None:
    schedule = await _create_schedule(
        tenant,
        schedule=ScheduleSpec(type="CRON", cron="0 9 * * *", timezone="Asia/Shanghai"),
    )
    assert schedule.status == "ACTIVE"
    assert schedule.schedule_type == "CRON"
    assert schedule.cron_expr == "0 9 * * *"
    assert schedule.revision == 1
    assert schedule.next_fire_at is not None
    assert schedule.next_fire_at.tzinfo is not None
    assert schedule.next_fire_at > datetime.now(UTC)
    local = schedule.next_fire_at.astimezone(ZoneInfo("Asia/Shanghai"))
    assert (local.hour, local.minute) == (9, 0)


async def test_create_once_schedule_keeps_run_at(tenant: TenantContext) -> None:
    run_at = datetime.now(UTC) + timedelta(days=1)
    schedule = await _create_schedule(
        tenant,
        schedule=ScheduleSpec(type="ONCE", run_at=run_at, timezone="UTC"),
    )
    assert schedule.schedule_type == "ONCE"
    assert schedule.cron_expr is None
    assert schedule.run_at == run_at
    assert schedule.next_fire_at == run_at


async def test_pause_resume_delete_schedule(tenant: TenantContext) -> None:
    schedule = await _create_schedule(tenant)
    async with tenant.session_factory() as session:
        service = ScheduleService(session, tenant.settings)
        paused = await service.pause_schedule(tenant.tenant_id, schedule.id)
        assert paused.status == "PAUSED"
        # 已 PAUSED 再暂停 → 幂等返回（设计 API-14），不再是 REVISION_CONFLICT
        again = await service.pause_schedule(tenant.tenant_id, schedule.id)
        assert again.status == "PAUSED"
        resumed = await service.resume_schedule(tenant.tenant_id, schedule.id)
        assert resumed.status == "ACTIVE"
        listed, total = await service.list_schedules(tenant.tenant_id)
        assert [item.id for item in listed] == [schedule.id]
        assert total == 1
        await service.delete_schedule(tenant.tenant_id, schedule.id)
        await session.commit()
    async with tenant.session_factory() as session, session.begin():
        service = ScheduleService(session, tenant.settings)
        with pytest.raises(AppError) as exc_info:
            await service.get_schedule(tenant.tenant_id, schedule.id)
        assert exc_info.value.code == "COMMON_NOT_FOUND"
        deleted_items, deleted_total = await service.list_schedules(tenant.tenant_id)
        assert deleted_items == []
        assert deleted_total == 0


async def test_cron_fire_creates_task_once_with_deterministic_key(tenant: TenantContext) -> None:
    now = datetime.now(UTC)
    skill_id = uuid.uuid4()
    artifact_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    schedule = await _create_schedule(
        tenant,
        skill_id=skill_id,
        agent_id=agent_id,
        actor_user_id=actor_id,
    )
    fire_at = now - timedelta(seconds=1)
    await _set_next_fire_at(tenant, schedule.id, fire_at)
    resolver = FakeResolver(build_resolve_response(skill_id, artifact_id))
    loop = SchedulerLoop(tenant.session_factory, resolver, tenant.settings)

    first_id = await loop.run_once(now=now)
    assert first_id is not None
    tasks = await _schedule_tasks(tenant, schedule.id)
    assert len(tasks) == 1
    task = tasks[0]
    assert task.id == first_id
    assert task.idempotency_key == f"schedule:{schedule.id}:{fire_at.isoformat()}"
    assert task.trigger_type == "SCHEDULED"
    assert task.skill_id == skill_id
    assert task.skill_artifact_id == artifact_id
    assert task.snapshot_hash.startswith("sha256:")
    assert task.execution_snapshot_json["agent"]["key"] == "agent"
    assert task.delivery_route_id == schedule.delivery_route_id
    assert resolver.calls == [(agent_id, actor_id, tenant.tenant_id)]

    fired = await fetch_schedule(tenant, schedule.id)
    assert fired.last_fire_at == fire_at
    assert fired.next_fire_at is not None
    assert fired.next_fire_at > now

    await _set_next_fire_at(tenant, schedule.id, fire_at)
    second_id = await loop.run_once(now=now)
    assert second_id is None
    assert len(await _schedule_tasks(tenant, schedule.id)) == 1


async def test_once_schedule_completes_after_fire(tenant: TenantContext) -> None:
    now = datetime.now(UTC)
    run_at = now - timedelta(seconds=1)
    schedule = await _create_schedule(
        tenant,
        schedule=ScheduleSpec(type="ONCE", run_at=run_at, timezone="UTC"),
    )
    resolver = FakeResolver(build_resolve_response(schedule.skill_id, uuid.uuid4()))
    loop = SchedulerLoop(tenant.session_factory, resolver, tenant.settings)
    task_id = await loop.run_once(now=now)
    assert task_id is not None
    fired = await fetch_schedule(tenant, schedule.id)
    assert fired.status == "COMPLETED"
    assert fired.completed_at == now
    assert fired.next_fire_at is None
    assert fired.last_fire_at == run_at
    assert len(await _schedule_tasks(tenant, schedule.id)) == 1


async def test_misfire_beyond_grace_skips_task_and_advances(tenant: TenantContext) -> None:
    now = datetime.now(UTC)
    schedule = await _create_schedule(tenant)
    missed_at = now - timedelta(seconds=tenant.settings.misfire_grace_sec + 30)
    await _set_next_fire_at(tenant, schedule.id, missed_at)
    resolver = FakeResolver(build_resolve_response(schedule.skill_id, uuid.uuid4()))
    loop = SchedulerLoop(tenant.session_factory, resolver, tenant.settings)
    result = await loop.run_once(now=now)
    assert result is None
    assert resolver.calls == []
    assert await _schedule_tasks(tenant, schedule.id) == []
    refreshed = await fetch_schedule(tenant, schedule.id)
    assert refreshed.next_fire_at is not None
    assert refreshed.next_fire_at > now
    assert refreshed.last_fire_at is None


async def test_list_schedules_filters_by_actor(tenant: TenantContext) -> None:
    actor_id = uuid.uuid4()
    await _create_schedule(tenant, actor_user_id=actor_id)
    await _create_schedule(tenant)
    async with tenant.session_factory() as session, session.begin():
        service = ScheduleService(session, tenant.settings)
        listed, total_matched = await service.list_schedules(
            tenant.tenant_id, actor_user_id=actor_id
        )
        assert len(listed) == 1
        assert listed[0].actor_user_id == actor_id
        total = (
            await session.execute(
                select(func.count())
                .select_from(TaskSchedule)
                .where(TaskSchedule.tenant_id == tenant.tenant_id)
            )
        ).scalar_one()
        assert total == 2
