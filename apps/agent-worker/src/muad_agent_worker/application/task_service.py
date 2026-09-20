from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_common import SharedSettings
from muad_contracts import (
    CreateTaskRequest,
    DeliveryMode,
    DeliveryStatus,
    TaskStatus,
    TriggerType,
)
from sqlalchemy import CursorResult, false, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.task import TaskExecution
from .delivery_routes import upsert_delivery_route
from .task_events import TaskEventType, append_event

CANCEL_PENDING_STATUS = "CANCELLING"
INITIAL_PRIORITY = 100
EXECUTION_MODE_ASYNC = "ASYNC"
TASK_TYPE_SKILL = "SKILL"
TERMINAL_STATUSES = (str(TaskStatus.COMPLETED), str(TaskStatus.FAILED), str(TaskStatus.CANCELLED))
CANCELABLE_STATUSES = (str(TaskStatus.QUEUED), str(TaskStatus.WAITING))


class TaskService:
    def __init__(self, session: AsyncSession, settings: SharedSettings | None = None) -> None:
        self._session = session
        self._settings = settings or SharedSettings()

    async def create(self, payload: CreateTaskRequest) -> TaskExecution:
        existing = await self._find_by_idempotency_key(payload.tenant_id, payload.idempotency_key)
        if existing is not None:
            return existing
        route_id = await self._resolve_route(payload)
        task_id = uuid.uuid4()
        now = datetime.now(UTC)
        values = {
            "id": task_id,
            "tenant_id": payload.tenant_id,
            "source_run_id": payload.source_run_id,
            "agent_id": payload.agent_id,
            "actor_user_id": payload.actor_user_id,
            "intent_key": payload.intent_key,
            "skill_id": payload.skill_id,
            "skill_artifact_id": payload.skill_artifact_id,
            "trigger_type": str(TriggerType.IMMEDIATE),
            "execution_mode": EXECUTION_MODE_ASYNC,
            "task_type": TASK_TYPE_SKILL,
            "status": str(TaskStatus.QUEUED),
            "input_json": payload.input,
            "execution_snapshot_schema_version": payload.execution_snapshot_schema_version,
            "execution_snapshot_json": payload.execution_snapshot,
            "snapshot_hash": payload.snapshot_hash,
            "idempotency_key": payload.idempotency_key,
            "priority": INITIAL_PRIORITY,
            "attempt": 0,
            "max_attempts": self._settings.task_max_attempts,
            "not_before": now,
            "deadline_at": now + timedelta(hours=self._settings.task_default_deadline_hours),
            "delivery_route_id": route_id,
            "delivery_mode": str(payload.delivery_mode),
            "delivery_status": str(self._initial_delivery_status(payload.delivery_mode)),
            "delivery_key": f"task:{task_id}:final",
            "delivery_attempts": 0,
        }
        statement = (
            pg_insert(TaskExecution)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=[TaskExecution.tenant_id, TaskExecution.idempotency_key],
                index_where=TaskExecution.is_deleted == false(),
            )
            .returning(TaskExecution.id)
        )
        inserted_id = (await self._session.execute(statement)).scalar_one_or_none()
        if inserted_id is None:
            deduped = await self._find_by_idempotency_key(payload.tenant_id, payload.idempotency_key)
            if deduped is None:
                raise AppError(ErrorCode.COMMON_CONFLICT)
            return deduped
        await append_event(
            self._session,
            tenant_id=payload.tenant_id,
            task_id=inserted_id,
            event_type=TaskEventType.CREATED,
            payload={"idempotency_key": payload.idempotency_key},
        )
        await self._session.flush()
        task = await self._session.get(TaskExecution, inserted_id)
        if task is None:
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
        return task

    async def get(self, tenant_id: str, task_id: uuid.UUID) -> TaskExecution:
        task = (
            await self._session.execute(
                select(TaskExecution).where(
                    TaskExecution.id == task_id,
                    TaskExecution.tenant_id == tenant_id,
                    TaskExecution.is_deleted.is_(False),
                )
            )
        ).scalar_one_or_none()
        if task is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        return task

    async def list(
        self,
        tenant_id: str,
        *,
        status: TaskStatus | None = None,
        trigger_type: TriggerType | None = None,
        agent_id: uuid.UUID | None = None,
        actor_user_id: uuid.UUID | None = None,
        schedule_id: uuid.UUID | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[TaskExecution], int]:
        conditions: list[Any] = [
            TaskExecution.tenant_id == tenant_id,
            TaskExecution.is_deleted.is_(False),
        ]
        if status is not None:
            conditions.append(TaskExecution.status == str(status))
        if trigger_type is not None:
            conditions.append(TaskExecution.trigger_type == str(trigger_type))
        if agent_id is not None:
            conditions.append(TaskExecution.agent_id == agent_id)
        if actor_user_id is not None:
            conditions.append(TaskExecution.actor_user_id == actor_user_id)
        if schedule_id is not None:
            conditions.append(TaskExecution.schedule_id == schedule_id)
        if start_time is not None:
            conditions.append(TaskExecution.create_time >= start_time)
        if end_time is not None:
            conditions.append(TaskExecution.create_time <= end_time)
        items = (
            (
                await self._session.execute(
                    select(TaskExecution)
                    .where(*conditions)
                    .order_by(TaskExecution.create_time.desc())
                    .limit(page_size)
                    .offset((page - 1) * page_size)
                )
            )
            .scalars()
            .all()
        )
        total = (
            await self._session.execute(
                select(func.count()).select_from(TaskExecution).where(*conditions)
            )
        ).scalar_one()
        return list(items), int(total)

    async def cancel(self, tenant_id: str, task_id: uuid.UUID) -> str:
        task = await self.get(tenant_id, task_id)
        if task.status in TERMINAL_STATUSES:
            return task.status
        now = datetime.now(UTC)
        if task.status in CANCELABLE_STATUSES:
            rowcount = await self._cas_status(
                tenant_id,
                task_id,
                expected=CANCELABLE_STATUSES,
                values={
                    "status": str(TaskStatus.CANCELLED),
                    "cancel_requested": True,
                    "finished_at": now,
                    "update_time": now,
                },
            )
            if rowcount == 1:
                await append_event(
                    self._session,
                    tenant_id=tenant_id,
                    task_id=task_id,
                    event_type=TaskEventType.CANCELLED,
                )
                return str(TaskStatus.CANCELLED)
            refreshed = await self.get(tenant_id, task_id)
            return refreshed.status
        if task.status == str(TaskStatus.RUNNING):
            rowcount = await self._cas_status(
                tenant_id,
                task_id,
                expected=(str(TaskStatus.RUNNING),),
                values={"cancel_requested": True, "update_time": now},
            )
            if rowcount == 1:
                await append_event(
                    self._session,
                    tenant_id=tenant_id,
                    task_id=task_id,
                    event_type=TaskEventType.CANCEL_REQUESTED,
                )
            return CANCEL_PENDING_STATUS
        return task.status

    async def _cas_status(
        self,
        tenant_id: str,
        task_id: uuid.UUID,
        *,
        expected: tuple[str, ...],
        values: dict[str, Any],
    ) -> int:
        result = await self._session.execute(
            update(TaskExecution)
            .where(
                TaskExecution.id == task_id,
                TaskExecution.tenant_id == tenant_id,
                TaskExecution.status.in_(expected),
                TaskExecution.is_deleted.is_(False),
            )
            .values(**values)
        )
        return int(cast(CursorResult[Any], result).rowcount)

    async def _find_by_idempotency_key(self, tenant_id: str, key: str) -> TaskExecution | None:
        return (
            await self._session.execute(
                select(TaskExecution).where(
                    TaskExecution.tenant_id == tenant_id,
                    TaskExecution.idempotency_key == key,
                    TaskExecution.is_deleted.is_(False),
                )
            )
        ).scalar_one_or_none()

    async def _resolve_route(self, payload: CreateTaskRequest) -> uuid.UUID | None:
        if payload.delivery_route is None:
            return None
        return await upsert_delivery_route(
            self._session,
            tenant_id=payload.tenant_id,
            platform_user_id=payload.actor_user_id,
            route=payload.delivery_route,
        )

    def _initial_delivery_status(self, mode: DeliveryMode) -> DeliveryStatus:
        if mode is DeliveryMode.FINAL_ONLY:
            return DeliveryStatus.PENDING
        return DeliveryStatus.NONE
