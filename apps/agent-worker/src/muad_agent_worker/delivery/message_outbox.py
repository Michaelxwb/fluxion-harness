"""`task.message_outbox` 消费者（design §4.6）：把 Task 终态消息交给 Runtime。

- 领取只动 **PENDING + 到期 + 租约空闲** 的行；`attempts` 在预留时自增，崩溃不丢退避预算。
- Runtime 明确受理（含幂等重放）→ `APPLIED`；明确终态拒绝 → `FAILED`；没有结论 → 退避重试。
- 路由永久失效时同事务把来源 Schedule 置 `NEEDS_ATTENTION` 并写审计事件。
- 发送是 Runtime `message_delivery` 的事：本循环只负责“交出去”，不判断用户是否收到。
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from muad_common import SharedSettings
from muad_contracts import MessageDeliveryEnqueueRequest
from muad_contracts.platform_settings import PlatformSettings
from muad_logging import get_logger
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..application.ports import NullPlatformSettingsClient, PlatformSettingsClient
from ..application.schedule_attention import flag_schedule_attention
from ..application.task_message_outbox import OUTBOX_PENDING
from ..infrastructure.models.task import TaskMessageOutbox
from ..metrics import record_loop_tick
from .message_outbox_client import (
    MessageOutboxClientProtocol,
    MessageOutboxRejected,
    MessageOutboxTransportError,
)

logger = get_logger(__name__)

OUTBOX_APPLIED = "APPLIED"
OUTBOX_FAILED = "FAILED"
#: 预留租约：远大于一次 HTTP 受理；进程崩溃后由租约过期接管。
OUTBOX_LEASE_SEC = 300
#: 没有结论时的退避上限；设置不可读时也用这个固定值，不猜租户策略。
MAX_BACKOFF_SEC = 3600
SETTINGS_RETRY_SEC = 30
CODE_SETTINGS_UNAVAILABLE = "PLATFORM_SETTINGS_UNAVAILABLE"
CODE_PAYLOAD_INVALID = "MESSAGE_OUTBOX_PAYLOAD_INVALID"
CODE_ATTEMPTS_EXHAUSTED = "MESSAGE_OUTBOX_ATTEMPTS_EXHAUSTED"
CODE_TRANSPORT = "MESSAGE_DELIVERY_TRANSPORT_ERROR"
#: 路由失效的业务码：终态失败并把来源 Schedule 置 NEEDS_ATTENTION。
ROUTE_UNAVAILABLE_CODES = frozenset(
    {"SCHEDULE_DELIVERY_UNSUPPORTED", "BOT_NOT_FOUND", "FORBIDDEN", "DELIVERY_ROUTE_UNAVAILABLE"}
)


@dataclass(frozen=True, slots=True)
class OutboxOutcome:
    outbox_id: uuid.UUID
    task_id: uuid.UUID
    applied: bool
    error_code: str | None


@dataclass(frozen=True, slots=True)
class _ClaimedOutbox:
    outbox_id: uuid.UUID
    tenant_id: str
    task_id: uuid.UUID
    message_key: str
    kind: str
    target_key: str
    route_json: dict[str, Any]
    payload_json: dict[str, Any]
    attempts: int


class TaskMessageOutboxLoop:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        client: MessageOutboxClientProtocol,
        settings: SharedSettings | None = None,
        *,
        settings_client: PlatformSettingsClient | None = None,
        lease_owner: str | None = None,
    ) -> None:
        self._sessions = session_factory
        self._client = client
        self._settings = settings or SharedSettings()
        self._settings_client: PlatformSettingsClient = (
            settings_client or NullPlatformSettingsClient()
        )
        self._owner = lease_owner or f"message-outbox-{uuid.uuid4()}"

    async def run_forever(self) -> None:
        while True:
            try:
                for _ in range(self._settings.delivery_batch_size):
                    if await self.run_once() is None:
                        break
            except Exception:
                record_loop_tick("worker.message_outbox", "failed")
                logger.exception("message_outbox_loop_tick_failed")
            else:
                record_loop_tick("worker.message_outbox", "ok")
            await asyncio.sleep(self._settings.delivery_poll_interval_sec)

    async def run_once(self, *, now: datetime | None = None) -> OutboxOutcome | None:
        moment = now or datetime.now(UTC)
        claimed = await self._claim(moment)
        if claimed is None:
            return None
        platform = await self._platform_for(claimed.tenant_id)
        if platform is None:
            await self._schedule_retry(
                claimed,
                moment=moment,
                code=CODE_SETTINGS_UNAVAILABLE,
                delay_sec=SETTINGS_RETRY_SEC,
            )
            return OutboxOutcome(
                outbox_id=claimed.outbox_id,
                task_id=claimed.task_id,
                applied=False,
                error_code=CODE_SETTINGS_UNAVAILABLE,
            )
        task_settings = platform.task
        if claimed.attempts > task_settings.delivery_max_attempts:
            return await self._mark_failed(
                claimed, moment=moment, code=CODE_ATTEMPTS_EXHAUSTED
            )
        try:
            request = MessageDeliveryEnqueueRequest.model_validate(
                {
                    "tenant_id": claimed.tenant_id,
                    "owner_type": "TASK",
                    "owner_id": claimed.task_id,
                    "message_key": claimed.message_key,
                    "kind": claimed.kind,
                    "target_key": claimed.target_key,
                    "route": claimed.route_json,
                    "payload": claimed.payload_json,
                }
            )
        except ValueError:
            return await self._mark_failed(
                claimed, moment=moment, code=CODE_PAYLOAD_INVALID
            )
        try:
            await self._client.enqueue(
                tenant_id=claimed.tenant_id, request=request
            )
        except MessageOutboxTransportError:
            return await self._retry_or_fail(
                claimed,
                moment=moment,
                code=CODE_TRANSPORT,
                base_sec=task_settings.delivery_backoff_base_sec,
                max_attempts=task_settings.delivery_max_attempts,
            )
        except MessageOutboxRejected as exc:
            if exc.retryable:
                return await self._retry_or_fail(
                    claimed,
                    moment=moment,
                    code=exc.code,
                    base_sec=task_settings.delivery_backoff_base_sec,
                    max_attempts=task_settings.delivery_max_attempts,
                )
            return await self._mark_failed(claimed, moment=moment, code=exc.code)
        await self._mark_applied(claimed, moment)
        return OutboxOutcome(
            outbox_id=claimed.outbox_id,
            task_id=claimed.task_id,
            applied=True,
            error_code=None,
        )

    async def _claim(self, moment: datetime) -> _ClaimedOutbox | None:
        async with self._sessions() as session, session.begin():
            outbox_id = await session.scalar(
                select(TaskMessageOutbox.id)
                .where(
                    TaskMessageOutbox.status == OUTBOX_PENDING,
                    TaskMessageOutbox.next_attempt_at <= moment,
                    TaskMessageOutbox.is_deleted.is_(False),
                    or_(
                        TaskMessageOutbox.lease_until.is_(None),
                        TaskMessageOutbox.lease_until < moment,
                    ),
                )
                .order_by(
                    TaskMessageOutbox.next_attempt_at,
                    TaskMessageOutbox.create_time,
                    TaskMessageOutbox.id,
                )
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if outbox_id is None:
                return None
            row = await session.scalar(
                select(TaskMessageOutbox)
                .where(TaskMessageOutbox.id == outbox_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if row is None:
                return None
            row.attempts += 1
            row.lease_owner = self._owner
            row.lease_until = moment + timedelta(seconds=OUTBOX_LEASE_SEC)
            row.update_time = moment
            return _ClaimedOutbox(
                outbox_id=row.id,
                tenant_id=row.tenant_id,
                task_id=row.task_id,
                message_key=row.message_key,
                kind=row.kind,
                target_key=row.target_key,
                route_json=dict(row.route_json or {}),
                payload_json=dict(row.payload_json or {}),
                attempts=int(row.attempts),
            )

    async def _platform_for(self, tenant_id: str) -> PlatformSettings | None:
        from ..application.platform_settings import resolve_platform_settings

        try:
            snapshot = await self._settings_client.fetch_snapshot(tenant_id=tenant_id)
            return resolve_platform_settings(
                snapshot, batch_platform_limit=self._settings.batch_platform_limit
            )
        except Exception:
            logger.warning(
                "message_outbox_platform_settings_unavailable tenant_id=%s",
                tenant_id,
                exc_info=True,
            )
            return None

    async def _mark_applied(self, claimed: _ClaimedOutbox, moment: datetime) -> None:
        async with self._sessions() as session, session.begin():
            await session.execute(
                update(TaskMessageOutbox)
                .where(
                    TaskMessageOutbox.id == claimed.outbox_id,
                    TaskMessageOutbox.status == OUTBOX_PENDING,
                    TaskMessageOutbox.lease_owner == self._owner,
                )
                .values(
                    status=OUTBOX_APPLIED,
                    lease_owner=None,
                    lease_until=None,
                    last_error_code=None,
                    update_time=moment,
                )
                .execution_options(synchronize_session=False)
            )

    async def _retry_or_fail(
        self,
        claimed: _ClaimedOutbox,
        *,
        moment: datetime,
        code: str,
        base_sec: int,
        max_attempts: int,
    ) -> OutboxOutcome:
        if claimed.attempts >= max_attempts:
            return await self._mark_failed(claimed, moment=moment, code=code)
        delay = min(base_sec * (2 ** (claimed.attempts - 1)), MAX_BACKOFF_SEC)
        await self._schedule_retry(claimed, moment=moment, code=code, delay_sec=delay)
        return OutboxOutcome(
            outbox_id=claimed.outbox_id,
            task_id=claimed.task_id,
            applied=False,
            error_code=code,
        )

    async def _schedule_retry(
        self,
        claimed: _ClaimedOutbox,
        *,
        moment: datetime,
        code: str,
        delay_sec: int,
    ) -> None:
        async with self._sessions() as session, session.begin():
            await session.execute(
                update(TaskMessageOutbox)
                .where(
                    TaskMessageOutbox.id == claimed.outbox_id,
                    TaskMessageOutbox.status == OUTBOX_PENDING,
                    TaskMessageOutbox.lease_owner == self._owner,
                )
                .values(
                    lease_owner=None,
                    lease_until=None,
                    next_attempt_at=moment + timedelta(seconds=delay_sec),
                    last_error_code=code[:64],
                    update_time=moment,
                )
                .execution_options(synchronize_session=False)
            )

    async def _mark_failed(
        self, claimed: _ClaimedOutbox, *, moment: datetime, code: str
    ) -> OutboxOutcome:
        async with self._sessions() as session, session.begin():
            updated = await session.scalar(
                update(TaskMessageOutbox)
                .where(
                    TaskMessageOutbox.id == claimed.outbox_id,
                    TaskMessageOutbox.status == OUTBOX_PENDING,
                    TaskMessageOutbox.lease_owner == self._owner,
                )
                .values(
                    status=OUTBOX_FAILED,
                    lease_owner=None,
                    lease_until=None,
                    last_error_code=code[:64],
                    update_time=moment,
                )
                .returning(TaskMessageOutbox.id)
                .execution_options(synchronize_session=False)
            )
            if updated is not None and code in ROUTE_UNAVAILABLE_CODES:
                await flag_schedule_attention(
                    session,
                    tenant_id=claimed.tenant_id,
                    execution_id=claimed.task_id,
                    code=code,
                    now=moment,
                )
        return OutboxOutcome(
            outbox_id=claimed.outbox_id,
            task_id=claimed.task_id,
            applied=False,
            error_code=code,
        )


__all__ = ["OutboxOutcome", "TaskMessageOutboxLoop"]
