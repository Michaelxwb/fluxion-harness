from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from croniter import CroniterBadCronError, croniter
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_common import SharedSettings
from muad_contracts import (
    TASK_SNAPSHOT_SCHEMA_VERSION,
    CreateScheduleRequest,
    DeliveryStatus,
    ResolveDefinitionResponse,
    ResolvedSkill,
    ScheduleSpec,
    ScheduleStatus,
    TaskStatus,
    TriggerType,
    UpdateScheduleRequest,
    build_task_snapshot,
    snapshot_hash,
)
from sqlalchemy import false, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..application.batch_fanin import settle_child
from ..application.delivery_routes import upsert_delivery_route
from ..application.task_events import TaskEventSeed, TaskEventType, append_event, append_events
from ..application.task_service import (
    EXECUTION_MODE_ASYNC,
    INITIAL_PRIORITY,
    TASK_TYPE_SKILL,
    validate_execution_snapshot,
)
from ..infrastructure.models.task import TaskExecution, TaskSchedule
from ..metrics import (
    SCHEDULED_FIRE_METRIC,
    SCHEDULED_MISFIRE_METRIC,
    TASKS_METRIC,
    increment,
    record_outcome,
)
from .client import ResolveDefinitionProtocol, ResolveTransportError
from .templates import render_input_template, validate_input_template

logger = logging.getLogger(__name__)

CRON_TYPE = "CRON"
ONCE_TYPE = "ONCE"
TERMINAL_SCHEDULE_STATUSES = (str(ScheduleStatus.COMPLETED), str(ScheduleStatus.MISSED))
SKIP_MISFIRE = "SCHEDULE_MISFIRE_SKIPPED"
SKIP_SKILL_NOT_EFFECTIVE = "SKILL_NOT_EFFECTIVE"
FIRED_STATUS = "FIRED"
TASK_DEADLINE_EXCEEDED = "TASK_DEADLINE_EXCEEDED"
TASK_DEADLINE_EXCEEDED_TOTAL = "task_deadline_exceeded_total"
NON_TERMINAL_TASK_STATUSES = (
    str(TaskStatus.QUEUED),
    str(TaskStatus.RUNNING),
    str(TaskStatus.WAITING),
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
        moment = now or datetime.now(UTC)
        async with self._session_factory() as session:
            async with session.begin():
                rows = (
                    await session.execute(
                        update(TaskExecution)
                        .where(
                            TaskExecution.status.in_(NON_TERMINAL_TASK_STATUSES),
                            TaskExecution.deadline_at < moment,
                            TaskExecution.is_deleted.is_(False),
                        )
                        .values(
                            status=str(TaskStatus.FAILED),
                            error_code=TASK_DEADLINE_EXCEEDED,
                            error_message="task deadline exceeded",
                            finished_at=moment,
                            lease_owner=None,
                            lease_until=None,
                            update_time=moment,
                        )
                        .returning(
                            TaskExecution.id, TaskExecution.tenant_id, TaskExecution.task_type
                        )
                    )
                ).all()
                if not rows:
                    return 0
                await append_events(
                    session,
                    [
                        TaskEventSeed(
                            tenant_id=tenant_id,
                            task_id=task_id,
                            event_type=TaskEventType.DEADLINE_EXCEEDED,
                        )
                        for task_id, tenant_id, _task_type in rows
                    ],
                )
                for task_id, _, _task_type in rows:
                    expired = await session.get(TaskExecution, task_id)
                    if expired is not None:
                        await settle_child(session, expired, moment)
        for _task_id, _tenant_id, task_type in rows:
            record_outcome(TASKS_METRIC, str(TaskStatus.FAILED), {"type": task_type})
        increment(TASK_DEADLINE_EXCEEDED_TOTAL, len(rows))
        return len(rows)


class ScheduleResolutionError(Exception):
    pass


def _next_cron_fire(cron: str, base: datetime, zone: ZoneInfo) -> datetime:
    """按目标时区的「墙上时间」计算下一次触发。

    不能把带时区的起点直接交给 croniter：在 DST 切换日它会漂移一小时
    （美东 2026-03-08 的 `0 9 * * *` 会算成 08:00，2026-11-01 会算成 10:00）。
    改为先去掉时区、只在本地墙上时间上迭代，再把目标时区贴回去。
    """
    naive_base = base.astimezone(zone).replace(tzinfo=None)
    try:
        naive_next = croniter(cron, naive_base).get_next(datetime)
    except (CroniterBadCronError, ValueError) as exc:
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR) from exc
    return naive_next.replace(tzinfo=zone)


