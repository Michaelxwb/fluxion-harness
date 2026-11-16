"""Scheduler：短事务选 due、锁外准入、锁内 CAS 写 Fire/Execution/进度（design §3.2.9）。

一轮 `run_due` 的形状固定：

1. **短事务**按 `(next_fire_at, tenant_id)` 选出到期的 ACTIVE Schedule，只拿 id/身份/指针，
   事务结束即释放锁；
2. 读当前 Revision，用 `recurrence` 纯计算本次 occurrence（先判 DST gap，再判 misfire）；
3. **事务外**取平台设置并做 Admission（`resolve-definition` 类的网络调用）；
4. **短事务**里 `SELECT ... FOR UPDATE` 复核 `status/current_revision_id/next_fire_at`（CAS），
   再交给 `scheduler.fire.write_fire` 写唯一 Fire、冻结 Execution 并推进进度。

锁外准入是硬约束：解析/授权/凭据是网络调用，持行锁做它们会把同一 Schedule 的生命周期动作
（暂停/取消/更新）钉在网络上。竞态里暂停/更新/取消赢 ⇒ 本轮的准入结果作废，不写半条事实。

单条失败只记日志并继续下一条：一个租户的解析故障不得让别的租户饿死（design §3.5.11 公平性）。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import cast

from muad_common import SharedSettings
from muad_contracts import (
    FireStatus,
    SchedulePolicy,
    ScheduleStatus,
    TerminalStatus,
)
from muad_contracts.platform_settings import PlatformSettings
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..application.batch_fanin import settle_child
from ..application.platform_settings import resolve_platform_settings
from ..application.ports import NullPlatformSettingsClient, PlatformSettingsClient
from ..application.schedule_admission import (
    AdmissionOutcome,
    AdmissionRejected,
    ScheduleAdmissionService,
    admission_request_from_revision,
)
from ..application.schedule_quota import ScheduleQuota
from ..application.task_events import TaskEventType
from ..application.terminal_tasks import TerminalChange, write_terminal
from ..infrastructure.db_clock import database_now
from ..infrastructure.models.task import TaskExecution, TaskSchedule, TaskScheduleRevision
from ..metrics import (
    SCHEDULE_ADMISSION_FAILED_METRIC,
    SCHEDULE_DUE_LAG_SECONDS_METRIC,
    SCHEDULE_FIRE_CREATED_METRIC,
    SCHEDULE_FIRE_SKIPPED_METRIC,
    SCHEDULE_TIMEZONE_ERROR_METRIC,
    SCHEDULED_FIRE_METRIC,
    SCHEDULED_MISFIRE_METRIC,
    TASK_DEADLINE_EXCEEDED_METRIC,
    increment,
    record_gauge,
    record_label,
    record_loop_tick,
    record_outcome,
)
from .client import ScheduleMaterialsProtocol
from .fire import FireOutcome, write_fire
from .recurrence import OccurrencePlan, PlanAction, build_rule, plan_occurrence

logger = logging.getLogger(__name__)

TASK_DEADLINE_EXCEEDED = "TASK_DEADLINE_EXCEEDED"
NON_TERMINAL_TASK_STATUSES = (
    "QUEUED",
    "RUNNING",
    "WAITING_EXTERNAL",
    "WAITING_HUMAN",
)


class DeadlineSweeper:
    """独立于 Worker 串行执行的 deadline sweep（设计 §3.2.2）。

    被阻塞的 Skill 不阻塞本循环：sweep 只做有界 CAS 与事件写入，投递交给持久
    DeliveryLoop；过期 Child 终态后同事务触发 fan-in，Parent 不会被永久搁置。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: SharedSettings | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings or SharedSettings()

    async def sweep(self, *, now: datetime | None = None) -> int:
        """按绝对期限收尾过期 Task：候选与 CAS 都用 PG 时钟（`now` 仅测试注入）。"""
        async with self._session_factory() as session:
            moment = now if now is not None else await database_now(session)
            conditions = (
                TaskExecution.status.in_(NON_TERMINAL_TASK_STATUSES),
                TaskExecution.deadline_at < moment,
                TaskExecution.is_deleted.is_(False),
            )
            tasks = list(
                await session.scalars(
                    select(TaskExecution)
                    .where(*conditions)
                    .order_by(TaskExecution.deadline_at, TaskExecution.id)
                    .limit(128)
                )
            )
        affected = 0
        # One root tree per transaction: scans never retain locks across unrelated operations.
        for task in tasks:
            async with self._session_factory() as session, session.begin():
                written = await write_terminal(
                    session,
                    task,
                    TerminalChange(
                        status=TerminalStatus.FAILED,
                        error_code=TASK_DEADLINE_EXCEEDED,
                        error_message="task deadline exceeded",
                    ),
                    moment,
                    conditions=conditions,
                    event_type=TaskEventType.DEADLINE_EXCEEDED,
                )
                if written:
                    affected += 1
                    await settle_child(session, task, moment)
        if affected:
            increment(TASK_DEADLINE_EXCEEDED_METRIC, affected)
        return affected


