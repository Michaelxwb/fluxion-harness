"""消息级 push 投递：`runtime.message_delivery` 的写入与租约状态机（design §4.5/§5.6）。

**一个消息对一个目标一条意图**：`(tenant_id, message_key, target_key)` 是完整唯一事实。
`message_key` 跨自动重试/崩溃恢复稳定；用户显式"再发一次"使用新的 `message_id` 生成新键，
因此允许再次发送。`UNKNOWN` 保留核实、不自动重发；`DEGRADED` 是降级成功（带取件链接），
不是 `DELIVERED`。

租约决策复用 `delivery_lease.DeliveryLeaseProtocol`（与 `channel_notification` 同一套），
本模块只做行锁、列映射与 `message_delivery_attempt` 回执。
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

import sqlalchemy as sa
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import (
    AttachmentRef,
    DeliveryRouteInput,
)
from muad_contracts import delivery_target_key as delivery_target_key
from muad_contracts.canonical import ensure_strict_json
from pydantic import JsonValue, ValidationError
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.db import SessionFactoryProvider
from ..infrastructure.db_clock import database_now
from ..infrastructure.models.runtime import (
    MessageDelivery,
    MessageDeliveryAttempt,
)
from .delivery_lease import (
    DeliveryLeaseProtocol,
    DeliveryPolicy,
    DeliveryReceipt,
    DeliveryStates,
    LeaseGrant,
    LeaseSnapshot,
)

#: `message_delivery.status` 词表（数据库 CHECK 是权威）。
MESSAGE_PENDING = "PENDING"
MESSAGE_DELIVERING = "DELIVERING"
MESSAGE_DELIVERED = "DELIVERED"
MESSAGE_DEGRADED = "DEGRADED"
MESSAGE_UNKNOWN = "UNKNOWN"
MESSAGE_FAILED = "FAILED"
MESSAGE_DELIVERY_STATES = (
    MESSAGE_PENDING,
    MESSAGE_DELIVERING,
    MESSAGE_DELIVERED,
    MESSAGE_DEGRADED,
    MESSAGE_UNKNOWN,
    MESSAGE_FAILED,
)

MessageOwnerType = Literal["RUN", "TASK"]
MessageKind = Literal["TERMINAL", "ARTIFACT", "HUMAN_QUESTION", "TASK_RESULT"]
MESSAGE_KIND_TERMINAL: MessageKind = "TERMINAL"
MESSAGE_KIND_ARTIFACT: MessageKind = "ARTIFACT"
MESSAGE_KIND_HUMAN_QUESTION: MessageKind = "HUMAN_QUESTION"
MESSAGE_KIND_TASK_RESULT: MessageKind = "TASK_RESULT"

#: 消息级回执语义：DEGRADED 是降级成功，可重放；UNKNOWN/FAILED 保留，不再授租约。
_MESSAGE_STATES = DeliveryStates(
    pending=MESSAGE_PENDING,
    delivering=MESSAGE_DELIVERING,
    delivered=MESSAGE_DELIVERED,
    unknown=MESSAGE_UNKNOWN,
    not_required=None,
    ack_states={
        "DELIVERED": MESSAGE_DELIVERED,
        "DEGRADED": MESSAGE_DEGRADED,
        "NOT_SENT": MESSAGE_PENDING,
        "UNKNOWN": MESSAGE_UNKNOWN,
        "FAILED": MESSAGE_FAILED,
    },
    replay_states=frozenset({MESSAGE_DELIVERED, MESSAGE_DEGRADED}),
    no_lease_states=frozenset({MESSAGE_UNKNOWN, MESSAGE_FAILED}),
)
#: 快照上限：正文与附件是**有界**载荷，重试只读快照，不随产出增长。
MAX_DELIVERY_TEXT_CHARS = 20_000
MAX_DELIVERY_ATTACHMENTS = 20


def run_terminal_message_key(run_id: uuid.UUID) -> str:
    """Run 终态的稳定消息身份（一个 Run 只有一个终态）。"""
    return f"run:{run_id}:terminal"


def run_message_key(run_id: uuid.UUID, message_id: uuid.UUID | str) -> str:
    """Run 内一条独立消息的身份；显式重发用新 `message_id`，允许再次发送。"""
    return f"run:{run_id}:message:{message_id}"


def run_waiting_message_key(run_id: uuid.UUID, interrupt_id: uuid.UUID) -> str:
    """等待人工确认的消息身份（一次中断一份，重投命中同一行）。"""
    return f"run:{run_id}:message:{interrupt_id}"


def execution_final_message_key(task_id: uuid.UUID) -> str:
    """Task 最终结果的消息身份（Task 侧 outbox 在 TASK-004 接入）。"""
    return f"execution:{task_id}:final"


def execution_message_key(task_id: uuid.UUID, message_id: uuid.UUID | str) -> str:
    return f"execution:{task_id}:message:{message_id}"


@dataclass(frozen=True, slots=True)
class MessageDeliveryPayload:
    """`payload_json` 的有界快照：正文 + 附件引用 + 终态渲染事实。"""

    text: str = ""
    attachments: tuple[AttachmentRef, ...] = field(default_factory=tuple)
    status: str = ""
    error_code: str | None = None

    def __post_init__(self) -> None:
        if len(self.text) > MAX_DELIVERY_TEXT_CHARS:
            raise ValueError("delivery text exceeds the snapshot bound")
        if len(self.attachments) > MAX_DELIVERY_ATTACHMENTS:
            raise ValueError("delivery attachments exceed the snapshot bound")
        if not self.text and not self.attachments and not self.status:
            raise ValueError("delivery payload must carry text, attachments or terminal status")

    def to_json(self) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "text": self.text,
            "attachments": [
                attachment.model_dump(mode="json") for attachment in self.attachments
            ],
        }
        if self.status:
            payload["status"] = self.status
        if self.error_code:
            payload["error_code"] = self.error_code
        return ensure_strict_json(payload)

    @classmethod
    def from_json(cls, payload: Mapping[str, Any] | None) -> MessageDeliveryPayload:
        """严格解析已冻结快照；坏载荷显式失败，不静默当空消息。"""
        value = payload or {}
        try:
            text = str(value.get("text") or "")
            status = str(value.get("status") or "")
            error_code = value.get("error_code")
            if error_code is not None and not isinstance(error_code, str):
                raise ValueError("error_code must be a string")
            raw_attachments = value.get("attachments") or []
            if not isinstance(raw_attachments, list):
                raise ValueError("attachments must be a list")
            attachments = tuple(
                AttachmentRef.model_validate(item) for item in raw_attachments
            )
        except (TypeError, ValueError, ValidationError) as exc:
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR) from exc
        try:
            return cls(
                text=text,
                attachments=attachments,
                status=status,
                error_code=error_code,
            )
        except ValueError as exc:
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR) from exc


@dataclass(frozen=True, slots=True)
class MessageDeliveryReceipt:
    id: uuid.UUID
    created: bool
    status: str
    message_key: str
    target_key: str


@dataclass(frozen=True, slots=True)
class MessageDeliveryLease:
    delivery_id: uuid.UUID
    message_key: str
    target_key: str
    state: str
    lease_epoch: int
    lease_until: datetime | None
    attempt: int
    reused: bool
    receipt: DeliveryReceipt | None = None


@dataclass(frozen=True, slots=True)
class MessageDeliveryConfirmation:
    delivery_id: uuid.UUID
    message_key: str
    target_key: str
    state: str
    attempt: int
    reused: bool
    receipt: DeliveryReceipt | None = None


async def enqueue_message_delivery(
    session: AsyncSession,
    *,
    tenant_id: str,
    owner_type: MessageOwnerType,
    owner_id: uuid.UUID,
    message_key: str,
    kind: MessageKind,
    target_key: str,
    route: DeliveryRouteInput,
    payload: MessageDeliveryPayload,
    message_id: str | None = None,
    agent_id: uuid.UUID | None = None,
    platform_user_id: uuid.UUID | None = None,
) -> MessageDeliveryReceipt:
    """同一事务内写一条消息级意图；同 `(message_key, target_key)` 幂等返回原行。"""
    inserted = await session.scalar(
        pg_insert(MessageDelivery)
        .values(
            tenant_id=tenant_id,
            owner_type=owner_type,
            owner_id=owner_id,
            message_key=message_key,
            kind=kind,
            target_key=target_key,
            route_json=route.model_dump(mode="json"),
            payload_json=payload.to_json(),
            message_id=message_id,
            agent_id=agent_id,
            platform_user_id=platform_user_id,
            status=MESSAGE_PENDING,
        )
        .on_conflict_do_nothing(
            index_elements=["tenant_id", "message_key", "target_key"]
        )
        .returning(MessageDelivery.id)
    )
    row = await session.scalar(
        sa.select(MessageDelivery).where(
            MessageDelivery.tenant_id == tenant_id,
            MessageDelivery.message_key == message_key,
            MessageDelivery.target_key == target_key,
        )
    )
    if row is None:
        raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
    return MessageDeliveryReceipt(
        id=row.id,
        created=inserted is not None,
        status=row.status,
        message_key=row.message_key,
        target_key=row.target_key,
    )


class TenantMessageDelivery:
    """按租户的 message_delivery 门面：每次操作取一次设置快照（事务外）。"""

    def __init__(
        self,
        session_factory_provider: SessionFactoryProvider,
        settings_client: Any,
    ) -> None:
        self._sessions = session_factory_provider
        self._settings = settings_client

    async def reserve(
        self,
        *,
        tenant_id: str,
        message_key: str,
        target_key: str,
        attempt_id: uuid.UUID,
        lease_sec: int | None = None,
    ) -> MessageDeliveryLease:
        service = await self._service(tenant_id)
        return await service.reserve(
            tenant_id=tenant_id,
            message_key=message_key,
            target_key=target_key,
            attempt_id=attempt_id,
            lease_sec=lease_sec,
        )

    async def renew(
        self,
        *,
        tenant_id: str,
        delivery_id: uuid.UUID,
        message_key: str,
        target_key: str,
        attempt_id: uuid.UUID,
        epoch: int,
        lease_sec: int | None = None,
    ) -> MessageDeliveryLease:
        service = await self._service(tenant_id)
        return await service.renew(
            tenant_id=tenant_id,
            delivery_id=delivery_id,
            message_key=message_key,
            target_key=target_key,
            attempt_id=attempt_id,
            epoch=epoch,
            lease_sec=lease_sec,
        )

    async def ack(
        self,
        *,
        tenant_id: str,
        delivery_id: uuid.UUID,
        message_key: str,
        target_key: str,
        attempt_id: uuid.UUID,
        epoch: int,
        outcome: str,
        result: Mapping[str, JsonValue] | None = None,
    ) -> MessageDeliveryConfirmation:
        service = await self._service(tenant_id)
        return await service.ack(
            tenant_id=tenant_id,
            delivery_id=delivery_id,
            message_key=message_key,
            target_key=target_key,
            attempt_id=attempt_id,
            epoch=epoch,
            outcome=outcome,
            result=result,
        )

    async def _service(self, tenant_id: str) -> MessageDeliveryService:
        from .channel_delivery import delivery_policy_from_settings

        snapshot = await self._settings.fetch_snapshot(tenant_id=tenant_id)
        return MessageDeliveryService(
            self._sessions, delivery_policy_from_settings(snapshot.settings)
        )


class MessageDeliveryService:
    """message_delivery 的持久状态机：每个方法一个短事务，行锁内推进事实。"""

    def __init__(
        self,
        session_factory_provider: SessionFactoryProvider,
        policy: DeliveryPolicy,
    ) -> None:
        self._sessions = session_factory_provider
        self._protocol = DeliveryLeaseProtocol(policy, _MESSAGE_STATES)

    async def reserve(
        self,
        *,
        tenant_id: str,
        message_key: str,
        target_key: str,
        attempt_id: uuid.UUID,
        lease_sec: int | None = None,
    ) -> MessageDeliveryLease:
        lease_seconds = self._protocol.lease_seconds(lease_sec)
        async with self._sessions()() as session, session.begin():
            row = await _lock_by_key(
                session,
                tenant_id=tenant_id,
                message_key=message_key,
                target_key=target_key,
            )
            now = await database_now(session)
            decision = self._protocol.reserve(
                _snapshot(row),
                now=now,
                attempt_id=attempt_id,
                lease_seconds=lease_seconds,
            )
            if decision.action == "grant" and decision.grant is not None:
                _apply_grant(row, decision.grant, now=now, attempt_id=attempt_id)
                await _open_attempt(
                    session, row, grant=decision.grant, now=now
                )
                await session.flush()
                return _lease_view(row, reused=False)
            if decision.action == "reuse" and decision.grant is not None:
                return _lease_view(row, reused=True, receipt=decision.receipt)
            return _no_lease_view(row, reused=True, receipt=decision.receipt)

    async def renew(
        self,
        *,
        tenant_id: str,
        delivery_id: uuid.UUID,
        message_key: str,
        target_key: str,
        attempt_id: uuid.UUID,
        epoch: int,
        lease_sec: int | None = None,
    ) -> MessageDeliveryLease:
        lease_seconds = self._protocol.lease_seconds(lease_sec)
        async with self._sessions()() as session, session.begin():
            row = await _lock_by_id(
                session,
                tenant_id=tenant_id,
                delivery_id=delivery_id,
                message_key=message_key,
                target_key=target_key,
            )
            now = await database_now(session)
            grant = self._protocol.renew(
                _snapshot(row),
                now=now,
                attempt_id=attempt_id,
                epoch=epoch,
                lease_seconds=lease_seconds,
            )
            row.lease_until = grant.lease_until
            row.update_time = now
            await session.flush()
            return _lease_view(row, reused=False)

    async def ack(
        self,
        *,
        tenant_id: str,
        delivery_id: uuid.UUID,
        message_key: str,
        target_key: str,
        attempt_id: uuid.UUID,
        epoch: int,
        outcome: str,
        result: Mapping[str, JsonValue] | None = None,
    ) -> MessageDeliveryConfirmation:
        async with self._sessions()() as session, session.begin():
            row = await _lock_by_id(
                session,
                tenant_id=tenant_id,
                delivery_id=delivery_id,
                message_key=message_key,
                target_key=target_key,
            )
            now = await database_now(session)
            decision = self._protocol.ack(
                _snapshot(row),
                now=now,
                attempt_id=attempt_id,
                epoch=epoch,
                outcome=outcome,
                result=result,
            )
            if decision.reused:
                return _confirmation(row, decision.receipt, reused=True)
            receipt = decision.receipt
            if receipt is None:
                raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
            _apply_ack(
                row,
                decision.state,
                receipt=receipt,
                now=now,
                next_attempt_at=decision.next_attempt_at,
            )
            await _close_attempt(
                session, row, receipt=receipt, now=now, state=decision.state
            )
            await session.flush()
            return _confirmation(row, receipt, reused=False)


def message_delivery_due_statement(
    *, now: datetime, max_attempts: int, limit: int
) -> sa.Select[Any]:
    """到期消息的领取谓词：与 `ix_message_delivery_due` 同形。"""
    return (
        sa.select(MessageDelivery)
        .where(
            MessageDelivery.status == MESSAGE_PENDING,
            MessageDelivery.next_attempt_at <= now,
            MessageDelivery.attempt_count < max_attempts,
        )
        .order_by(
            MessageDelivery.tenant_id,
            MessageDelivery.next_attempt_at,
            MessageDelivery.id,
        )
        .limit(limit)
    )


def delivery_payload_of(row: MessageDelivery) -> MessageDeliveryPayload:
    return MessageDeliveryPayload.from_json(row.payload_json)


def delivery_route_of_row(row: MessageDelivery) -> DeliveryRouteInput:
    return DeliveryRouteInput.model_validate(row.route_json or {})


async def _lock_by_key(
    session: AsyncSession,
    *,
    tenant_id: str,
    message_key: str,
    target_key: str,
) -> MessageDelivery:
    row = await session.scalar(
        sa.select(MessageDelivery)
        .where(
            MessageDelivery.tenant_id == tenant_id,
            MessageDelivery.message_key == message_key,
            MessageDelivery.target_key == target_key,
        )
        .with_for_update()
    )
    if row is None:
        raise AppError(ErrorCode.COMMON_NOT_FOUND)
    return row


async def _lock_by_id(
    session: AsyncSession,
    *,
    tenant_id: str,
    delivery_id: uuid.UUID,
    message_key: str,
    target_key: str,
) -> MessageDelivery:
    row = await session.scalar(
        sa.select(MessageDelivery)
        .where(
            MessageDelivery.tenant_id == tenant_id,
            MessageDelivery.id == delivery_id,
        )
        .with_for_update()
    )
    if row is None or (row.message_key, row.target_key) != (message_key, target_key):
        raise AppError(ErrorCode.COMMON_NOT_FOUND)
    return row


def _snapshot(row: MessageDelivery) -> LeaseSnapshot:
    payload = row.delivery_result
    return LeaseSnapshot(
        state=row.status,
        attempt=int(row.attempt_count),
        lease_owner=row.lease_owner,
        lease_epoch=int(row.lease_epoch),
        lease_until=row.lease_until,
        receipt=None if payload is None else DeliveryReceipt.from_json(payload),
    )


def _apply_grant(
    row: MessageDelivery,
    grant: LeaseGrant,
    *,
    now: datetime,
    attempt_id: uuid.UUID,
) -> None:
    row.status = grant.state
    row.attempt_count = grant.attempt
    row.lease_owner = str(attempt_id)
    row.lease_epoch = grant.lease_epoch
    row.lease_until = grant.lease_until
    row.update_time = now


def _apply_ack(
    row: MessageDelivery,
    state: str,
    *,
    receipt: DeliveryReceipt,
    now: datetime,
    next_attempt_at: datetime | None,
) -> None:
    result = receipt.result or {}
    row.delivery_result = receipt.to_json()
    row.status = state
    row.lease_owner, row.lease_until = None, None
    row.update_time = now
    if state in (MESSAGE_DELIVERED, MESSAGE_DEGRADED):
        row.delivered_at = now
    if state == MESSAGE_DEGRADED:
        fallback = result.get("fallback_url")
        row.fallback_url = fallback if isinstance(fallback, str) else None
        row.last_error_code = "DEGRADED"
    elif state == MESSAGE_PENDING:
        row.last_error_code = _error_code(result, "NOT_SENT")
        row.last_error_message = _error_message(result)
        if next_attempt_at is not None:
            row.next_attempt_at = next_attempt_at
    elif state == MESSAGE_UNKNOWN:
        row.last_error_code = _error_code(result, "UNKNOWN")
        row.last_error_message = _error_message(result)
    elif state == MESSAGE_FAILED:
        row.last_error_code = _error_code(result, "FAILED")
        row.last_error_message = _error_message(result)
    else:
        row.last_error_code = None
        row.last_error_message = None


def _error_code(result: Mapping[str, JsonValue], fallback: str) -> str:
    value = result.get("code") or result.get("reason")
    return value if isinstance(value, str) and value else fallback


def _error_message(result: Mapping[str, JsonValue]) -> str | None:
    value = result.get("message") or result.get("reason")
    return value if isinstance(value, str) and value else None


async def _open_attempt(
    session: AsyncSession,
    row: MessageDelivery,
    *,
    grant: LeaseGrant,
    now: datetime,
) -> None:
    session.add(
        MessageDeliveryAttempt(
            tenant_id=row.tenant_id,
            delivery_id=row.id,
            attempt_no=grant.attempt,
            lease_epoch=grant.lease_epoch,
            status=MESSAGE_DELIVERING,
            started_at=now,
        )
    )


async def _close_attempt(
    session: AsyncSession,
    row: MessageDelivery,
    *,
    receipt: DeliveryReceipt,
    now: datetime,
    state: str,
) -> None:
    attempt = await session.scalar(
        sa.select(MessageDeliveryAttempt)
        .where(
            MessageDeliveryAttempt.tenant_id == row.tenant_id,
            MessageDeliveryAttempt.delivery_id == row.id,
            MessageDeliveryAttempt.attempt_no == receipt.attempt,
        )
        .with_for_update()
    )
    result = receipt.result or {}
    if attempt is None:
        session.add(
            MessageDeliveryAttempt(
                tenant_id=row.tenant_id,
                delivery_id=row.id,
                attempt_no=receipt.attempt,
                lease_epoch=receipt.lease_epoch,
                status=state,
                error_code=_error_code(result, state) if state != MESSAGE_DELIVERED else None,
                error_message=_error_message(result),
                result_json=dict(result),
                started_at=receipt.acked_at,
                finished_at=now,
            )
        )
        return
    attempt.status = state
    attempt.result_json = dict(result)
    attempt.error_code = _error_code(result, state) if state != MESSAGE_DELIVERED else None
    attempt.error_message = _error_message(result)
    attempt.finished_at = now


def _lease_view(
    row: MessageDelivery, *, reused: bool, receipt: DeliveryReceipt | None = None
) -> MessageDeliveryLease:
    return MessageDeliveryLease(
        delivery_id=row.id,
        message_key=row.message_key,
        target_key=row.target_key,
        state=row.status,
        lease_epoch=int(row.lease_epoch),
        lease_until=row.lease_until,
        attempt=int(row.attempt_count),
        reused=reused,
        receipt=receipt,
    )


def _no_lease_view(
    row: MessageDelivery, *, reused: bool, receipt: DeliveryReceipt | None
) -> MessageDeliveryLease:
    """没有授出租约：调用方不得发送（已送达/已降级重放、UNKNOWN/FAILED 保留）。"""
    return MessageDeliveryLease(
        delivery_id=row.id,
        message_key=row.message_key,
        target_key=row.target_key,
        state=row.status,
        lease_epoch=0,
        lease_until=None,
        attempt=int(row.attempt_count),
        reused=reused,
        receipt=receipt,
    )


def _confirmation(
    row: MessageDelivery, receipt: DeliveryReceipt | None, *, reused: bool
) -> MessageDeliveryConfirmation:
    return MessageDeliveryConfirmation(
        delivery_id=row.id,
        message_key=row.message_key,
        target_key=row.target_key,
        state=row.status,
        attempt=int(row.attempt_count),
        reused=reused,
        receipt=receipt,
    )
