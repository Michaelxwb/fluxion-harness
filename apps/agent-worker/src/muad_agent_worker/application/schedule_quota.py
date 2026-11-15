"""用户维度调度配额（scheduled-task-v2 design §3.5.4 / §3.5.5 / §3.5.11）。

边界分工：

- **Draft/创建边界**（静态）：Schedule 数量上限与最小间隔在创建前校验，超限抛
  `SCHEDULE_QUOTA_EXCEEDED`（429）；不写 ACTIVE，也不写半条 Schedule。
- **每次 Fire 的 admission 边界**（运行时）：日触发、日 Agentic 运行、并发 Agentic 执行
  在 Fire 事务内复检。超限不静默——同一事务写一条 `SKIPPED(skip_reason=QUOTA)` Fire。
  事件（FIRE `SKIPPED`）由共享实体事件写入方在调用方事务内追加，本模块只返回记录结果。
- **原子性**：用户维度用 `pg_advisory_xact_lock(hash(tenant,user,'quota'))` 串行化。只锁
  Schedule 行拦不住"同一用户的两个不同 Schedule 同时抢最后一个额度"；日计数必须在锁内、
  按 `(tenant_id, owner_user_id)` 查询（绝不使用全库 count）。
- **自然日**：按 Schedule Revision 的 IANA 时区切本地自然日，窗口作用在 occurrence 的
  `scheduled_at` 上——晚到/补发的 Fire 记在它所属的计划日，不按执行机器所在日重算。
- **设置只影响后续边界**：配额在调用边界由 `ScheduleQuota.from_settings` 冻结；已提交的
  Fire/Execution 不因设置变化回溯，计数也不"重取"设置。
- **等待与续接不是新触发**：日触发按 Fire 行计数；Human/External 等待与续接不新增 Fire，
  因此不消耗日触发额度。并发只统计占执行槽的 `QUEUED`/`RUNNING`（等待不占 Worker 槽）。

锁序：advisory 用户配额锁总是在 Schedule 行锁**之内**获取，任何调用方不得反向先取它。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts.enums import FireStatus, ScheduleMode, ScheduleStatus, SkipReason, TaskStatus, TaskType
from muad_contracts.platform_settings import PlatformSettings
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.task import ScheduleFire, TaskExecution, TaskSchedule, TaskScheduleRevision

logger = logging.getLogger(__name__)

#: advisory 锁身份前缀：同集群内与其它业务锁隔离（键 = tenant + owner + 本语义）。
QUOTA_LOCK_NAMESPACE = "muad:schedule-quota"

#: 终态 Schedule 不再占用用户名额（ENDED/CANCELLED 只读历史）。
TERMINAL_SCHEDULE_STATUSES = (str(ScheduleStatus.ENDED), str(ScheduleStatus.CANCELLED))

#: 计入"业务触发"的 Fire 状态：SKIPPED/CANCELLED 不是一次真实尝试。
COUNTED_FIRE_STATUSES = (
    str(FireStatus.CREATED),
    str(FireStatus.EXECUTION_CREATED),
    str(FireStatus.FAILED_ADMISSION),
)

#: 占执行槽的执行状态；WAITING_* 等待中不占 Worker 槽。
OPEN_EXECUTION_STATUSES = (str(TaskStatus.QUEUED), str(TaskStatus.RUNNING))

AGENTIC_MODE = str(ScheduleMode.AGENTIC)
AGENTIC_TASK_TYPE = str(TaskType.AGENTIC)


class QuotaLimit(StrEnum):
    """运行时配额拒绝的具体维度（事件/日志用；Fire 上统一记 `QUOTA`）。"""

    DAILY_FIRES = "DAILY_FIRES"
    AGENTIC_DAILY = "AGENTIC_DAILY"
    AGENTIC_CONCURRENCY = "AGENTIC_CONCURRENCY"


@dataclass(frozen=True, slots=True)
class ScheduleQuota:
    """一个操作边界冻结的用户调度配额（来自平台设置 `schedule` 分组）。"""

    max_schedules_per_user: int
    min_schedule_interval_sec: int
    max_fires_per_day_per_user: int
    max_agentic_runs_per_day_per_user: int
    max_concurrent_agentic_executions_per_user: int

    @classmethod
    def from_settings(cls, settings: PlatformSettings) -> ScheduleQuota:
        source = settings.schedule
        return cls(
            max_schedules_per_user=source.max_schedules_per_user,
            min_schedule_interval_sec=source.min_schedule_interval_sec,
            max_fires_per_day_per_user=source.max_fires_per_day_per_user,
            max_agentic_runs_per_day_per_user=source.max_agentic_runs_per_day_per_user,
            max_concurrent_agentic_executions_per_user=(
                source.max_concurrent_agentic_executions_per_user
            ),
        )


@dataclass(frozen=True, slots=True)
class FireAdmission:
    """一次 Fire admission 的配额结论；拒绝时 `denied_by` 指出维度。"""

    allowed: bool
    denied_by: QuotaLimit | None = None
    used: int = 0
    limit: int = 0


def local_day_window(moment: datetime, timezone: str) -> tuple[datetime, datetime]:
    """`moment` 所属本地自然日对应的 UTC 半开区间 `[start, end)`。"""
    if moment.tzinfo is None:
        raise ValueError("occurrence must be timezone-aware")
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"invalid IANA timezone: {timezone!r}") from exc
    local_date = moment.astimezone(zone).date()
    start = datetime.combine(local_date, time.min, tzinfo=zone).astimezone(UTC)
    end = datetime.combine(local_date + timedelta(days=1), time.min, tzinfo=zone).astimezone(UTC)
    return start, end


async def _lock_user_quota(
    session: AsyncSession, *, tenant_id: str, owner_user_id: uuid.UUID
) -> None:
    """串行化同一用户的配额判定与计数提交（事务级 advisory 锁，事务结束自动释放）。"""
    identity = f"{QUOTA_LOCK_NAMESPACE}:{tenant_id}:{owner_user_id}"
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
        {"identity": identity},
    )


async def count_open_schedules(
    session: AsyncSession, *, tenant_id: str, owner_user_id: uuid.UUID
) -> int:
    """该用户当前占名的 Schedule 数（同租户、未删除、非终态）。"""
    total = await session.scalar(
        select(func.count())
        .select_from(TaskSchedule)
        .where(
            TaskSchedule.tenant_id == tenant_id,
            TaskSchedule.owner_user_id == owner_user_id,
            TaskSchedule.is_deleted.is_(False),
            TaskSchedule.status.not_in(TERMINAL_SCHEDULE_STATUSES),
        )
    )
    return int(total or 0)


async def ensure_schedule_capacity(
    session: AsyncSession, *, tenant_id: str, owner_user_id: uuid.UUID, quota: ScheduleQuota
) -> int:
    """创建/确认边界占用一个名额；超限抛 `SCHEDULE_QUOTA_EXCEEDED`。返回占用前数量。"""
    await _lock_user_quota(session, tenant_id=tenant_id, owner_user_id=owner_user_id)
    used = await count_open_schedules(session, tenant_id=tenant_id, owner_user_id=owner_user_id)
    if used >= quota.max_schedules_per_user:
        raise AppError(ErrorCode.SCHEDULE_QUOTA_EXCEEDED, data={"limit": "SCHEDULES"})
    return used


def ensure_min_interval(occurrences: Sequence[datetime], *, quota: ScheduleQuota) -> None:
    """相邻本地计划槽间隔不得小于配置值；单次任务没有相邻槽。"""
    for previous, following in zip(occurrences, occurrences[1:], strict=False):
        if previous.tzinfo is None or following.tzinfo is None:
            raise ValueError("occurrences must be timezone-aware")
        if (following - previous).total_seconds() < quota.min_schedule_interval_sec:
            raise AppError(ErrorCode.SCHEDULE_QUOTA_EXCEEDED, data={"limit": "MIN_INTERVAL"})


async def _count_daily_fires(
    session: AsyncSession,
    *,
    tenant_id: str,
    owner_user_id: uuid.UUID,
    window: tuple[datetime, datetime],
    agentic_only: bool,
) -> int:
    start, end = window
    statement = (
        select(func.count())
        .select_from(ScheduleFire)
        .join(TaskSchedule, TaskSchedule.id == ScheduleFire.schedule_id)
        .where(
            ScheduleFire.tenant_id == tenant_id,
            ScheduleFire.is_deleted.is_(False),
            ScheduleFire.scheduled_at >= start,
            ScheduleFire.scheduled_at < end,
            ScheduleFire.status.in_(COUNTED_FIRE_STATUSES),
            TaskSchedule.tenant_id == tenant_id,
            TaskSchedule.owner_user_id == owner_user_id,
            TaskSchedule.is_deleted.is_(False),
        )
    )
    if agentic_only:
        statement = statement.join(
            TaskScheduleRevision,
            TaskScheduleRevision.id == ScheduleFire.schedule_revision_id,
        ).where(TaskScheduleRevision.mode == AGENTIC_MODE)
    return int(await session.scalar(statement) or 0)


async def _count_open_agentic_executions(
    session: AsyncSession, *, tenant_id: str, owner_user_id: uuid.UUID
) -> int:
    total = await session.scalar(
        select(func.count())
        .select_from(TaskExecution)
        .where(
            TaskExecution.tenant_id == tenant_id,
            TaskExecution.actor_user_id == owner_user_id,
            TaskExecution.is_deleted.is_(False),
            TaskExecution.task_type == AGENTIC_TASK_TYPE,
            TaskExecution.status.in_(OPEN_EXECUTION_STATUSES),
        )
    )
    return int(total or 0)


async def admit_fire(
    session: AsyncSession,
    *,
    schedule: TaskSchedule,
    revision: TaskScheduleRevision,
    scheduled_at: datetime,
    quota: ScheduleQuota,
) -> FireAdmission:
    """Fire 事务内的用户配额复检；必须在写 Fire/Execution 的同一事务里调用。"""
    await _lock_user_quota(
        session, tenant_id=schedule.tenant_id, owner_user_id=schedule.owner_user_id
    )
    window = local_day_window(scheduled_at, revision.timezone)
    daily = await _count_daily_fires(
        session,
        tenant_id=schedule.tenant_id,
        owner_user_id=schedule.owner_user_id,
        window=window,
        agentic_only=False,
    )
    if daily >= quota.max_fires_per_day_per_user:
        return _denied(QuotaLimit.DAILY_FIRES, used=daily, limit=quota.max_fires_per_day_per_user)
    if revision.mode != AGENTIC_MODE:
        return FireAdmission(allowed=True, used=daily, limit=quota.max_fires_per_day_per_user)
    agentic_daily = await _count_daily_fires(
        session,
        tenant_id=schedule.tenant_id,
        owner_user_id=schedule.owner_user_id,
        window=window,
        agentic_only=True,
    )
    if agentic_daily >= quota.max_agentic_runs_per_day_per_user:
        return _denied(
            QuotaLimit.AGENTIC_DAILY,
            used=agentic_daily,
            limit=quota.max_agentic_runs_per_day_per_user,
        )
    concurrent = await _count_open_agentic_executions(
        session, tenant_id=schedule.tenant_id, owner_user_id=schedule.owner_user_id
    )
    if concurrent >= quota.max_concurrent_agentic_executions_per_user:
        return _denied(
            QuotaLimit.AGENTIC_CONCURRENCY,
            used=concurrent,
            limit=quota.max_concurrent_agentic_executions_per_user,
        )
    return FireAdmission(allowed=True, used=daily, limit=quota.max_fires_per_day_per_user)


def _denied(denied_by: QuotaLimit, *, used: int, limit: int) -> FireAdmission:
    return FireAdmission(allowed=False, denied_by=denied_by, used=used, limit=limit)


async def record_quota_skip(
    session: AsyncSession,
    *,
    schedule: TaskSchedule,
    revision: TaskScheduleRevision,
    scheduled_at: datetime,
) -> bool:
    """写一条 `SKIPPED(QUOTA)` Fire（与 admission 同一事务；唯一键冲突视为已处理）。

    返回是否新插入。FIRE `SKIPPED` 审计事件由调用方用共享实体事件写入方在同事务追加；
    Fire 行本身即权限内的可见记录，不做静默丢弃。
    """
    fire_id = await session.scalar(
        pg_insert(ScheduleFire)
        .values(
            tenant_id=schedule.tenant_id,
            schedule_id=schedule.id,
            schedule_revision_id=revision.id,
            scheduled_at=scheduled_at,
            status=str(FireStatus.SKIPPED),
            skip_reason=str(SkipReason.QUOTA),
            missed_count=0,
        )
        .on_conflict_do_nothing(constraint="uq_schedule_fire_occurrence")
        .returning(ScheduleFire.id)
    )
    inserted = fire_id is not None
    if inserted:
        logger.warning(
            "schedule_fire_quota_skipped",
            extra={
                "tenant_id": schedule.tenant_id,
                "schedule_id": str(schedule.id),
                "scheduled_at": scheduled_at.isoformat(),
            },
        )
    return inserted