@dataclass(frozen=True, slots=True)
class DueSchedule:
    """一期扫描的候选：只带锁内复核与锁外准入需要的事实，不持有 Row 对象。"""

    id: uuid.UUID
    tenant_id: str
    agent_id: uuid.UUID
    owner_user_id: uuid.UUID
    current_revision_id: uuid.UUID
    next_fire_at: datetime
    occurrence_count: int


class SchedulerLoop:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        materials: ScheduleMaterialsProtocol,
        settings: SharedSettings | None = None,
        deadline_sweeper: DeadlineSweeper | None = None,
        settings_client: PlatformSettingsClient | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._materials = materials
        self._settings = settings or SharedSettings()
        self._settings_client: PlatformSettingsClient = settings_client or NullPlatformSettingsClient()
        self._deadline_sweeper = deadline_sweeper or DeadlineSweeper(session_factory, self._settings)
        self._last_sweep_at: datetime | None = None

    async def run_forever(self) -> None:
        """两个**互相独立**的循环：到期 Schedule 的执行，与 deadline sweep。

        串成一个循环时，一整轮 `run_due`（最多 `scheduler_batch_size` 条 Schedule，每条
        都要取一次设置并解析有效定义）会把 sweep 一直往后推——
        `task_deadline_sweep_interval_sec` 于是不是实际最大延迟，一次慢的外部解析就能让
        它迟到整轮（评审 #15 实测：阻塞 resolver 后 1.2s 仍未 sweep，`_last_sweep_at`
        还是 None）。deadline 是**用户承诺**，不能排在外部调用后面。
        """
        await asyncio.gather(self.run_fire_forever(), self.run_sweep_forever())

    async def run_fire_forever(self) -> None:
        """到期 Schedule 扫描与执行：独立循环（监督器按自己的心跳看停顿）。"""
        while True:
            try:
                await self.run_due()
            except Exception:
                logger.exception("scheduler_loop_tick_failed")
                record_loop_tick("worker.scheduler.fire", "failed")
            else:
                record_loop_tick("worker.scheduler.fire", "ok")
            await asyncio.sleep(self._settings.scheduler_poll_interval_sec)

    async def run_sweep_forever(self) -> None:
        """deadline sweep：与到期扫描并列，慢的外部解析不推迟期限收尾。"""
        while True:
            try:
                await self.sweep_deadlines_if_due()
            except Exception:
                logger.exception("deadline_sweep_tick_failed")
                record_loop_tick("worker.scheduler.deadline", "failed")
            else:
                record_loop_tick("worker.scheduler.deadline", "ok")
            await asyncio.sleep(self._settings.scheduler_poll_interval_sec)

    async def _pg_clock(self) -> datetime:
        """PG 服务端时钟：Schedule 触发、misfire 宽限与 Task 期限冻结的裁决时钟（B-235）。"""
        async with self._session_factory() as session:
            return await database_now(session)

    async def sweep_deadlines_if_due(self, *, now: datetime | None = None) -> int:
        """按独立节拍执行 sweep：与 Skill 串行执行解耦，被阻塞的 Skill 不拖住它。

        节拍与 sweep 时刻都取 PG 时钟（`now` 仅测试注入）：sweep 的期限判定不能被本机
        墙钟偏差提前或推迟。
        """
        moment = now if now is not None else await self._pg_clock()
        interval = timedelta(seconds=self._settings.task_deadline_sweep_interval_sec)
        if self._last_sweep_at is not None and moment - self._last_sweep_at < interval:
            return 0
        self._last_sweep_at = moment
        return await self._deadline_sweeper.sweep(now=moment)

    async def run_due(self, *, now: datetime | None = None) -> int:
        """一轮内连续处理全部到期 Schedule（上限 `scheduler_batch_size`），返回处理数。

        每轮只处理一条时，同一分钟到期的 Schedule 一多就会排队超过 `misfire_grace_sec`
        被误判为错过；这里在一轮内逐条处理，且 misfire 统一以本轮起点判定——排队等待
        的时间不算错过。单条失败只记日志，不影响同轮其它 Schedule。
        """
        moment = now if now is not None else await self._pg_clock()
        seen: set[uuid.UUID] = set()
        for _ in range(self._settings.scheduler_batch_size):
            due = await self._claim_due_schedule(moment, exclude=seen)
            if due is None:
                break
            seen.add(due.id)
            try:
                await self._process(due, moment)
            except Exception:
                logger.exception("schedule_trigger_failed", extra={"schedule_id": str(due.id)})
        return len(seen)

    async def run_once(self, *, now: datetime | None = None) -> uuid.UUID | None:
        """处理一条到期 Schedule；返回本次新建的根 Execution id（无执行返回 None）。"""
        moment = now if now is not None else await self._pg_clock()
        due = await self._claim_due_schedule(moment)
        if due is None:
            return None
        return await self._process(due, moment)

    # ----------------------------------------------------------------- 锁外的读与准入

    async def _claim_due_schedule(
        self, moment: datetime, *, exclude: set[uuid.UUID] | None = None
    ) -> DueSchedule | None:
        """只用于选行；事务结束即释放锁，准入与写库之间不持锁。

        排序是 `(next_fire_at, tenant_id)`：不同租户的 due Schedule 不因固定前缀饿死。
        """
        conditions = [
            TaskSchedule.status == ScheduleStatus.ACTIVE.value,
            TaskSchedule.next_fire_at.is_not(None),
            TaskSchedule.next_fire_at <= moment,
            TaskSchedule.is_deleted.is_(False),
            TaskSchedule.current_revision_id.is_not(None),
        ]
        if exclude:
            conditions.append(TaskSchedule.id.not_in(exclude))
        async with self._session_factory() as session, session.begin():
            row = (
                await session.execute(
                    select(
                        TaskSchedule.id,
                        TaskSchedule.tenant_id,
                        TaskSchedule.agent_id,
                        TaskSchedule.owner_user_id,
                        TaskSchedule.current_revision_id,
                        TaskSchedule.next_fire_at,
                        TaskSchedule.occurrence_count,
                    )
                    .where(*conditions)
                    .order_by(TaskSchedule.next_fire_at.asc(), TaskSchedule.tenant_id.asc())
                    .with_for_update(skip_locked=True)
                    .limit(1)
                )
            ).one_or_none()
        if row is None:
            return None
        return DueSchedule(
            id=row.id,
            tenant_id=row.tenant_id,
            agent_id=row.agent_id,
            owner_user_id=row.owner_user_id,
            current_revision_id=row.current_revision_id,
            next_fire_at=row.next_fire_at,
            occurrence_count=row.occurrence_count,
        )

    async def _revision(self, due: DueSchedule) -> TaskScheduleRevision | None:
        async with self._session_factory() as session:
            return cast(
                TaskScheduleRevision | None,
                await session.scalar(
                    select(TaskScheduleRevision).where(
                        TaskScheduleRevision.id == due.current_revision_id,
                        TaskScheduleRevision.tenant_id == due.tenant_id,
                        TaskScheduleRevision.schedule_id == due.id,
                        TaskScheduleRevision.is_deleted.is_(False),
                    )
                ),
            )

    async def _task_settings(self, tenant_id: str) -> PlatformSettings:
        """任务默认的唯一来源：该租户当前平台设置（源不可读即明确失败，RULE-06）。"""
        snapshot = await self._settings_client.fetch_snapshot(tenant_id=tenant_id)
        return resolve_platform_settings(snapshot, batch_platform_limit=self._settings.batch_platform_limit)

    async def _admit(
        self,
        due: DueSchedule,
        revision: TaskScheduleRevision,
        *,
        deadline_at: datetime,
        settings: PlatformSettings,
    ) -> AdmissionOutcome:
        """锁外的一次性准入；ONCE 的有界重试只发生在这一次调用内。"""
        try:
            request = admission_request_from_revision(
                revision,
                tenant_id=due.tenant_id,
                agent_id=due.agent_id,
                actor_user_id=due.owner_user_id,
                deadline_at=deadline_at,
            )
        except AdmissionRejected as exc:
            return AdmissionOutcome(error=exc.error)
        service = ScheduleAdmissionService(self._materials, settings=settings)
        return await service.admit(
            request, retry_transient_once=revision.recurrence_kind == "ONCE"
        )

    def _plan(
        self,
        due: DueSchedule,
        revision: TaskScheduleRevision,
        moment: datetime,
        grace_sec: int,
        policy: SchedulePolicy,
    ) -> OccurrencePlan | None:
        """纯计算本次 occurrence（触发/错过/终态）；DST gap 以计划内的 `dst_events` 一起给出。"""
        try:
            rule = build_rule(
                kind=revision.recurrence_kind,
                timezone=revision.timezone,
                cron=revision.cron_expr,
                run_at=revision.run_at,
            )
        except ValueError as exc:
            logger.warning(
                "schedule_recurrence_invalid",
                extra={"schedule_id": str(due.id), "reason": str(exc)},
            )
            increment(SCHEDULE_TIMEZONE_ERROR_METRIC)
            return None
        plan = plan_occurrence(
            rule=rule,
            policy=policy,
            next_fire_at=due.next_fire_at,
            now=moment,
            grace_sec=grace_sec,
            occurrence_count=due.occurrence_count,
        )
        return None if plan.action is PlanAction.IDLE else plan

    @staticmethod
    def _policy(revision: TaskScheduleRevision) -> SchedulePolicy:
        return SchedulePolicy.model_validate(revision.schedule_policy or {})

    # ----------------------------------------------------------------- 本次 occurrence

    async def _process(self, due: DueSchedule, moment: datetime) -> uuid.UUID | None:
        revision = await self._revision(due)
        if revision is None:
            logger.warning(
                "schedule_revision_missing",
                extra={"schedule_id": str(due.id), "revision_id": str(due.current_revision_id)},
            )
            return None
        settings = await self._task_settings(due.tenant_id)
        policy = self._policy(revision)
        plan = self._plan(due, revision, moment, settings.task.misfire_grace_sec, policy)
        if plan is None:
            return None
        admission: AdmissionOutcome | None = None
        deadline_at: datetime | None = None
        if plan.action is PlanAction.FIRE:
            deadline_at = moment + timedelta(hours=settings.task.default_deadline_hours)
            admission = await self._admit(
                due, revision, deadline_at=deadline_at, settings=settings
            )
        async with self._session_factory() as session, session.begin():
            schedule = await self._lock_schedule(session, due, revision)
            if schedule is None:
                logger.info(
                    "schedule_fire_superseded",
                    extra={"schedule_id": str(due.id), "tenant_id": due.tenant_id},
                )
                return None
            outcome = await write_fire(
                session,
                schedule=schedule,
                revision=revision,
                policy=policy,
                plan=plan,
                now=moment,
                admission=admission,
                quota=ScheduleQuota.from_settings(settings),
                task_defaults=settings.task,
                deadline_at=deadline_at,
            )
        if plan.scheduled_at is not None:
            record_gauge(
                SCHEDULE_DUE_LAG_SECONDS_METRIC,
                max(0.0, (moment - plan.scheduled_at).total_seconds()),
            )
        self._record(outcome)
        return outcome.execution_id

    async def _lock_schedule(
        self, session: AsyncSession, due: DueSchedule, revision: TaskScheduleRevision
    ) -> TaskSchedule | None:
        """锁内复核本轮读到的三个事实：`status / current_revision_id / next_fire_at`。

        竞态里暂停、更新或取消赢了，这里就取不到行，本轮准入结果整份作废。
        """
        return cast(
            TaskSchedule | None,
            await session.scalar(
                select(TaskSchedule)
                .where(
                    TaskSchedule.id == due.id,
                    TaskSchedule.tenant_id == due.tenant_id,
                    TaskSchedule.status == ScheduleStatus.ACTIVE.value,
                    TaskSchedule.is_deleted.is_(False),
                    TaskSchedule.current_revision_id == revision.id,
                    TaskSchedule.next_fire_at == due.next_fire_at,
                )
                .with_for_update()
            ),
        )

    @staticmethod
    def _record(outcome: FireOutcome) -> None:
        if outcome.status is None:
            return
        record_outcome(SCHEDULED_FIRE_METRIC, str(outcome.status))
        if outcome.misfire:
            increment(SCHEDULED_MISFIRE_METRIC)
        if outcome.skip_reason is not None:
            record_label(SCHEDULE_FIRE_SKIPPED_METRIC, "reason", str(outcome.skip_reason))
        if outcome.status is FireStatus.EXECUTION_CREATED:
            increment(SCHEDULE_FIRE_CREATED_METRIC)
        if outcome.admission_error_code is not None:
            record_label(SCHEDULE_ADMISSION_FAILED_METRIC, "reason", outcome.admission_error_code)
