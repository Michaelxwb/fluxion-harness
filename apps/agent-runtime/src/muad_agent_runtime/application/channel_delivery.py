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

状态机本身在 `delivery_lease.DeliveryLeaseProtocol`：本模块只负责行锁、列映射与 PG 时钟，
`message_delivery` 复用同一套决策，不抄第二份。
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import cast

import sqlalchemy as sa
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts.channel_delivery import DeliveryAckOutcome, DeliveryState
from muad_contracts.platform_settings import parse_platform_settings
from pydantic import JsonValue
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.db import SessionFactoryProvider
from ..infrastructure.db_clock import database_now
from ..infrastructure.models.runtime import ChannelNotification
from .channel_notifications import DELIVERY_NOT_REQUIRED, DELIVERY_PENDING, DELIVERY_STATES
from .delivery_lease import (
    DeliveryLeaseProtocol,
    DeliveryPolicy,
    DeliveryReceipt,
    DeliveryStates,
    LeaseGrant,
    LeaseSnapshot,
)
from .ports import PlatformSettingsClient

__all__ = [
    "DELIVERY_BACKOFF_CAP_SEC",
    "DELIVERY_DELIVERED",
    "DELIVERY_DELIVERING",
    "DELIVERY_UNKNOWN",
    "ChannelDeliveryService",
    "DeliveryConfirmation",
    "DeliveryLease",
    "DeliveryPolicy",
    "DeliveryReceipt",
    "TenantChannelDelivery",
    "delivery_policy_from_settings",
]

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

#: `channel_notification` 的回执语义：没有 DEGRADED/FAILED（那是 message_delivery 的词表）。
_CHANNEL_STATES = DeliveryStates(
    pending=DELIVERY_PENDING,
    delivering=DELIVERY_DELIVERING,
    delivered=DELIVERY_DELIVERED,
    unknown=DELIVERY_UNKNOWN,
    not_required=DELIVERY_NOT_REQUIRED,
    ack_states={
        "DELIVERED": DELIVERY_DELIVERED,
        "NOT_SENT": DELIVERY_PENDING,
        "UNKNOWN": DELIVERY_UNKNOWN,
    },
    replay_states=frozenset({DELIVERY_DELIVERED}),
    no_lease_states=frozenset({DELIVERY_UNKNOWN}),
)


def delivery_policy_from_settings(
    settings: Mapping[str, object] | None,
) -> DeliveryPolicy:
    """平台设置快照 → 投递协议边界；缺省分组回落 schema 默认（唯一来源在 contracts）。"""
    parsed = parse_platform_settings(settings)
    return DeliveryPolicy(
        backoff_base_sec=parsed.task.delivery_backoff_base_sec,
        replay_retention_days=parsed.webchat.notification_retention_days,
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
        self,
        session_factory_provider: SessionFactoryProvider,
        settings_client: PlatformSettingsClient,
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
        self,
        session_factory_provider: SessionFactoryProvider,
        policy: DeliveryPolicy,
    ) -> None:
        self._sessions = session_factory_provider
        self._protocol = DeliveryLeaseProtocol(policy, _CHANNEL_STATES)

    async def reserve(
        self,
        *,
        tenant_id: str,
        delivery_key: str,
        attempt_id: uuid.UUID,
        lease_sec: int | None = None,
    ) -> DeliveryLease:
        """按稳定 `delivery_key` 预占：PENDING/过期租约 → DELIVERING；已送达重放首次结果。"""
        lease_seconds = self._protocol.lease_seconds(lease_sec)
        async with self._sessions()() as session, session.begin():
            row = await _lock_by_key(
                session, tenant_id=tenant_id, delivery_key=delivery_key
            )
            if row is None:
                raise AppError(ErrorCode.COMMON_NOT_FOUND)
            now = await database_now(session)
            decision = self._protocol.reserve(
                _snapshot(row),
                now=now,
                attempt_id=attempt_id,
                lease_seconds=lease_seconds,
            )
            if decision.grant is not None and decision.action == "grant":
                _apply_grant(row, decision.grant, now=now, attempt_id=attempt_id)
                await session.flush()
                return _lease_view(row, reused=False)
            if decision.action == "reuse" and decision.grant is not None:
                return _lease_view(row, reused=True)
            return _no_lease_view(row, reused=True, receipt=decision.receipt)

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
        lease_seconds = self._protocol.lease_seconds(lease_sec)
        async with self._sessions()() as session, session.begin():
            row = await _lock_by_id(
                session,
                tenant_id=tenant_id,
                notification_id=notification_id,
                delivery_key=delivery_key,
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
        notification_id: uuid.UUID,
        delivery_key: str,
        attempt_id: uuid.UUID,
        epoch: int,
        outcome: DeliveryAckOutcome,
        result: Mapping[str, JsonValue] | None = None,
    ) -> DeliveryConfirmation:
        """确认真实结局：只有 DELIVERED 是送达；重复 ack 回放首次结果，不重写。"""
        async with self._sessions()() as session, session.begin():
            row = await _lock_by_id(
                session,
                tenant_id=tenant_id,
                notification_id=notification_id,
                delivery_key=delivery_key,
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
            if not decision.reused:
                receipt = decision.receipt
                if receipt is None:
                    raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
                row.delivery_result = receipt.to_json()
                row.delivery_state = decision.state
                row.lease_owner, row.lease_until = None, None
                if decision.next_attempt_at is not None:
                    row.next_attempt_at = decision.next_attempt_at
                row.update_time = now
                await session.flush()
            return _confirmation(row, decision.receipt, reused=decision.reused)


def _snapshot(row: ChannelNotification) -> LeaseSnapshot:
    return LeaseSnapshot(
        state=row.delivery_state,
        attempt=int(row.attempt),
        lease_owner=row.lease_owner,
        lease_epoch=int(row.lease_epoch),
        lease_until=row.lease_until,
        receipt=_stored_receipt(row),
    )


def _apply_grant(
    row: ChannelNotification,
    grant: LeaseGrant,
    *,
    now: datetime,
    attempt_id: uuid.UUID,
) -> None:
    row.delivery_state = grant.state
    row.attempt = grant.attempt
    row.lease_owner = str(attempt_id)
    row.lease_epoch = grant.lease_epoch
    row.lease_until = grant.lease_until
    row.update_time = now


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
    session: AsyncSession,
    *,
    tenant_id: str,
    notification_id: uuid.UUID,
    delivery_key: str,
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


def _stored_receipt(row: ChannelNotification) -> DeliveryReceipt | None:
    payload = row.delivery_result
    return None if payload is None else DeliveryReceipt.from_json(payload)


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
