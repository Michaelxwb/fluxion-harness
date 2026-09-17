from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

import sqlalchemy as sa
from muad_common import SharedSettings
from muad_contracts import (
    DeliveryMessage,
    DeliveryMode,
    DeliveryRequest,
    DeliveryRouteInput,
    DeliveryStatus,
    TaskStatus,
)
from sqlalchemy import CursorResult, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..application.task_events import TaskEventType, append_event
from ..infrastructure.models.task import DeliveryRoute, TaskExecution
from .client import DeliveryClientProtocol, DeliveryTransportError

logger = logging.getLogger(__name__)

BACKOFF_BASE_SEC = 5
TERMINAL_STATUSES = (str(TaskStatus.COMPLETED), str(TaskStatus.FAILED), str(TaskStatus.CANCELLED))
RETRYABLE_DELIVERY_STATUSES = (str(DeliveryStatus.PENDING), str(DeliveryStatus.FAILED))


@dataclass(frozen=True)
class DeliveryOutcome:
    task_id: uuid.UUID
    http_status: int | None
    error: str | None
    sent: bool


def build_delivery_message(task: TaskExecution) -> DeliveryMessage:
    if task.status == str(TaskStatus.COMPLETED):
        text = f"任务已完成：{task.intent_key}"
    elif task.status == str(TaskStatus.FAILED):
        text = f"任务执行失败：{task.error_code or 'UNKNOWN'}"
    else:
        text = "任务已取消"
    return DeliveryMessage(text=text)


class DeliveryLoop:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        client: DeliveryClientProtocol,
        settings: SharedSettings | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._client = client
        self._settings = settings or SharedSettings()

    async def run_forever(self) -> None:
        while True:
            try:
                await self.run_once()
            except Exception:
                logger.exception("delivery_loop_tick_failed")
            await asyncio.sleep(self._settings.delivery_poll_interval_sec)

    async def run_once(self, *, now: datetime | None = None) -> DeliveryOutcome | None:
        moment = now or datetime.now(UTC)
        candidate = await self._claim_candidate(moment)
        if candidate is None:
            return None
        task, route = candidate
        if route is None:
            error = "delivery route missing"
            await self._record_terminal_failure(task, error=error, now=moment)
            return DeliveryOutcome(task_id=task.id, http_status=None, error=error, sent=False)
        request = DeliveryRequest(
            task_id=task.id,
            delivery_key=task.delivery_key,
            route=_route_input(route),
            message=build_delivery_message(task),
            artifact_ids=[],
        )
        try:
            status_code = await self._client.deliver(request)
        except DeliveryTransportError as exc:
            error = str(exc)
            await self._record_retryable_failure(task, error=error, now=moment)
            return DeliveryOutcome(task_id=task.id, http_status=None, error=error, sent=False)
        if 200 <= status_code < 300:
            await self._record_sent(task, now=moment)
            return DeliveryOutcome(task_id=task.id, http_status=status_code, error=None, sent=True)
        error = f"delivery returned status {status_code}"
        if 400 <= status_code < 500:
            await self._record_terminal_failure(task, error=error, now=moment)
        else:
            await self._record_retryable_failure(task, error=error, now=moment)
        return DeliveryOutcome(task_id=task.id, http_status=status_code, error=error, sent=False)

    async def _claim_candidate(
        self,
        moment: datetime,
    ) -> tuple[TaskExecution, DeliveryRoute | None] | None:
        backoff = sa.func.make_interval(
            0,
            0,
            0,
            0,
            0,
            0,
            BACKOFF_BASE_SEC * sa.func.power(2, TaskExecution.delivery_attempts),
        )
        statement = (
            select(TaskExecution, DeliveryRoute)
            .outerjoin(DeliveryRoute, DeliveryRoute.id == TaskExecution.delivery_route_id)
            .where(
                TaskExecution.status.in_(TERMINAL_STATUSES),
                TaskExecution.delivery_mode == str(DeliveryMode.FINAL_ONLY),
                TaskExecution.delivery_status.in_(RETRYABLE_DELIVERY_STATUSES),
                TaskExecution.delivery_attempts < self._settings.delivery_max_attempts,
                or_(
                    TaskExecution.delivery_status == str(DeliveryStatus.PENDING),
                    TaskExecution.update_time < moment - backoff,
                ),
                TaskExecution.is_deleted.is_(False),
            )
            .order_by(TaskExecution.create_time.asc())
            .with_for_update(of=TaskExecution, skip_locked=True)
            .limit(1)
        )
        async with self._session_factory() as session:
            async with session.begin():
                row = (await session.execute(statement)).first()
        if row is None:
            return None
        return row[0], row[1]

    async def _record_sent(self, task: TaskExecution, *, now: datetime) -> None:
        await self._apply(
            task,
            values={
                "delivery_status": str(DeliveryStatus.SENT),
                "delivered_at": now,
                "update_time": now,
            },
            event_type=TaskEventType.DELIVERY_SENT,
            payload={"delivery_attempts": task.delivery_attempts},
        )

    async def _record_terminal_failure(
        self,
        task: TaskExecution,
        *,
        error: str,
        now: datetime,
    ) -> None:
        await self._apply(
            task,
            values={
                "delivery_status": str(DeliveryStatus.FAILED),
                "delivery_attempts": self._settings.delivery_max_attempts,
                "update_time": now,
            },
            event_type=TaskEventType.DELIVERY_FAILED,
            payload={"error": error, "terminal": True},
        )

    async def _record_retryable_failure(
        self,
        task: TaskExecution,
        *,
        error: str,
        now: datetime,
    ) -> None:
        attempts = task.delivery_attempts + 1
        exhausted = attempts >= self._settings.delivery_max_attempts
        await self._apply(
            task,
            values={
                "delivery_status": str(DeliveryStatus.FAILED),
                "delivery_attempts": attempts,
                "update_time": now,
            },
            event_type=(
                TaskEventType.DELIVERY_FAILED if exhausted else TaskEventType.DELIVERY_RETRY
            ),
            payload={"error": error, "delivery_attempts": attempts, "terminal": exhausted},
        )

    async def _apply(
        self,
        task: TaskExecution,
        *,
        values: dict[str, Any],
        event_type: TaskEventType,
        payload: dict[str, Any],
    ) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    update(TaskExecution)
                    .where(
                        TaskExecution.id == task.id,
                        TaskExecution.delivery_status.in_(RETRYABLE_DELIVERY_STATUSES),
                        TaskExecution.delivery_attempts == task.delivery_attempts,
                        TaskExecution.is_deleted.is_(False),
                    )
                    .values(**values)
                )
                if int(cast(CursorResult[Any], result).rowcount) == 1:
                    await append_event(
                        session,
                        tenant_id=task.tenant_id,
                        task_id=task.id,
                        event_type=event_type,
                        payload=payload,
                    )


def _route_input(route: DeliveryRoute) -> DeliveryRouteInput:
    return DeliveryRouteInput.model_validate(
        {
            "channel": route.channel,
            "bot_id": route.bot_id,
            "external_user_id": route.external_user_id,
            "external_conversation_id": route.external_conversation_id,
        }
    )
