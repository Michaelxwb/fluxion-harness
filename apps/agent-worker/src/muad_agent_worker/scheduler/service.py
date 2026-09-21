from __future__ import annotations

import asyncio
import hashlib
import json
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
    CreateScheduleRequest,
    DeliveryStatus,
    ResolveDefinitionResponse,
    ScheduleSpec,
    ScheduleStatus,
    TaskStatus,
    TriggerType,
    UpdateScheduleRequest,
)
from sqlalchemy import false, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..application.delivery_routes import upsert_delivery_route
from ..application.task_events import TaskEventType, append_event
from ..application.task_service import EXECUTION_MODE_ASYNC, INITIAL_PRIORITY, TASK_TYPE_SKILL
from ..infrastructure.models.task import TaskExecution, TaskSchedule
from .client import ResolveDefinitionProtocol

logger = logging.getLogger(__name__)

CRON_TYPE = "CRON"
ONCE_TYPE = "ONCE"
TERMINAL_SCHEDULE_STATUSES = (str(ScheduleStatus.COMPLETED), str(ScheduleStatus.MISSED))
SNAPSHOT_HASH_PREFIX = "sha256:"


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


def compute_next_fire_at(spec: ScheduleSpec, base: datetime) -> datetime:
    if spec.type == ONCE_TYPE:
        if spec.run_at is None:
            raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
        return spec.run_at if spec.run_at.tzinfo else spec.run_at.replace(tzinfo=UTC)
    if not spec.cron:
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
    return _next_cron_fire(spec.cron, base, ZoneInfo(spec.timezone))


def compute_snapshot_hash(snapshot: dict[str, Any]) -> str:
    canonical = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{SNAPSHOT_HASH_PREFIX}{digest}"


class ScheduleService:
    def __init__(self, session: AsyncSession, settings: SharedSettings | None = None) -> None:
        self._session = session
        self._settings = settings or SharedSettings()

    async def create_schedule(self, tenant_id: str, payload: CreateScheduleRequest) -> TaskSchedule:
        if payload.schedule.type == CRON_TYPE and not payload.schedule.cron:
            raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
        if payload.schedule.type == ONCE_TYPE and payload.schedule.run_at is None:
            raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
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
        self, tenant_id: str, schedule_id: uuid.UUID, payload: UpdateScheduleRequest
    ) -> TaskSchedule:
        """API-07：只影响将来触发，revision 递增；COMPLETED/MISSED 为终态不可改。

        已创建 Task 各自持有冻结 Snapshot，本方法不触碰它们。
        """
        schedule = await self.get_schedule(tenant_id, schedule_id)
        if schedule.status in TERMINAL_SCHEDULE_STATUSES:
            raise AppError(ErrorCode.REVISION_CONFLICT)
        now = datetime.now(UTC)
        if payload.name is not None:
            schedule.name = payload.name
        if payload.input_template is not None:
            schedule.input_template_json = payload.input_template
        if payload.schedule is not None:
            if payload.schedule.type == CRON_TYPE and not payload.schedule.cron:
                raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
            if payload.schedule.type == ONCE_TYPE and payload.schedule.run_at is None:
                raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
            next_fire_at = compute_next_fire_at(payload.schedule, now)
            schedule.schedule_type = payload.schedule.type
            schedule.cron_expr = payload.schedule.cron
            schedule.timezone = payload.schedule.timezone
            schedule.run_at = payload.schedule.run_at
            schedule.next_fire_at = next_fire_at
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
                    .order_by(TaskSchedule.create_time.desc())
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

    async def pause_schedule(self, tenant_id: str, schedule_id: uuid.UUID) -> TaskSchedule:
        schedule = await self.get_schedule(tenant_id, schedule_id)
        if schedule.status == str(ScheduleStatus.PAUSED):
            # 已暂停：幂等返回，不报冲突（终态仍由 _transition 拒绝）。
            return schedule
        return await self._transition(
            tenant_id,
            schedule_id,
            expected=ScheduleStatus.ACTIVE,
            target=ScheduleStatus.PAUSED,
        )

    async def resume_schedule(self, tenant_id: str, schedule_id: uuid.UUID) -> TaskSchedule:
        schedule = await self._transition(
            tenant_id,
            schedule_id,
            expected=ScheduleStatus.PAUSED,
            target=ScheduleStatus.ACTIVE,
        )
        # 按当前时间重算：错过的触发按 SKIP 不补发，next_fire_at 不得落在过去。
        now = datetime.now(UTC)
        schedule.next_fire_at = compute_next_fire_at(
            ScheduleSpec(
                type=schedule.schedule_type,
                cron=schedule.cron_expr,
                run_at=schedule.run_at,
                timezone=schedule.timezone,
            ),
            now,
        )
        schedule.update_time = now
        await self._session.flush()
        return schedule

    async def delete_schedule(self, tenant_id: str, schedule_id: uuid.UUID) -> None:
        """软删除；已删除再次调用幂等成功，未知 id 仍为 404。"""
        schedule = (
            await self._session.execute(
                select(TaskSchedule).where(
                    TaskSchedule.id == schedule_id,
                    TaskSchedule.tenant_id == tenant_id,
                )
            )
        ).scalar_one_or_none()
        if schedule is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        if schedule.is_deleted:
            return
        schedule.is_deleted = True
        schedule.update_time = datetime.now(UTC)
        await self._session.flush()

    async def _transition(
        self,
        tenant_id: str,
        schedule_id: uuid.UUID,
        *,
        expected: ScheduleStatus,
        target: ScheduleStatus,
    ) -> TaskSchedule:
        schedule = await self.get_schedule(tenant_id, schedule_id)
        if schedule.status != str(expected):
            raise AppError(ErrorCode.REVISION_CONFLICT)
        schedule.status = str(target)
        schedule.update_time = datetime.now(UTC)
        await self._session.flush()
        return schedule