def build_scheduled_snapshot(
    resolved: ResolveDefinitionResponse, skill: ResolvedSkill
) -> tuple[dict[str, Any], str]:
    """触发时按当前有效定义冻结快照：与 Runtime 同一构建口径，只含目标 Skill。

    字段白名单构建，Console resolve 带回的模型 api_key / MCP endpoint 不进入快照；
    再经 `validate_execution_snapshot` 复核必需键与无密钥（RULE-secret-001 / RULE-04）。
    """
    snapshot = build_task_snapshot(
        agent=resolved.agent,
        model=resolved.model,
        skill=skill,
        mcp_servers=resolved.mcp_servers,
    )
    validate_execution_snapshot(snapshot)
    return snapshot, snapshot_hash(snapshot)


def compute_next_fire_at(spec: ScheduleSpec, base: datetime) -> datetime:
    if spec.type == ONCE_TYPE:
        if spec.run_at is None:
            raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
        return spec.run_at if spec.run_at.tzinfo else spec.run_at.replace(tzinfo=UTC)
    if not spec.cron:
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
    return _next_cron_fire(spec.cron, base, ZoneInfo(spec.timezone))


class ScheduleService:
    def __init__(self, session: AsyncSession, settings: SharedSettings | None = None) -> None:
        self._session = session
        self._settings = settings or SharedSettings()

    async def create_schedule(self, tenant_id: str, payload: CreateScheduleRequest) -> TaskSchedule:
        if payload.schedule.type == CRON_TYPE and not payload.schedule.cron:
            raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
        if payload.schedule.type == ONCE_TYPE and payload.schedule.run_at is None:
            raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
        validate_input_template(payload.input_template)
        now = datetime.now(UTC)
        route_id = await upsert_delivery_route(
            self._session,
            tenant_id=tenant_id,
            platform_user_id=payload.actor_user_id,
            route=payload.delivery_route,
        )
        schedule = TaskSchedule(
            tenant_id=tenant_id,
            name=payload.name,
            agent_id=payload.agent_id,
            actor_user_id=payload.actor_user_id,
            intent_key=payload.intent_key,
            skill_id=payload.skill_id,
            input_template_json=payload.input_template,
            schedule_type=payload.schedule.type,
            cron_expr=payload.schedule.cron,
            timezone=payload.schedule.timezone,
            run_at=payload.schedule.run_at,
            delivery_route_id=route_id,
            status=str(ScheduleStatus.ACTIVE),
            next_fire_at=compute_next_fire_at(payload.schedule, now),
            revision=1,
        )
        self._session.add(schedule)
        await self._session.flush()
        return schedule

    async def update_schedule(
        self,
        tenant_id: str,
        schedule_id: uuid.UUID,
        payload: UpdateScheduleRequest,
        *,
        actor_user_id: uuid.UUID | None = None,
    ) -> TaskSchedule:
        """API-07：只影响将来触发，revision 递增；COMPLETED/MISSED 为终态不可改。

        行锁下读改写，与 Scheduler 触发的 `_lock_claim` 串行，不会覆写并发推进的状态；
        已创建 Task 各自持有冻结 Snapshot，本方法不触碰它们。
        """
        schedule = await self._lock_for_change(tenant_id, schedule_id, actor_user_id)
        if schedule.status in TERMINAL_SCHEDULE_STATUSES:
            raise AppError(ErrorCode.REVISION_CONFLICT)
        now = datetime.now(UTC)
        if payload.name is not None:
            schedule.name = payload.name
        if payload.input_template is not None:
            validate_input_template(payload.input_template)
            schedule.input_template_json = payload.input_template
        if payload.schedule is not None:
            self._apply_spec(schedule, payload.schedule, now)
        if payload.delivery_route is not None:
            schedule.delivery_route_id = await upsert_delivery_route(
                self._session,
                tenant_id=tenant_id,
                platform_user_id=schedule.actor_user_id,
                route=payload.delivery_route,
            )
        schedule.revision += 1
        schedule.update_time = now
        await self._session.flush()
        return schedule

    def _apply_spec(self, schedule: TaskSchedule, spec: ScheduleSpec, now: datetime) -> None:
        if spec.type == CRON_TYPE and not spec.cron:
            raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
        if spec.type == ONCE_TYPE and spec.run_at is None:
            raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
        next_fire_at = compute_next_fire_at(spec, now)
        schedule.schedule_type = spec.type
        schedule.cron_expr = spec.cron
        schedule.timezone = spec.timezone
        schedule.run_at = spec.run_at
        schedule.next_fire_at = next_fire_at

    async def get_schedule(self, tenant_id: str, schedule_id: uuid.UUID) -> TaskSchedule:
        schedule = (
            await self._session.execute(
                select(TaskSchedule).where(
                    TaskSchedule.id == schedule_id,
                    TaskSchedule.tenant_id == tenant_id,
                    TaskSchedule.is_deleted.is_(False),
                )
            )
        ).scalar_one_or_none()
        if schedule is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        return schedule

    async def _lock_for_change(
        self,
        tenant_id: str,
        schedule_id: uuid.UUID,
        actor_user_id: uuid.UUID | None,
        *,
        include_deleted: bool = False,
    ) -> TaskSchedule:
        """变更前 `SELECT ... FOR UPDATE`：状态判断与写入在同一行锁内完成（CAS 语义）。

        `actor_user_id` 非空时（Runtime 代表用户调用）必须是 Schedule owner，否则
        `FORBIDDEN`；Console Admin 路径传 None，由 Console 侧权限域把关。
        """
        conditions: list[Any] = [
            TaskSchedule.id == schedule_id,
            TaskSchedule.tenant_id == tenant_id,
        ]
        if not include_deleted:
            conditions.append(TaskSchedule.is_deleted.is_(False))
        schedule = (
            await self._session.execute(
                select(TaskSchedule)
                .where(*conditions)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if schedule is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        if actor_user_id is not None and schedule.actor_user_id != actor_user_id:
            raise AppError(ErrorCode.FORBIDDEN)
        return schedule

    async def list_schedules(
        self,
        tenant_id: str,
        *,
        actor_user_id: uuid.UUID | None = None,
        agent_id: uuid.UUID | None = None,
        status: ScheduleStatus | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[TaskSchedule], int]:
        conditions: list[Any] = [
            TaskSchedule.tenant_id == tenant_id,
            TaskSchedule.is_deleted.is_(False),
        ]
        if actor_user_id is not None:
            conditions.append(TaskSchedule.actor_user_id == actor_user_id)
        if agent_id is not None:
            conditions.append(TaskSchedule.agent_id == agent_id)
        if status is not None:
            conditions.append(TaskSchedule.status == str(status))
        items = (
            (
                await self._session.execute(
                    select(TaskSchedule)
                    .where(*conditions)
                    .order_by(TaskSchedule.update_time.desc(), TaskSchedule.create_time.desc())
                    .limit(page_size)
                    .offset((page - 1) * page_size)
                )
            )
            .scalars()
            .all()
        )
        total = (
            await self._session.execute(
                select(func.count()).select_from(TaskSchedule).where(*conditions)
            )
        ).scalar_one()
        return list(items), int(total)

    async def pause_schedule(
        self,
        tenant_id: str,
        schedule_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID | None = None,
    ) -> TaskSchedule:
        schedule = await self._lock_for_change(tenant_id, schedule_id, actor_user_id)
        if schedule.status == str(ScheduleStatus.PAUSED):
            # 已暂停：幂等返回，不报冲突。
            return schedule
        if schedule.status != str(ScheduleStatus.ACTIVE):
            raise AppError(ErrorCode.REVISION_CONFLICT)
        schedule.status = str(ScheduleStatus.PAUSED)
        schedule.update_time = datetime.now(UTC)
        await self._session.flush()
        return schedule

    async def resume_schedule(
        self,
        tenant_id: str,
        schedule_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID | None = None,
    ) -> TaskSchedule:
        schedule = await self._lock_for_change(tenant_id, schedule_id, actor_user_id)
        if schedule.status != str(ScheduleStatus.PAUSED):
            raise AppError(ErrorCode.REVISION_CONFLICT)
        # 按当前时间重算：错过的触发按 SKIP 不补发，next_fire_at 不得落在过去。
        now = datetime.now(UTC)
        schedule.next_fire_at = compute_next_fire_at(
            ScheduleSpec(
                type="CRON" if schedule.schedule_type == CRON_TYPE else "ONCE",
                cron=schedule.cron_expr,
                run_at=schedule.run_at,
                timezone=schedule.timezone,
            ),
            now,
        )
        schedule.status = str(ScheduleStatus.ACTIVE)
        schedule.update_time = now
        await self._session.flush()
        return schedule

    async def delete_schedule(
        self,
        tenant_id: str,
        schedule_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID | None = None,
    ) -> None:
        """软删除；已删除再次调用幂等成功，未知 id 仍为 404。"""
        schedule = await self._lock_for_change(
            tenant_id, schedule_id, actor_user_id, include_deleted=True
        )
        if schedule.is_deleted:
            return
        schedule.is_deleted = True
        schedule.update_time = datetime.now(UTC)
        await self._session.flush()


class SchedulerLoop:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        resolver: ResolveDefinitionProtocol,
        settings: SharedSettings | None = None,
        deadline_sweeper: DeadlineSweeper | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._resolver = resolver
        self._settings = settings or SharedSettings()
        self._deadline_sweeper = deadline_sweeper or DeadlineSweeper(
            session_factory, self._settings
        )
        self._last_sweep_at: datetime | None = None

    async def run_forever(self) -> None:
        while True:
            try:
                await self.run_due()
                await self.sweep_deadlines_if_due()
            except Exception:
                logger.exception("scheduler_loop_tick_failed")
            await asyncio.sleep(self._settings.scheduler_poll_interval_sec)

    async def sweep_deadlines_if_due(self, *, now: datetime | None = None) -> int:
        """按独立节拍执行 sweep：与 Skill 串行执行解耦，被阻塞的 Skill 不拖住它。"""
        moment = now or datetime.now(UTC)
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
        moment = now or datetime.now(UTC)
        seen: set[uuid.UUID] = set()
        for _ in range(self._settings.scheduler_batch_size):
            schedule = await self._claim_due_schedule(moment, exclude=seen)
            if schedule is None:
                break
            seen.add(schedule.id)
            try:
                await self._process(schedule, moment)
            except Exception:
                logger.exception("schedule_trigger_failed", extra={"schedule_id": str(schedule.id)})
        return len(seen)

    async def run_once(self, *, now: datetime | None = None) -> uuid.UUID | None:
        moment = now or datetime.now(UTC)
        schedule = await self._claim_due_schedule(moment)
        if schedule is None:
            return None
        return await self._process(schedule, moment)

    async def _process(self, schedule: TaskSchedule, moment: datetime) -> uuid.UUID | None:
        fire_at = schedule.next_fire_at
        if fire_at is None:
            return None
        if fire_at < moment - timedelta(seconds=self._settings.misfire_grace_sec):
            logger.warning(
                "schedule_misfire_skipped",
                extra={"schedule_id": str(schedule.id), "scheduled_fire_at": fire_at.isoformat()},
            )
            await self._skip(schedule, fire_at, moment, SKIP_MISFIRE, "scheduled fire time missed")
            return None
        try:
            resolved = await self._resolver.resolve(
                schedule.agent_id, schedule.actor_user_id, schedule.tenant_id
            )
        except ResolveTransportError as exc:
            # 授权/Binding 被撤销或 resolve 不可用：fail closed，不建可执行 Task，
            # 留原因并照常推进，避免同一 Schedule 每轮重复 claim 形成热循环。
            # ONCE 在此进入 MISSED：run_at 已过且未产生 TaskExecution（设计 §3.2.3 定义），
            # 具体原因看 last_error_code。
            logger.warning(
                "schedule_trigger_resolve_failed",
                extra={"schedule_id": str(schedule.id), "reason_code": exc.code},
            )
            await self._skip(
                schedule, fire_at, moment, exc.code, f"resolve-definition failed: {exc.code}"
            )
            return None
        try:
            return await self.fire(schedule, fire_at, resolved, now=moment)
        except AppError as exc:
            # 冻结快照/模板渲染等确定性失败：同样 fail closed 并推进，不留热循环。
            await self._skip(schedule, fire_at, moment, str(exc.code), "task snapshot rejected")
            return None

    async def fire(
        self,
        schedule: TaskSchedule,
        scheduled_fire_at: datetime,
        resolved: ResolveDefinitionResponse,
        *,
        now: datetime | None = None,
    ) -> uuid.UUID | None:
        moment = now or datetime.now(UTC)
        skill = next((item for item in resolved.skills if item.skill_id == schedule.skill_id), None)
        async with self._session_factory() as session:
            async with session.begin():
                locked = await self._lock_claim(session, schedule, scheduled_fire_at)
                if locked is None:
                    logger.info(
                        "schedule_claim_superseded",
                        extra={"schedule_id": str(schedule.id)},
                    )
                    return None
                if skill is None:
                    await self._record_skip(
                        session,
                        schedule,
                        scheduled_fire_at,
                        moment,
                        SKIP_SKILL_NOT_EFFECTIVE,
                        f"skill {schedule.skill_id} is no longer effective for the actor",
                    )
                    return None
                inserted_id = await self._insert_task(
                    session, schedule, resolved, skill, scheduled_fire_at, moment
                )
                await self._advance(
                    session, schedule, scheduled_fire_at, moment, fired=True
                )
        if inserted_id is not None:
            record_outcome(SCHEDULED_FIRE_METRIC, FIRED_STATUS)
        return inserted_id

    async def _insert_task(
        self,
        session: AsyncSession,
        schedule: TaskSchedule,
        resolved: ResolveDefinitionResponse,
        skill: ResolvedSkill,
        scheduled_fire_at: datetime,
        moment: datetime,
    ) -> uuid.UUID | None:
        snapshot, frozen_hash = build_scheduled_snapshot(resolved, skill)
        task_id = uuid.uuid4()
        values: dict[str, Any] = {
            "id": task_id,
            "tenant_id": schedule.tenant_id,
            "schedule_id": schedule.id,
            "agent_id": schedule.agent_id,
            "actor_user_id": schedule.actor_user_id,
            "intent_key": schedule.intent_key,
            "skill_id": skill.skill_id,
            "skill_artifact_id": skill.artifact_id,
            "trigger_type": str(TriggerType.SCHEDULED),
            "execution_mode": EXECUTION_MODE_ASYNC,
            "task_type": TASK_TYPE_SKILL,
            "status": str(TaskStatus.QUEUED),
            "input_json": render_input_template(
                schedule.input_template_json, scheduled_fire_at, schedule.timezone
            ),
            "execution_snapshot_schema_version": TASK_SNAPSHOT_SCHEMA_VERSION,
            "execution_snapshot_json": snapshot,
            "snapshot_hash": frozen_hash,
            "idempotency_key": self._idempotency_key(schedule.id, scheduled_fire_at),
            "priority": INITIAL_PRIORITY,
            "attempt": 0,
            "max_attempts": self._settings.task_max_attempts,
            "not_before": moment,
            "deadline_at": moment + timedelta(hours=self._settings.task_default_deadline_hours),
            "delivery_route_id": schedule.delivery_route_id,
            "delivery_mode": "FINAL_ONLY",
            "delivery_status": str(DeliveryStatus.PENDING),
            "delivery_key": f"task:{task_id}:final",
            "delivery_attempts": 0,
        }
        inserted_id = (
            await session.execute(
                pg_insert(TaskExecution)
                .values(**values)
                .on_conflict_do_nothing(
                    index_elements=[TaskExecution.tenant_id, TaskExecution.idempotency_key],
                    index_where=TaskExecution.is_deleted == false(),
                )
                .returning(TaskExecution.id)
            )
        ).scalar_one_or_none()
        if inserted_id is not None:
            await append_event(
                session,
                tenant_id=schedule.tenant_id,
                task_id=inserted_id,
                event_type=TaskEventType.CREATED,
                payload={
                    "schedule_id": str(schedule.id),
                    "scheduled_fire_at": scheduled_fire_at.isoformat(),
                },
            )
        return inserted_id

    async def _claim_due_schedule(
        self, moment: datetime, *, exclude: set[uuid.UUID] | None = None
    ) -> TaskSchedule | None:
        """只用于选行；事务结束即释放锁，resolve 与落库之间不持锁。"""
        conditions: list[Any] = [
            TaskSchedule.status == str(ScheduleStatus.ACTIVE),
            TaskSchedule.next_fire_at.is_not(None),
            TaskSchedule.next_fire_at <= moment,
            TaskSchedule.is_deleted.is_(False),
        ]
        if exclude:
            conditions.append(TaskSchedule.id.not_in(exclude))
        async with self._session_factory() as session:
            async with session.begin():
                schedule = (
                    await session.execute(
                        select(TaskSchedule)
                        .where(*conditions)
                        .order_by(TaskSchedule.next_fire_at.asc())
                        .with_for_update(skip_locked=True)
                        .limit(1)
                    )
                ).scalar_one_or_none()
        return schedule

    async def _lock_claim(
        self,
        session: AsyncSession,
        schedule: TaskSchedule,
        scheduled_fire_at: datetime,
    ) -> TaskSchedule | None:
        """事务内复核 claim 结果：暂停/删除/修改若已赢得竞态则不返回行。"""
        return (
            await session.execute(
                select(TaskSchedule)
                .where(
                    TaskSchedule.id == schedule.id,
                    TaskSchedule.tenant_id == schedule.tenant_id,
                    TaskSchedule.status == str(ScheduleStatus.ACTIVE),
                    TaskSchedule.is_deleted.is_(False),
                    TaskSchedule.revision == schedule.revision,
                    TaskSchedule.next_fire_at == scheduled_fire_at,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()

    async def _skip(
        self,
        schedule: TaskSchedule,
        scheduled_fire_at: datetime,
        moment: datetime,
        code: str,
        message: str,
    ) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                if await self._lock_claim(session, schedule, scheduled_fire_at) is None:
                    return
                await self._record_skip(session, schedule, scheduled_fire_at, moment, code, message)

    async def _record_skip(
        self,
        session: AsyncSession,
        schedule: TaskSchedule,
        scheduled_fire_at: datetime,
        moment: datetime,
        code: str,
        message: str,
    ) -> None:
        await self._advance(
            session,
            schedule,
            scheduled_fire_at,
            moment,
            fired=False,
            skip=(code, message),
        )
        if code == SKIP_MISFIRE:
            increment(SCHEDULED_MISFIRE_METRIC)
        record_outcome(SCHEDULED_FIRE_METRIC, code)

    async def _advance(
        self,
        session: AsyncSession,
        schedule: TaskSchedule,
        scheduled_fire_at: datetime,
        moment: datetime,
        *,
        fired: bool,
        skip: tuple[str, str] | None = None,
    ) -> None:
        values: dict[str, Any] = {"update_time": moment}
        if schedule.schedule_type == CRON_TYPE:
            if not schedule.cron_expr:
                raise ScheduleResolutionError(f"schedule {schedule.id} missing cron expression")
            values["next_fire_at"] = _next_cron_fire(
                schedule.cron_expr, moment, ZoneInfo(schedule.timezone)
            )
        elif fired:
            values.update(
                status=str(ScheduleStatus.COMPLETED),
                completed_at=moment,
                next_fire_at=None,
            )
        else:
            # ONCE 错过触发：终态 MISSED，不写 completed_at，不补发（设计 §3.2.3）。
            values.update(
                status=str(ScheduleStatus.MISSED),
                completed_at=None,
                next_fire_at=None,
            )
        if fired:
            values["last_fire_at"] = scheduled_fire_at
            values["last_error_code"] = None
            values["last_error_message"] = None
        elif skip is not None:
            values["last_error_code"] = skip[0]
            values["last_error_message"] = skip[1]
            values["last_skipped_at"] = moment
        await session.execute(
            update(TaskSchedule)
            .where(
                TaskSchedule.id == schedule.id,
                TaskSchedule.next_fire_at == scheduled_fire_at,
            )
            .values(**values)
        )

    def _idempotency_key(self, schedule_id: uuid.UUID, scheduled_fire_at: datetime) -> str:
        return f"schedule:{schedule_id}:{scheduled_fire_at.isoformat()}"
