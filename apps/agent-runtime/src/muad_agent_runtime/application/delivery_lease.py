"""通用出站投递租约状态机（design §4.5）。

`channel_notification` 与 `message_delivery` 是两张不同的表，但 reserve/renew/ack 的
**决策语义完全相同**：稳定键、attempt 身份、单调 epoch、有界租约、失约即停手、
ack 幂等、`NOT_SENT` 退避、`UNKNOWN` 保留核实、已送达/已降级重放首次回执。

本模块只做**纯决策**（不碰 Session）：两边的服务把行读成 `LeaseSnapshot`，把
`LeaseGrant`/`AckDecision` 写回自己的列。这样新增第二张投递表时不会抄出第二套状态机。
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts.channel_delivery import (
    DELIVERY_LEASE_DEFAULT_SEC,
    DELIVERY_LEASE_MAX_SEC,
    DELIVERY_LEASE_MIN_SEC,
    DeliveryAckOutcome,
    validate_delivery_result,
)
from pydantic import JsonValue

#: 首次授租的 epoch（列默认 0；接管只前进不倒退）。
INITIAL_DELIVERY_EPOCH = 1
#: `NOT_SENT` 退避上限：指数增长但有界，一次失败不会把重试推到不可预期。
DELIVERY_BACKOFF_CAP_SEC = 3600
#: `attempt` 是 smallint：计数到顶即显式拒绝新租约，不靠数据库溢出报错。
DELIVERY_ATTEMPT_MAX = 32767

#: 回执词表（持久 CHECK 因表而异，但回执 JSON 只认这些取值）。
DELIVERY_ACK_OUTCOMES = frozenset(
    {"DELIVERED", "NOT_SENT", "UNKNOWN", "DEGRADED", "FAILED"}
)


@dataclass(frozen=True, slots=True)
class DeliveryPolicy:
    """投递协议的时间边界（来源是 contracts 的 typed 平台设置，不在此重述默认值）。"""

    backoff_base_sec: int
    replay_retention_days: int

    def __post_init__(self) -> None:
        if self.backoff_base_sec < 1 or self.replay_retention_days < 1:
            raise ValueError("delivery policy bounds must be positive")


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
        if outcome not in DELIVERY_ACK_OUTCOMES or acked_at.tzinfo is None:
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
        if result is not None and not isinstance(result, dict):
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
        return cls(
            outcome=outcome,  # type: ignore[arg-type]
            attempt=attempt,
            attempt_id=attempt_id,
            lease_epoch=lease_epoch,
            acked_at=acked_at,
            result=result,
        )


@dataclass(frozen=True, slots=True)
class DeliveryStates:
    """一张投递表的词表与回执语义（两张表只在"哪些结局存在"上不同）。"""

    pending: str
    delivering: str
    delivered: str
    unknown: str
    not_required: str | None
    ack_states: Mapping[str, str]
    replay_states: frozenset[str]
    no_lease_states: frozenset[str]


@dataclass(frozen=True, slots=True)
class LeaseSnapshot:
    """状态机看到的行事实（服务层从任意 ORM 行投影出来）。"""

    state: str
    attempt: int
    lease_owner: str | None
    lease_epoch: int
    lease_until: datetime | None
    receipt: DeliveryReceipt | None


@dataclass(frozen=True, slots=True)
class LeaseGrant:
    """一次授租/续租要写回的行事实。"""

    state: str
    attempt: int
    lease_epoch: int
    lease_until: datetime
    reused: bool = False


@dataclass(frozen=True, slots=True)
class ReserveDecision:
    """reserve 的结论：授租、复用、重放或"没有租约可发"。"""

    action: Literal["grant", "reuse", "replay", "no_lease"]
    grant: LeaseGrant | None = None
    receipt: DeliveryReceipt | None = None
    reused: bool = False


@dataclass(frozen=True, slots=True)
class AckDecision:
    """ack 的结论：要写回的状态 + 冻结回执；`reused=True` 表示回放首次结果。"""

    state: str
    receipt: DeliveryReceipt | None
    reused: bool
    next_attempt_at: datetime | None = None


class DeliveryLeaseProtocol:
    """无持久化的租约状态机；事务、行锁与列名留在具体服务里。"""

    def __init__(self, policy: DeliveryPolicy, states: DeliveryStates) -> None:
        self._policy = policy
        self._states = states

    @property
    def states(self) -> DeliveryStates:
        return self._states

    def lease_seconds(self, lease_sec: int | None) -> int:
        """发送方未指定时用协议默认；显式值必须在协议边界内（线契约之外再守一道）。"""
        seconds = DELIVERY_LEASE_DEFAULT_SEC if lease_sec is None else lease_sec
        if not DELIVERY_LEASE_MIN_SEC <= seconds <= DELIVERY_LEASE_MAX_SEC:
            raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
        return seconds

    def reserve(
        self,
        snapshot: LeaseSnapshot,
        *,
        now: datetime,
        attempt_id: uuid.UUID,
        lease_seconds: int,
    ) -> ReserveDecision:
        """PENDING/过期租约 → DELIVERING；已送达/已降级重放；UNKNOWN/FAILED 保留核实。"""
        states = self._states
        if states.not_required is not None and snapshot.state == states.not_required:
            raise AppError(ErrorCode.COMMON_CONFLICT)
        if snapshot.state in states.replay_states:
            receipt = self._require_receipt(snapshot)
            if now - receipt.acked_at > timedelta(days=self._policy.replay_retention_days):
                raise AppError(ErrorCode.DELIVERY_RECEIPT_EXPIRED)
            return ReserveDecision(action="replay", receipt=receipt, reused=True)
        if snapshot.state in states.no_lease_states:
            return ReserveDecision(
                action="no_lease", receipt=snapshot.receipt, reused=True
            )
        if _lease_alive(snapshot, now):
            if snapshot.lease_owner == str(attempt_id):
                return ReserveDecision(
                    action="reuse",
                    grant=LeaseGrant(
                        state=snapshot.state,
                        attempt=snapshot.attempt,
                        lease_epoch=snapshot.lease_epoch,
                        lease_until=_lease_until(snapshot),
                        reused=True,
                    ),
                    reused=True,
                )
            raise AppError(ErrorCode.DELIVERY_LEASE_LOST)
        attempt = snapshot.attempt + 1
        if attempt > DELIVERY_ATTEMPT_MAX:
            raise AppError(ErrorCode.COMMON_CONFLICT)
        return ReserveDecision(
            action="grant",
            grant=LeaseGrant(
                state=states.delivering,
                attempt=attempt,
                lease_epoch=snapshot.lease_epoch + INITIAL_DELIVERY_EPOCH,
                lease_until=now + timedelta(seconds=lease_seconds),
            ),
        )

    def renew(
        self,
        snapshot: LeaseSnapshot,
        *,
        now: datetime,
        attempt_id: uuid.UUID,
        epoch: int,
        lease_seconds: int,
    ) -> LeaseGrant:
        """续租：核 attempt+epoch 且租约仍有效；期限只前进不倒退。"""
        current = self.require_live_lease(
            snapshot, attempt_id=attempt_id, epoch=epoch, now=now
        )
        return LeaseGrant(
            state=snapshot.state,
            attempt=snapshot.attempt,
            lease_epoch=snapshot.lease_epoch,
            lease_until=max(current, now + timedelta(seconds=lease_seconds)),
        )

    def ack(
        self,
        snapshot: LeaseSnapshot,
        *,
        now: datetime,
        attempt_id: uuid.UUID,
        epoch: int,
        outcome: str,
        result: Mapping[str, JsonValue] | None,
    ) -> AckDecision:
        """确认真实结局：重复 ack 回放首次结果，不重写；失约旧回执不得落库。"""
        payload = _require_result(result)
        state = self._states.ack_states.get(outcome)
        if state is None:
            raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
        stored = snapshot.receipt
        if _is_replay(stored, attempt_id=attempt_id, epoch=epoch):
            return AckDecision(state=snapshot.state, receipt=stored, reused=True)
        self.require_live_lease(snapshot, attempt_id=attempt_id, epoch=epoch, now=now)
        receipt = DeliveryReceipt(
            outcome=outcome,  # type: ignore[arg-type]
            attempt=snapshot.attempt,
            attempt_id=attempt_id,
            lease_epoch=epoch,
            acked_at=now,
            result=payload,
        )
        next_attempt_at = (
            now + timedelta(seconds=self._backoff_sec(snapshot.attempt))
            if state == self._states.pending
            else None
        )
        return AckDecision(
            state=state,
            receipt=receipt,
            reused=False,
            next_attempt_at=next_attempt_at,
        )

    def require_live_lease(
        self,
        snapshot: LeaseSnapshot,
        *,
        attempt_id: uuid.UUID,
        epoch: int,
        now: datetime,
    ) -> datetime:
        """失约即停手：非 DELIVERING、attempt/epoch 不符或租约过期一律 LEASE_LOST。"""
        if snapshot.state != self._states.delivering:
            raise AppError(ErrorCode.DELIVERY_LEASE_LOST)
        if snapshot.lease_owner != str(attempt_id):
            raise AppError(ErrorCode.DELIVERY_LEASE_LOST)
        if snapshot.lease_epoch != epoch:
            raise AppError(ErrorCode.DELIVERY_LEASE_LOST)
        lease_until = snapshot.lease_until
        if lease_until is None or lease_until <= now:
            raise AppError(ErrorCode.DELIVERY_LEASE_LOST)
        return lease_until

    def _require_receipt(self, snapshot: LeaseSnapshot) -> DeliveryReceipt:
        if snapshot.receipt is None:
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
        return snapshot.receipt

    def _backoff_sec(self, attempt: int) -> int:
        """`NOT_SENT` 退避：按尝试次数指数增长且有界（基数来自平台设置，唯一来源）。"""
        grown: int = self._policy.backoff_base_sec * 2 ** (max(attempt, 1) - 1)
        return min(grown, DELIVERY_BACKOFF_CAP_SEC)


def _lease_alive(snapshot: LeaseSnapshot, now: datetime) -> bool:
    return (
        snapshot.state == "DELIVERING"
        and snapshot.lease_until is not None
        and snapshot.lease_until > now
    )


def _lease_until(snapshot: LeaseSnapshot) -> datetime:
    if snapshot.lease_until is None:
        raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
    return snapshot.lease_until


def _require_result(
    result: Mapping[str, JsonValue] | None,
) -> dict[str, JsonValue] | None:
    """ack 结果必须是严格 JSON 对象且在字节上限内；越界显式校验失败。"""
    try:
        return validate_delivery_result(result)
    except ValueError as exc:
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR) from exc


def _is_replay(
    receipt: DeliveryReceipt | None, *, attempt_id: uuid.UUID, epoch: int
) -> bool:
    """ack 幂等：同一 attempt+epoch 的重复确认回放冻结的首次结果。"""
    return (
        receipt is not None
        and receipt.attempt_id == attempt_id
        and receipt.lease_epoch == epoch
    )
