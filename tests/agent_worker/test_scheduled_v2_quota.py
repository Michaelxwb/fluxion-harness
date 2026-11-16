"""用户维度调度配额的原子边界（scheduled-task-v2 TASK-005 / B-04）。

真实边界：真实 PostgreSQL（每用例克隆的 `muad_tst_*` 库）与真实的
`pg_advisory_xact_lock` 语义；并发用例用**两个独立会话/事务**真正抢同一份额度，
单会话断言证明不了锁。

覆盖：Schedule 数量/最小间隔的 Draft 边界拒绝；Fire 的日触发、日 Agentic、并发 Agentic
复检与 `SKIPPED(QUOTA)` 记录；本地自然日窗口；租户/用户隔离（不是全库 count）；回滚不泄漏
额度；等待/续接不算新触发；设置只在后续边界生效。
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from muad_agent_worker.application.schedule_quota import (
    ScheduleQuota,
    admit_fire,
    count_open_schedules,
    ensure_min_interval,
    ensure_schedule_capacity,
    local_day_window,
    record_quota_skip,
)
from muad_agent_worker.infrastructure.models.task import (
    ScheduleFire,
    TaskExecution,
    TaskSchedule,
    TaskScheduleRevision,
)
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts.platform_settings import parse_platform_settings
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_worker.conftest import TenantContext

pytestmark = pytest.mark.integration

SHANGHAI = "Asia/Shanghai"
#: 2026-11-15 01:00 Asia/Shanghai（UTC 仍是 11-14，用来区分本地日与 UTC 日）。
LOCAL_DAY_START = datetime(2026, 11, 14, 17, 0, tzinfo=UTC)


def _quota(**overrides: int) -> ScheduleQuota:
    return ScheduleQuota.from_settings(parse_platform_settings({"schedule": overrides}))


def _fire_at(offset: timedelta) -> datetime:
    return LOCAL_DAY_START + offset


async def _seed_schedule(
    session: AsyncSession,
    tenant_id: str,
    owner_user_id: uuid.UUID,
    *,
    mode: str = "REMINDER",
    timezone: str = SHANGHAI,
    status: str = "ACTIVE",
) -> tuple[TaskSchedule, TaskScheduleRevision]:
    agent_id = uuid.uuid4()
    schedule = TaskSchedule(
        tenant_id=tenant_id,
        owner_user_id=owner_user_id,
        agent_id=agent_id,
        name="quota-schedule",
        status=status,
        created_by=owner_user_id,
    )
    session.add(schedule)
    await session.flush()
    revision = TaskScheduleRevision(
        tenant_id=tenant_id,
        schedule_id=schedule.id,
        revision_no=1,
        mode=mode,
        instruction_raw="每天 9 点",
        normalized_goal="每天 9 点提醒",
        recurrence_kind="CRON",
        cron_expr="0 9 * * *",
        timezone=timezone,
        created_by=owner_user_id,
    )
    session.add(revision)
    await session.flush()
    return schedule, revision


def _add_fire(
    session: AsyncSession,
    schedule: TaskSchedule,
    revision: TaskScheduleRevision,
    scheduled_at: datetime,
    *,
    status: str = "CREATED",
    skip_reason: str | None = None,
) -> ScheduleFire:
    fire = ScheduleFire(
        tenant_id=schedule.tenant_id,
        schedule_id=schedule.id,
        schedule_revision_id=revision.id,
        scheduled_at=scheduled_at,
        status=status,
        skip_reason=skip_reason,
    )
    session.add(fire)
    return fire


def _add_execution(
    session: AsyncSession,
    schedule: TaskSchedule,
    *,
    status: str,
    task_type: str = "AGENTIC",
) -> TaskExecution:
    execution = TaskExecution(
        tenant_id=schedule.tenant_id,
        schedule_id=schedule.id,
        agent_id=schedule.agent_id,
        actor_user_id=schedule.owner_user_id,
        task_type=task_type,
        trigger_type="SCHEDULED",
        status=status,
        input_json={},
        execution_snapshot_json={},
        snapshot_hash=f"test-{uuid.uuid4()}",
        idempotency_key=f"test-{uuid.uuid4()}",
    )
    session.add(execution)
    return execution


async def _count_fires(session: AsyncSession, tenant_id: str) -> int:
    total = await session.scalar(
        select(func.count()).select_from(ScheduleFire).where(ScheduleFire.tenant_id == tenant_id)
    )
    return int(total or 0)


@pytest.mark.unit
def test_local_day_window_uses_the_schedule_timezone() -> None:
    start, end = local_day_window(LOCAL_DAY_START, SHANGHAI)
    assert start == datetime(2026, 11, 14, 16, 0, tzinfo=UTC)
    assert end == datetime(2026, 11, 15, 16, 0, tzinfo=UTC)
    with pytest.raises(ValueError):
        local_day_window(datetime(2026, 11, 15, 9, 0), SHANGHAI)
    with pytest.raises(ValueError):
        local_day_window(LOCAL_DAY_START, "Not/AZone")


async def test_schedule_capacity_counts_only_open_owned_schedules(tenant: TenantContext) -> None:
    owner = uuid.uuid4()
    quota = _quota(max_schedules_per_user=2)
    async with tenant.session_factory() as session:
        async with session.begin():
            await _seed_schedule(session, tenant.tenant_id, owner, status="ACTIVE")
            await _seed_schedule(session, tenant.tenant_id, owner, status="PAUSED")
            # 终态不占名额。
            await _seed_schedule(session, tenant.tenant_id, owner, status="ENDED")
            await _seed_schedule(session, tenant.tenant_id, owner, status="CANCELLED")
            assert await count_open_schedules(
                session, tenant_id=tenant.tenant_id, owner_user_id=owner
            ) == 2
            with pytest.raises(AppError) as rejected:
                await ensure_schedule_capacity(
                    session, tenant_id=tenant.tenant_id, owner_user_id=owner, quota=quota
                )
            assert rejected.value.code == str(ErrorCode.SCHEDULE_QUOTA_EXCEEDED)


async def test_schedule_capacity_is_scoped_and_never_a_global_count(
    tenant: TenantContext,
) -> None:
    owner = uuid.uuid4()
    quota = _quota(max_schedules_per_user=1)
    async with tenant.session_factory() as session:
        async with session.begin():
            # 同租户其他用户、其他租户同 UUID 的存量都不允许遮蔽本用户额度。
            for index in range(3):
                await _seed_schedule(session, tenant.tenant_id, uuid.uuid4())
                await _seed_schedule(session, f"{tenant.tenant_id}-other-{index}", owner)
            assert await ensure_schedule_capacity(
                session, tenant_id=tenant.tenant_id, owner_user_id=owner, quota=quota
            ) == 0


@pytest.mark.unit
def test_min_interval_is_a_draft_boundary_rejection() -> None:
    quota = _quota(min_schedule_interval_sec=3600)
    occurrences = [_fire_at(timedelta(0)), _fire_at(timedelta(minutes=5))]
    with pytest.raises(AppError) as rejected:
        ensure_min_interval(occurrences, quota=quota)
    assert rejected.value.code == str(ErrorCode.SCHEDULE_QUOTA_EXCEEDED)
    ensure_min_interval([_fire_at(timedelta(0)), _fire_at(timedelta(hours=1))], quota=quota)
    ensure_min_interval([_fire_at(timedelta(0))], quota=quota)
    with pytest.raises(ValueError):
        ensure_min_interval([datetime(2026, 11, 15, 9, 0)] * 2, quota=quota)


async def test_daily_fire_quota_uses_the_local_day_and_records_skips(
    tenant: TenantContext,
) -> None:
    owner = uuid.uuid4()
    quota = _quota(max_fires_per_day_per_user=1)
    factory = tenant.session_factory
    async with factory() as session:
        async with session.begin():
            schedule, revision = await _seed_schedule(session, tenant.tenant_id, owner)
            first = _fire_at(timedelta(hours=1))
            assert (await admit_fire(
                session, schedule=schedule, revision=revision, scheduled_at=first, quota=quota
            )).allowed
            _add_fire(session, schedule, revision, first)
            # 同一本地日、不同 UTC 日：仍受同一个日额度约束。
            same_local_day = _fire_at(timedelta(hours=22))
            denied = await admit_fire(
                session,
                schedule=schedule,
                revision=revision,
                scheduled_at=same_local_day,
                quota=quota,
            )
            assert not denied.allowed and denied.denied_by == "DAILY_FIRES"
            assert await record_quota_skip(
                session, schedule=schedule, revision=revision, scheduled_at=same_local_day
            )
            # 跳过与取消不是一次业务触发，不占日额度。
            _add_fire(
                session, schedule, revision, _fire_at(timedelta(hours=2)), status="SKIPPED",
                skip_reason="MISFIRE",
            )
            _add_fire(session, schedule, revision, _fire_at(timedelta(hours=3)), status="CANCELLED")
        async with session.begin():
            skipped = await session.scalar(
                select(ScheduleFire).where(
                    ScheduleFire.schedule_id == schedule.id,
                    ScheduleFire.scheduled_at == same_local_day,
                )
            )
            assert skipped is not None
            assert (skipped.status, skipped.skip_reason) == ("SKIPPED", "QUOTA")
            still_denied = await admit_fire(
                session,
                schedule=await session.get(TaskSchedule, schedule.id),
                revision=await session.get(TaskScheduleRevision, revision.id),
                scheduled_at=_fire_at(timedelta(hours=20)),
                quota=quota,
            )
            assert not still_denied.allowed, "跳过/取消的 Fire 不得被算成业务触发"
            # 次日本地零点之后恢复可用。
            next_day = _fire_at(timedelta(hours=25, minutes=30))
            assert (await admit_fire(
                session,
                schedule=await session.get(TaskSchedule, schedule.id),
                revision=await session.get(TaskScheduleRevision, revision.id),
                scheduled_at=next_day,
                quota=quota,
            )).allowed


async def test_agentic_daily_and_concurrency_limits_are_separate(
    tenant: TenantContext,
) -> None:
    owner = uuid.uuid4()
    quota = _quota(
        max_fires_per_day_per_user=10,
        max_agentic_runs_per_day_per_user=1,
        max_concurrent_agentic_executions_per_user=1,
    )
    factory = tenant.session_factory
    async with factory() as session:
        async with session.begin():
            agentic, agentic_revision = await _seed_schedule(
                session, tenant.tenant_id, owner, mode="AGENTIC"
            )
            reminder, reminder_revision = await _seed_schedule(session, tenant.tenant_id, owner)
            first = _fire_at(timedelta(hours=1))
            assert (await admit_fire(
                session,
                schedule=agentic,
                revision=agentic_revision,
                scheduled_at=first,
                quota=quota,
            )).allowed
            _add_fire(session, agentic, agentic_revision, first)
            denied = await admit_fire(
                session,
                schedule=agentic,
                revision=agentic_revision,
                scheduled_at=first + timedelta(hours=2),
                quota=quota,
            )
            assert not denied.allowed and denied.denied_by == "AGENTIC_DAILY"
            # Agentic 日额度不影响同用户的 REMINDER 触发。
            assert (await admit_fire(
                session,
                schedule=reminder,
                revision=reminder_revision,
                scheduled_at=first + timedelta(hours=3),
                quota=quota,
            )).allowed


async def test_agentic_concurrency_counts_open_executions_only(tenant: TenantContext) -> None:
    owner = uuid.uuid4()
    quota = _quota(
        max_agentic_runs_per_day_per_user=10,
        max_concurrent_agentic_executions_per_user=1,
    )
    factory = tenant.session_factory
    async with factory() as session:
        async with session.begin():
            schedule, revision = await _seed_schedule(
                session, tenant.tenant_id, owner, mode="AGENTIC"
            )
            waiting = _add_execution(session, schedule, status="WAITING_HUMAN")
            _add_execution(session, schedule, status="WAITING_EXTERNAL")
            await session.flush()
            occurrence = _fire_at(timedelta(hours=1))
            # 等待中的执行不占 Worker 槽，也不产生新 Fire。
            assert (await admit_fire(
                session, schedule=schedule, revision=revision, scheduled_at=occurrence, quota=quota
            )).allowed
            waiting.status = "RUNNING"
            await session.flush()
            denied = await admit_fire(
                session,
                schedule=schedule,
                revision=revision,
                scheduled_at=occurrence + timedelta(hours=1),
                quota=quota,
            )
            assert not denied.allowed and denied.denied_by == "AGENTIC_CONCURRENCY"
            waiting.status = "COMPLETED"
            await session.flush()
            assert (await admit_fire(
                session,
                schedule=schedule,
                revision=revision,
                scheduled_at=occurrence + timedelta(hours=2),
                quota=quota,
            )).allowed


async def test_two_schedulers_racing_for_the_last_daily_fire_yield_one_skip(
    tenant: TenantContext,
) -> None:
    owner = uuid.uuid4()
    quota = _quota(max_fires_per_day_per_user=1)
    factory = tenant.session_factory
    async with factory() as session:
        async with session.begin():
            first_schedule, first_revision = await _seed_schedule(session, tenant.tenant_id, owner)
            second_schedule, second_revision = await _seed_schedule(session, tenant.tenant_id, owner)
    occurrence = _fire_at(timedelta(hours=1))
    lock_held = asyncio.Event()
    release = asyncio.Event()

    async def attempt(
        schedule_id: uuid.UUID, revision_id: uuid.UUID, hold: bool
    ) -> bool:
        async with factory() as session:
            async with session.begin():
                schedule = await session.get(TaskSchedule, schedule_id)
                revision = await session.get(TaskScheduleRevision, revision_id)
                decision = await admit_fire(
                    session, schedule=schedule, revision=revision, scheduled_at=occurrence, quota=quota
                )
                if decision.allowed:
                    _add_fire(session, schedule, revision, occurrence)
                    await session.flush()
                else:
                    await record_quota_skip(
                        session, schedule=schedule, revision=revision, scheduled_at=occurrence
                    )
                if hold:
                    lock_held.set()
                    await release.wait()
                return decision.allowed

    first = asyncio.create_task(attempt(first_schedule.id, first_revision.id, hold=True))
    await asyncio.wait_for(lock_held.wait(), timeout=5)
    second = asyncio.create_task(attempt(second_schedule.id, second_revision.id, hold=False))
    try:
        await asyncio.sleep(0.3)
        assert not second.done(), "第二个 Scheduler 没有被用户配额锁挡住（单会话断言证明不了原子性）"
    finally:
        release.set()
    results = sorted(await asyncio.gather(first, second))
    assert results == [False, True], "最后一份日额度必须恰好被一方拿到"
    async with factory() as session:
        statuses = await session.scalars(
            select(ScheduleFire.status).where(ScheduleFire.tenant_id == tenant.tenant_id)
        )
        assert sorted(statuses) == ["CREATED", "SKIPPED"]


async def test_concurrent_schedule_creation_cannot_exceed_user_capacity(
    tenant: TenantContext,
) -> None:
    owner = uuid.uuid4()
    quota = _quota(max_schedules_per_user=1)
    factory = tenant.session_factory
    lock_held = asyncio.Event()
    release = asyncio.Event()

    async def attempt(hold: bool) -> bool:
        async with factory() as session:
            async with session.begin():
                try:
                    await ensure_schedule_capacity(
                        session, tenant_id=tenant.tenant_id, owner_user_id=owner, quota=quota
                    )
                except AppError:
                    return False
                await _seed_schedule(session, tenant.tenant_id, owner)
                if hold:
                    lock_held.set()
                    await release.wait()
                return True

    first = asyncio.create_task(attempt(hold=True))
    await asyncio.wait_for(lock_held.wait(), timeout=5)
    second = asyncio.create_task(attempt(hold=False))
    try:
        await asyncio.sleep(0.3)
        assert not second.done(), "第二个创建事务没有被用户配额锁挡住"
    finally:
        release.set()
    results = sorted(await asyncio.gather(first, second))
    assert results == [False, True]
    async with factory() as session:
        assert await count_open_schedules(
            session, tenant_id=tenant.tenant_id, owner_user_id=owner
        ) == 1


async def test_rolled_back_fire_and_skip_do_not_leak_quota(tenant: TenantContext) -> None:
    owner = uuid.uuid4()
    quota = _quota(max_fires_per_day_per_user=1)
    factory = tenant.session_factory
    async with factory() as session:
        async with session.begin():
            schedule, revision = await _seed_schedule(session, tenant.tenant_id, owner)
    occurrence = _fire_at(timedelta(hours=1))
    session = factory()
    try:
        loaded = await session.get(TaskSchedule, schedule.id)
        loaded_revision = await session.get(TaskScheduleRevision, revision.id)
        decision = await admit_fire(
            session, schedule=loaded, revision=loaded_revision, scheduled_at=occurrence, quota=quota
        )
        assert decision.allowed
        _add_fire(session, loaded, loaded_revision, occurrence)
        await session.flush()
        assert await _count_fires(session, tenant.tenant_id) == 1
        await session.rollback()
    finally:
        await session.close()
    async with factory() as session:
        async with session.begin():
            assert await _count_fires(session, tenant.tenant_id) == 0
            loaded = await session.get(TaskSchedule, schedule.id)
            loaded_revision = await session.get(TaskScheduleRevision, revision.id)
            assert (await admit_fire(
                session, schedule=loaded, revision=loaded_revision, scheduled_at=occurrence, quota=quota
            )).allowed
            _add_fire(session, loaded, loaded_revision, occurrence)
            await session.flush()
            assert not (await admit_fire(
                session,
                schedule=loaded,
                revision=loaded_revision,
                scheduled_at=occurrence + timedelta(hours=1),
                quota=quota,
            )).allowed


async def test_frozen_quota_only_affects_later_boundaries(tenant: TenantContext) -> None:
    owner = uuid.uuid4()
    factory = tenant.session_factory
    async with factory() as session:
        async with session.begin():
            schedule, revision = await _seed_schedule(session, tenant.tenant_id, owner)
    first_occurrence = _fire_at(timedelta(hours=1))
    async with factory() as session:
        async with session.begin():
            loaded = await session.get(TaskSchedule, schedule.id)
            loaded_revision = await session.get(TaskScheduleRevision, revision.id)
            assert (await admit_fire(
                session, schedule=loaded, revision=loaded_revision,
                scheduled_at=first_occurrence, quota=_quota(max_fires_per_day_per_user=1),
            )).allowed
            _add_fire(session, loaded, loaded_revision, first_occurrence)
    async with factory() as session:
        async with session.begin():
            loaded = await session.get(TaskSchedule, schedule.id)
            loaded_revision = await session.get(TaskScheduleRevision, revision.id)
            # 更宽的设置只对后续边界生效：已提交的 Fire 不被回溯改写。
            assert (await admit_fire(
                session, schedule=loaded, revision=loaded_revision,
                scheduled_at=first_occurrence + timedelta(hours=1),
                quota=_quota(max_fires_per_day_per_user=2),
            )).allowed
            fires = (await session.scalars(
                select(ScheduleFire).where(ScheduleFire.schedule_id == schedule.id)
            )).all()
            assert [(fire.scheduled_at, fire.status) for fire in fires] == [
                (first_occurrence, "CREATED")
            ]
