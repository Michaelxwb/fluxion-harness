"""API-18：出站投递的持久 reserve/renew/ack 协议（design §3.3/§3.4/§3.6）。

投递事实全部落在 `runtime.channel_notification` **一行**上（0026 已备列，不新增 DDL）：

- **PENDING**（出站意图，`next_attempt_at` 到期可领）→ **DELIVERING**（`lease_owner` = 本次
  attempt、`lease_epoch` 单调递增、`lease_until` 有界）→ ack 后
  **DELIVERED**（实际发送成功，`delivery_result` 冻结**首次**结果，租约清除）/
  **PENDING**（`NOT_SENT`：确定没发出去，退避后重试）/
  **UNKNOWN**（没拿到结论：保留核实，不自动重发、不标"已送达"）。
- **重复 reserve 不重复投递**：同一 attempt 在租约有效期内重放原租约（epoch/期限不变、
  `attempt` 不增）；不同 attempt 只能接管**已过期**的租约（epoch+1 发新号 = 过期租约回收）。
- **epoch 防重 / 失约即停手**：renew/ack 必须持有**仍有效**的租约并核 attempt+epoch，否则
  `DELIVERY_LEASE_LOST`；被接管的旧 attempt 不能靠迟到 ack 改写结果。
- **ack 幂等**：同一 attempt+epoch 的重复 ack 回放冻结的首次结果，不重写、不再次推进状态。
- **已送达重放首次结果**：DELIVERED 行再次 reserve 不重发，回放首次结果；超过回执重放保留期
  显式 `DELIVERY_RECEIPT_EXPIRED`，不当成新投递重新发送。
- **存在占位不等于成功**：授出租约（DELIVERING）本身没有任何送达含义，只有
  `receipt.outcome=DELIVERED` 是送达事实。
- **NOT_REQUIRED 不是出站意图**：网页持久入箱行对 reserve 显式冲突（`COMMON_CONFLICT`），
  不会被误当成待发消息。
- **时间**：租约期限、退避与回执时刻一律用 PG 服务端时钟（`database_now`）；事务内**不发起
  任何网络请求**（设置快照由调用方在事务外取一次）。
- **`next_attempt_at` 是 PENDING 的到期时间**（投递泵的领取谓词，TASK-066）；`reserve` 不据此
  拒绝：活跃回复会话的收尾与兜底交付可以在到期前就同一稳定键协调（design §3.6）。
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import cast

import sqlalchemy as sa
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts.channel_delivery import (
    DELIVERY_LEASE_DEFAULT_SEC,
    DELIVERY_LEASE_MAX_SEC,
    DELIVERY_LEASE_MIN_SEC,
    DeliveryAckOutcome,
    DeliveryState,
    validate_delivery_result,
)
from muad_contracts.platform_settings import parse_platform_settings
from pydantic import JsonValue
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.db import SessionFactoryProvider
from ..infrastructure.db_clock import database_now
from ..infrastructure.models.runtime import ChannelNotification
from .channel_notifications import DELIVERY_NOT_REQUIRED, DELIVERY_PENDING, DELIVERY_STATES
from .ports import PlatformSettingsClient

#: `delivery_state` 词表的其余取值（与 0026 的 CHECK 同词表；一致性由验收断言）。
DELIVERY_DELIVERING = "DELIVERING"
DELIVERY_DELIVERED = "DELIVERED"
DELIVERY_UNKNOWN = "UNKNOWN"
#: 首次授租的 epoch（列默认 0；接管只前进不倒退）。
INITIAL_DELIVERY_EPOCH = 1
#: `NOT_SENT` 退避上限：指数增长但有界，一次失败不会把重试推到不可预期。
DELIVERY_BACKOFF_CAP_SEC = 3600
#: `attempt` 是 smallint：计数到顶即显式拒绝新租约，不靠数据库溢出报错。
DELIVERY_ATTEMPT_MAX = 32767
#: ack 结局 → 持久状态（只有实际发送成功才是 DELIVERED）。
_STATE_AFTER_ACK: dict[DeliveryAckOutcome, str] = {
    "DELIVERED": DELIVERY_DELIVERED,
    "NOT_SENT": DELIVERY_PENDING,
    "UNKNOWN": DELIVERY_UNKNOWN,
}


@dataclass(frozen=True, slots=True)
class DeliveryPolicy:
    """投递协议的时间边界（来源是 contracts 的 typed 平台设置，不在此重述默认值）。"""

    backoff_base_sec: int
    replay_retention_days: int

    def __post_init__(self) -> None:
        if self.backoff_base_sec < 1 or self.replay_retention_days < 1:
            raise ValueError("delivery policy bounds must be positive")


def delivery_policy_from_settings(settings: Mapping[str, object] | None) -> DeliveryPolicy:
    """平台设置快照 → 投递协议边界；缺省分组回落 schema 默认（唯一来源在 contracts）。"""
    parsed = parse_platform_settings(settings)
    return DeliveryPolicy(
        backoff_base_sec=parsed.task.delivery_backoff_base_sec,
        replay_retention_days=parsed.webchat.notification_retention_days,
    )


@dataclass(frozen=True, slots=True)
class DeliveryReceipt:
    """一次 ack 冻结的首次投递结果（重放与 UNKNOWN 核实读取同一份，不重写）。"""

    outcome: DeliveryAckOutcome
    attempt: int
    attempt_id: uuid.UUID
    lease_epoch: int
    acked_at: datetime
    result: dict[str, JsonValue] | None

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "outcome": self.outcome,
            "attempt": self.attempt,
            "attempt_id": str(self.attempt_id),
            "lease_epoch": self.lease_epoch,
            "acked_at": self.acked_at.isoformat(),
            "result": self.result,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, object]) -> DeliveryReceipt:
        """严格解析：坏载荷是内部事实损坏，显式失败，绝不静默当成"没有回执"。"""
        try:
            outcome, attempt = str(payload["outcome"]), int(str(payload["attempt"]))
            attempt_id = uuid.UUID(str(payload["attempt_id"]))
            lease_epoch = int(str(payload["lease_epoch"]))
            acked_at = datetime.fromisoformat(str(payload["acked_at"]))
            result = payload.get("result")
        except (KeyError, TypeError, ValueError) as exc:
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR) from exc
        if outcome not in _STATE_AFTER_ACK or acked_at.tzinfo is None:
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
        if result is not None and not isinstance(result, dict):
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
        return cls(
            outcome=outcome,
            attempt=attempt,
            attempt_id=attempt_id,
            lease_epoch=lease_epoch,
            acked_at=acked_at,
            result=result,
        )


@dataclass(frozen=True, slots=True)
class DeliveryLease:
    """reserve/renew 的结果：栅栏事实 + 行状态。

    `lease_until=None`（且 `lease_epoch=0`）表示**本次没有授出租约**——已送达重放、
    UNKNOWN 保留等；调用方不得据此发送任何内容。
    """

    notification_id: uuid.UUID
    delivery_key: str
    state: DeliveryState
    lease_epoch: int
    lease_until: datetime | None
    attempt: int
    reused: bool
    receipt: DeliveryReceipt | None = None


@dataclass(frozen=True, slots=True)
class DeliveryConfirmation:
    """ack 的结果：`reused=True` 表示本次回放了冻结的首次结果（幂等重试），状态未再推进。"""

    notification_id: uuid.UUID
    delivery_key: str
    state: DeliveryState
    attempt: int
    reused: bool
    receipt: DeliveryReceipt | None = None


class TenantChannelDelivery:
    """按租户的投递协议门面：每次操作取一次设置快照（HTTP 在**事务外**）再执行一次状态转换。

    Runtime 内的两个调用方共用它——API-18 端口（外部发送方：Gateway/Worker）与投递泵
    （本进程的兜底交付）：退避基数与回执保留期只有**一处来源**（平台设置），坏源显式失败，
    不回落默认值（design §3.7）。每个方法都只做"取快照 + 委托一次状态转换"。
    """

    def __init__(
        self, session_factory_provider: SessionFactoryProvider, settings_client: PlatformSettingsClient
    ) -> None:
        self._sessions = session_factory_provider
        self._settings = settings_client

    async def reserve(
        self,
        *,
        tenant_id: str,
        delivery_key: str,
        attempt_id: uuid.UUID,
        lease_sec: int | None = None,
    ) -> DeliveryLease:
        service = await self._service(tenant_id)
        return await service.reserve(
            tenant_id=tenant_id,
            delivery_key=delivery_key,
            attempt_id=attempt_id,
            lease_sec=lease_sec,
        )

    async def renew(
        self,
        *,
        tenant_id: str,
        notification_id: uuid.UUID,
        delivery_key: str,
        attempt_id: uuid.UUID,
        epoch: int,
        lease_sec: int | None = None,
    ) -> DeliveryLease:
        service = await self._service(tenant_id)
        return await service.renew(
            tenant_id=tenant_id,
            notification_id=notification_id,
            delivery_key=delivery_key,
            attempt_id=attempt_id,
            epoch=epoch,
            lease_sec=lease_sec,
        )

    async def ack(
        self,
        *,
        tenant_id: str,
        notification_id: uuid.UUID,
        delivery_key: str,
        attempt_id: uuid.UUID,
        epoch: int,
        outcome: DeliveryAckOutcome,
        result: Mapping[str, JsonValue] | None = None,
    ) -> DeliveryConfirmation:
        service = await self._service(tenant_id)
        return await service.ack(
            tenant_id=tenant_id,
            notification_id=notification_id,
            delivery_key=delivery_key,
            attempt_id=attempt_id,
            epoch=epoch,
            outcome=outcome,
            result=result,
        )

    async def _service(self, tenant_id: str) -> ChannelDeliveryService:
        """每次操作取一次设置快照（HTTP 在事务外）再构造服务：坏源显式失败，不回落默认。"""
        snapshot = await self._settings.fetch_snapshot(tenant_id=tenant_id)
        return ChannelDeliveryService(
            self._sessions, delivery_policy_from_settings(snapshot.settings)
        )


class ChannelDeliveryService:
    """出站投递的持久协议：每个方法一个短事务，行锁内只做状态判定与事实推进。"""

    def __init__(
        self, session_factory_provider: SessionFactoryProvider, policy: DeliveryPolicy
    ) -> None:
        self._sessions = session_factory_provider
        self._policy = policy

    async def reserve(
        self,
        *,
        tenant_id: str,
        delivery_key: str,
        attempt_id: uuid.UUID,
        lease_sec: int | None = None,
    ) -> DeliveryLease:
        """按稳定 `delivery_key` 预占：PENDING/过期租约 → DELIVERING；已送达重放首次结果。"""
        lease_seconds = _lease_seconds(lease_sec)
        async with self._sessions()() as session, session.begin():
            row = await _lock_by_key(session, tenant_id=tenant_id, delivery_key=delivery_key)
            if row is None:
                raise AppError(ErrorCode.COMMON_NOT_FOUND)
            now = await database_now(session)
            return await self._reserve_locked(
                session, row, now=now, attempt_id=attempt_id, lease_seconds=lease_seconds
            )

    async def renew(
        self,
        *,
        tenant_id: str,
        notification_id: uuid.UUID,
        delivery_key: str,
        attempt_id: uuid.UUID,
        epoch: int,
        lease_sec: int | None = None,
    ) -> DeliveryLease:
        """续租：核 attempt+epoch 且租约仍有效；期限只前进不倒退。"""
        lease_seconds = _lease_seconds(lease_sec)
        async with self._sessions()() as session, session.begin():
            row = await _lock_by_id(
                session,
                tenant_id=tenant_id,
                notification_id=notification_id,
                delivery_key=delivery_key,
            )
            now = await database_now(session)
            current = _require_live_lease(row, attempt_id=attempt_id, epoch=epoch, now=now)
            row.lease_until = max(current, now + timedelta(seconds=lease_seconds))
            row.update_time = now
            await session.flush()
            return _lease_view(row, reused=False)

    async def ack(
        self,
        *,
        tenant_id: str,
        notification_id: uuid.UUID,
        delivery_key: str,
        attempt_id: uuid.UUID,
        epoch: int,
        outcome: DeliveryAckOutcome,
        result: Mapping[str, JsonValue] | None = None,
    ) -> DeliveryConfirmation:
        """确认真实结局：只有 DELIVERED 是送达；重复 ack 回放首次结果，不重写。"""
        payload = _require_result(result)
        _require_outcome(outcome)
        async with self._sessions()() as session, session.begin():
            row = await _lock_by_id(
                session,
                tenant_id=tenant_id,
                notification_id=notification_id,
                delivery_key=delivery_key,
            )
            now = await database_now(session)
            stored = _stored_receipt(row)
            if _is_replay(stored, attempt_id=attempt_id, epoch=epoch):
                return _confirmation(row, stored, reused=True)
            _require_live_lease(row, attempt_id=attempt_id, epoch=epoch, now=now)
            receipt = DeliveryReceipt(
                outcome=outcome,
                attempt=int(row.attempt),
                attempt_id=attempt_id,
                lease_epoch=epoch,
                acked_at=now,
                result=payload,
            )
            row.delivery_result = receipt.to_json()
            row.delivery_state = _STATE_AFTER_ACK[outcome]
            row.lease_owner, row.lease_until = None, None
            if outcome == "NOT_SENT":
                row.next_attempt_at = now + timedelta(seconds=self._backoff_sec(int(row.attempt)))
            row.update_time = now
            await session.flush()
            return _confirmation(row, receipt, reused=False)

    async def _reserve_locked(
        self,
        session: AsyncSession,
        row: ChannelNotification,
        *,
        now: datetime,
        attempt_id: uuid.UUID,
        lease_seconds: int,
    ) -> DeliveryLease:
        """行锁内的状态判定（锁后取 PG 时钟，判定与写入同一口径）。"""
        receipt = _stored_receipt(row)
        if row.delivery_state == DELIVERY_NOT_REQUIRED:
            raise AppError(ErrorCode.COMMON_CONFLICT)
        if row.delivery_state == DELIVERY_DELIVERED:
            return _delivered_replay(
                row, receipt, now=now, retention_days=self._policy.replay_retention_days
            )
        if row.delivery_state == DELIVERY_UNKNOWN:
            return _no_lease_view(row, reused=True, receipt=receipt)
        if _lease_alive(row, now):
            if row.lease_owner == str(attempt_id):
                return _lease_view(row, reused=True)
            raise AppError(ErrorCode.DELIVERY_LEASE_LOST)
        return await _grant(
            session, row, now=now, attempt_id=attempt_id, lease_seconds=lease_seconds
        )

    def _backoff_sec(self, attempt: int) -> int:
        """`NOT_SENT` 退避：按尝试次数指数增长且有界（基数来自平台设置，唯一来源）。"""
        grown: int = self._policy.backoff_base_sec * 2 ** (attempt - 1)
        return min(grown, DELIVERY_BACKOFF_CAP_SEC)


def _lease_seconds(lease_sec: int | None) -> int:
    """发送方未指定时用协议默认；显式值必须在协议边界内（线契约之外再守一道）。"""
    seconds = DELIVERY_LEASE_DEFAULT_SEC if lease_sec is None else lease_sec
    if not DELIVERY_LEASE_MIN_SEC <= seconds <= DELIVERY_LEASE_MAX_SEC:
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
    return seconds


def _require_result(result: Mapping[str, JsonValue] | None) -> dict[str, JsonValue] | None:
    """ack 结果必须是严格 JSON 对象且在字节上限内；越界显式校验失败。"""
    try:
        return validate_delivery_result(result)
    except ValueError as exc:
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR) from exc


def _require_outcome(outcome: str) -> None:
    """结局词表由线契约/数据库 CHECK 定义：未知取值显式失败，不静默落库等约束报错。"""
    if outcome not in _STATE_AFTER_ACK:
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)


async def _lock_by_key(
    session: AsyncSession, *, tenant_id: str, delivery_key: str
) -> ChannelNotification | None:
    """按稳定投递键取行并加锁；租户谓词在 WHERE 内（跨租户行不可见）。"""
    row: ChannelNotification | None = await session.scalar(
        sa.select(ChannelNotification)
        .where(
            ChannelNotification.tenant_id == tenant_id,
            ChannelNotification.delivery_key == delivery_key,
        )
        .with_for_update()
    )
    return row


async def _lock_by_id(
    session: AsyncSession, *, tenant_id: str, notification_id: uuid.UUID, delivery_key: str
) -> ChannelNotification:
    """按通知 id 取行并加锁，并用 delivery_key 复核身份；不符一律 NOT_FOUND（不泄露存在性）。"""
    row = await session.scalar(
        sa.select(ChannelNotification)
        .where(
            ChannelNotification.tenant_id == tenant_id,
            ChannelNotification.id == notification_id,
        )
        .with_for_update()
    )
    if row is None or row.delivery_key != delivery_key:
        raise AppError(ErrorCode.COMMON_NOT_FOUND)
    return row


def _lease_alive(row: ChannelNotification, now: datetime) -> bool:
    return (
        row.delivery_state == DELIVERY_DELIVERING
        and row.lease_until is not None
        and row.lease_until > now
    )


def _require_live_lease(
    row: ChannelNotification, *, attempt_id: uuid.UUID, epoch: int, now: datetime
) -> datetime:
    """失约即停手：非 DELIVERING、attempt/epoch 不符或租约已过期一律 `DELIVERY_LEASE_LOST`。"""
    if row.delivery_state != DELIVERY_DELIVERING or row.lease_owner != str(attempt_id):
        raise AppError(ErrorCode.DELIVERY_LEASE_LOST)
    if int(row.lease_epoch) != epoch:
        raise AppError(ErrorCode.DELIVERY_LEASE_LOST)
    lease_until = row.lease_until
    if lease_until is None or lease_until <= now:
        raise AppError(ErrorCode.DELIVERY_LEASE_LOST)
    return lease_until


async def _grant(
    session: AsyncSession,
    row: ChannelNotification,
    *,
    now: datetime,
    attempt_id: uuid.UUID,
    lease_seconds: int,
) -> DeliveryLease:
    """发新租约（首次或接管过期租约）：epoch 单调前进、attempt 计数、期限有界。"""
    attempt = int(row.attempt) + 1
    if attempt > DELIVERY_ATTEMPT_MAX:
        raise AppError(ErrorCode.COMMON_CONFLICT)
    row.delivery_state = DELIVERY_DELIVERING
    row.attempt = attempt
    row.lease_owner = str(attempt_id)
    row.lease_epoch = int(row.lease_epoch) + 1
    row.lease_until = now + timedelta(seconds=lease_seconds)
    row.update_time = now
    await session.flush()
    return _lease_view(row, reused=False)


def _delivered_replay(
    row: ChannelNotification,
    receipt: DeliveryReceipt | None,
    *,
    now: datetime,
    retention_days: int,
) -> DeliveryLease:
    """已送达重放：DELIVERED 行必有首次结果；超过重放保留期显式过期，不当新投递。"""
    if receipt is None:
        raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
    if now - receipt.acked_at > timedelta(days=retention_days):
        raise AppError(ErrorCode.DELIVERY_RECEIPT_EXPIRED)
    return _no_lease_view(row, reused=True, receipt=receipt)


def _stored_receipt(row: ChannelNotification) -> DeliveryReceipt | None:
    payload = row.delivery_result
    return None if payload is None else DeliveryReceipt.from_json(payload)


def _is_replay(receipt: DeliveryReceipt | None, *, attempt_id: uuid.UUID, epoch: int) -> bool:
    """ack 幂等：同一 attempt+epoch 的重复确认回放冻结的首次结果。"""
    return receipt is not None and receipt.attempt_id == attempt_id and receipt.lease_epoch == epoch


def _state(value: str) -> DeliveryState:
    """行状态投影成线协议枚举；越界即内部事实损坏，不静默透传。"""
    if value not in DELIVERY_STATES:
        raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
    return cast(DeliveryState, value)


def _lease_view(row: ChannelNotification, *, reused: bool) -> DeliveryLease:
    return DeliveryLease(
        notification_id=row.id,
        delivery_key=row.delivery_key,
        state=_state(row.delivery_state),
        lease_epoch=int(row.lease_epoch),
        lease_until=row.lease_until,
        attempt=int(row.attempt),
        reused=reused,
    )


def _no_lease_view(
    row: ChannelNotification, *, reused: bool, receipt: DeliveryReceipt | None
) -> DeliveryLease:
    """没有授出租约的视图：`lease_epoch=0`/`lease_until=None`（调用方不得发送）。"""
    return DeliveryLease(
        notification_id=row.id,
        delivery_key=row.delivery_key,
        state=_state(row.delivery_state),
        lease_epoch=0,
        lease_until=None,
        attempt=int(row.attempt),
        reused=reused,
        receipt=receipt,
    )


def _confirmation(
    row: ChannelNotification, receipt: DeliveryReceipt | None, *, reused: bool
) -> DeliveryConfirmation:
    return DeliveryConfirmation(
        notification_id=row.id,
        delivery_key=row.delivery_key,
        state=_state(row.delivery_state),
        attempt=int(row.attempt),
        reused=reused,
        receipt=receipt,
    )
