"""B-116 / E-04：Misfire SKIP、ONCE 终态与调度指标（设计 §3.2.3 / §3.5）。

真实边界：真实 SchedulerLoop → 真实 PostgreSQL `task.task_schedule` /
`task.task_execution`；跳过记录、终态与指标都通过真实持久化路径观察，不 mock 存储。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from conftest import TenantContext
from helpers import FakeResolver, build_resolve_response, create_schedule_payload, fetch_schedule
from muad_agent_worker.infrastructure.models.task import TaskExecution, TaskSchedule
from muad_agent_worker.metrics import value
from muad_agent_worker.scheduler.service import (
    SKIP_MISFIRE,
    SchedulerLoop,
    ScheduleService,
)
from muad_contracts import CreateScheduleRequest, ScheduleSpec, UpdateScheduleRequest
from sqlalchemy import select, update

MISFIRE_TOTAL = "scheduled_misfire_total"


async def _create_schedule(
    tenant: TenantContext,
    *,
    schedule: ScheduleSpec | None = None,
) -> TaskSchedule:
    payload = create_schedule_payload(tenant, schedule=schedule)
    async with tenant.session_factory() as session:
        created = await ScheduleService(session, tenant.settings).create_schedule(
            tenant.tenant_id, payload
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


async def _tasks(tenant: TenantContext, schedule_id: uuid.UUID) -> list[TaskExecution]:
    async with tenant.session_factory() as session, session.begin():
        return list(
            (
                await session.execute(
                    select(TaskExecution)
                    .where(TaskExecution.schedule_id == schedule_id)
                    .order_by(TaskExecution.create_time)
                )
            )
            .scalars()
            .all()
        )


def _misfired_at(tenant: TenantContext, now: datetime) -> datetime:
    return now - timedelta(seconds=tenant.settings.misfire_grace_sec + 30)


async def test_b116_cron_misfire_records_latest_skip_and_creates_no_task(
    tenant: TenantContext,
) -> None:
    """CRON 错过触发：不补发、无 Task，最近一次跳过可查原因码/说明/时间。"""
    now = datetime.now(UTC)
    schedule = await _create_schedule(tenant)
    await _set_next_fire_at(tenant, schedule.id, _misfired_at(tenant, now))
    resolver = FakeResolver(build_resolve_response(schedule.skill_id, uuid.uuid4()))
    loop = SchedulerLoop(tenant.session_factory, resolver, tenant.settings)

    assert await loop.run_once(now=now) is None
    assert resolver.calls == [], "错过触发不得进入 resolve（不补发）"
    assert await _tasks(tenant, schedule.id) == [], "错过触发不得补建 Task"

    refreshed = await fetch_schedule(tenant, schedule.id)
    assert refreshed.status == "ACTIVE", "CRON 跳过不是终态"
    assert refreshed.last_fire_at is None
    assert refreshed.next_fire_at is not None and refreshed.next_fire_at > now
    assert refreshed.last_error_code == SKIP_MISFIRE
    assert refreshed.last_error_message
    assert refreshed.last_skipped_at == now


async def test_b116_schedule_contract_has_no_misfire_policy() -> None:
    """V1 仅 SKIP：不存在 misfire_policy 字段/API。"""
    assert "misfire_policy" not in TaskSchedule.__table__.columns
    for contract in (ScheduleSpec, CreateScheduleRequest, UpdateScheduleRequest):
        assert "misfire_policy" not in contract.model_fields


async def test_b116_once_success_completes_exactly_once_with_completed_at(
    tenant: TenantContext,
) -> None:
    """ONCE 成功触发恰好创建一个 Task 并进入 COMPLETED，completed_at 非空。"""
    now = datetime.now(UTC)
    run_at = now - timedelta(seconds=1)
    schedule = await _create_schedule(
        tenant, schedule=ScheduleSpec(type="ONCE", run_at=run_at, timezone="UTC")
    )
    resolver = FakeResolver(build_resolve_response(schedule.skill_id, uuid.uuid4()))
    loop = SchedulerLoop(tenant.session_factory, resolver, tenant.settings)

    first_id = await loop.run_once(now=now)
    assert first_id is not None

    tasks = await _tasks(tenant, schedule.id)
    assert len(tasks) == 1
    refreshed = await fetch_schedule(tenant, schedule.id)
    assert refreshed.status == "COMPLETED"
    assert refreshed.completed_at == now
    assert refreshed.next_fire_at is None
    assert refreshed.last_fire_at == run_at
    assert refreshed.last_error_code is None
    assert refreshed.last_skipped_at is None

    assert await loop.run_once(now=now + timedelta(seconds=1)) is None
    assert len(await _tasks(tenant, schedule.id)) == 1


async def test_b116_missed_once_is_terminal_missed_not_completed(
    tenant: TenantContext,
) -> None:
    """ONCE 错过触发：终态 MISSED，completed_at/next_fire_at 均为 NULL，不冒充完成。"""
    now = datetime.now(UTC)
    schedule = await _create_schedule(
        tenant,
        schedule=ScheduleSpec(type="ONCE", run_at=_misfired_at(tenant, now), timezone="UTC"),
    )
    resolver = FakeResolver(build_resolve_response(schedule.skill_id, uuid.uuid4()))
    loop = SchedulerLoop(tenant.session_factory, resolver, tenant.settings)

    assert await loop.run_once(now=now) is None
    assert resolver.calls == []
    assert await _tasks(tenant, schedule.id) == []

    refreshed = await fetch_schedule(tenant, schedule.id)
    assert refreshed.status == "MISSED"
    assert refreshed.completed_at is None, "错过不是完成"
    assert refreshed.next_fire_at is None
    assert refreshed.last_fire_at is None
    assert refreshed.last_error_code == SKIP_MISFIRE
    assert refreshed.last_skipped_at == now

    assert await loop.run_once(now=now + timedelta(seconds=1)) is None
    assert await _tasks(tenant, schedule.id) == []


async def test_e04_cron_misfire_stays_active_and_increments_misfire_total(
    tenant: TenantContext,
) -> None:
    """E-04：CRON 错过触发不补发，记最近一次跳过并累加 scheduled_misfire_total。"""
    before = value(MISFIRE_TOTAL)
    now = datetime.now(UTC)
    schedule = await _create_schedule(tenant)
    await _set_next_fire_at(tenant, schedule.id, _misfired_at(tenant, now))
    resolver = FakeResolver(build_resolve_response(schedule.skill_id, uuid.uuid4()))
    loop = SchedulerLoop(tenant.session_factory, resolver, tenant.settings)

    assert await loop.run_once(now=now) is None

    assert value(MISFIRE_TOTAL) == before + 1, "跳过必须累加 scheduled_misfire_total"
    assert await _tasks(tenant, schedule.id) == []
    refreshed = await fetch_schedule(tenant, schedule.id)
    assert refreshed.status == "ACTIVE"
    assert refreshed.last_skipped_at == now
    assert refreshed.last_error_code == SKIP_MISFIRE
    assert refreshed.next_fire_at is not None and refreshed.next_fire_at > now


async def test_e04_missed_once_is_terminal_missed_and_increments_misfire_total(
    tenant: TenantContext,
) -> None:
    """E-04：ONCE 错过触发进入不可恢复终态 MISSED，并累加 scheduled_misfire_total。"""
    before = value(MISFIRE_TOTAL)
    now = datetime.now(UTC)
    schedule = await _create_schedule(
        tenant,
        schedule=ScheduleSpec(type="ONCE", run_at=_misfired_at(tenant, now), timezone="UTC"),
    )
    resolver = FakeResolver(build_resolve_response(schedule.skill_id, uuid.uuid4()))
    loop = SchedulerLoop(tenant.session_factory, resolver, tenant.settings)

    assert await loop.run_once(now=now) is None

    assert value(MISFIRE_TOTAL) == before + 1
    refreshed = await fetch_schedule(tenant, schedule.id)
    assert refreshed.status == "MISSED"
    assert refreshed.completed_at is None
    assert refreshed.next_fire_at is None
    assert await _tasks(tenant, schedule.id) == []