class SchedulerLoop:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        resolver: ResolveDefinitionProtocol,
        settings: SharedSettings | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._resolver = resolver
        self._settings = settings or SharedSettings()

    async def run_forever(self) -> None:
        while True:
            try:
                await self.run_once()
            except Exception:
                logger.exception("scheduler_loop_tick_failed")
            await asyncio.sleep(self._settings.scheduler_poll_interval_sec)

    async def run_once(self, *, now: datetime | None = None) -> uuid.UUID | None:
        moment = now or datetime.now(UTC)
        schedule = await self._claim_due_schedule(moment)
        if schedule is None or schedule.next_fire_at is None:
            return None
        fire_at = schedule.next_fire_at
        if fire_at < moment - timedelta(seconds=self._settings.misfire_grace_sec):
            logger.warning(
                "schedule_misfire_skipped",
                extra={"schedule_id": str(schedule.id), "scheduled_fire_at": fire_at.isoformat()},
            )
            await self._skip_misfire(schedule, fire_at, moment)
            return None
        resolved = await self._resolver.resolve(
            schedule.agent_id, schedule.actor_user_id, schedule.tenant_id
        )
        return await self.fire(schedule, fire_at, resolved, now=moment)

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
        if skill is None:
            raise ScheduleResolutionError(f"skill {schedule.skill_id} missing from resolve response")
        snapshot = resolved.model_dump(mode="json")
        task_id = uuid.uuid4()
        values = {
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
            "input_json": schedule.input_template_json,
            "execution_snapshot_schema_version": 1,
            "execution_snapshot_json": snapshot,
            "snapshot_hash": compute_snapshot_hash(snapshot),
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
        async with self._session_factory() as session:
            async with session.begin():
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
                await self._advance_schedule(session, schedule, scheduled_fire_at, moment, fired=True)
        return inserted_id

    async def _claim_due_schedule(self, moment: datetime) -> TaskSchedule | None:
        async with self._session_factory() as session:
            async with session.begin():
                schedule = (
                    await session.execute(
                        select(TaskSchedule)
                        .where(
                            TaskSchedule.status == str(ScheduleStatus.ACTIVE),
                            TaskSchedule.next_fire_at.is_not(None),
                            TaskSchedule.next_fire_at <= moment,
                            TaskSchedule.is_deleted.is_(False),
                        )
                        .order_by(TaskSchedule.next_fire_at.asc())
                        .with_for_update(skip_locked=True)
                        .limit(1)
                    )
                ).scalar_one_or_none()
        return schedule

    async def _skip_misfire(
        self,
        schedule: TaskSchedule,
        scheduled_fire_at: datetime,
        moment: datetime,
    ) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                await self._advance_schedule(
                    session,
                    schedule,
                    scheduled_fire_at,
                    moment,
                    fired=False,
                )

    async def _advance_schedule(
        self,
        session: AsyncSession,
        schedule: TaskSchedule,
        scheduled_fire_at: datetime,
        moment: datetime,
        *,
        fired: bool,
    ) -> None:
        if schedule.schedule_type == CRON_TYPE:
            if not schedule.cron_expr:
                raise ScheduleResolutionError(f"schedule {schedule.id} missing cron expression")
            zone = ZoneInfo(schedule.timezone)
            next_fire_at = croniter(schedule.cron_expr, moment.astimezone(zone)).get_next(datetime)
            values: dict[str, Any] = {
                "next_fire_at": next_fire_at,
                "update_time": moment,
            }
            if fired:
                values["last_fire_at"] = scheduled_fire_at
        else:
            values = {
                "status": str(ScheduleStatus.COMPLETED),
                "completed_at": moment,
                "next_fire_at": None,
                "update_time": moment,
            }
            if fired:
                values["last_fire_at"] = scheduled_fire_at
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
