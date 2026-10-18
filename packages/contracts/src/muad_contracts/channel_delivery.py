"""API-18 出站投递的线契约：稳定 `delivery_key` + 发送方 attempt + 租约 epoch 的栅栏事实。

Runtime 是投递事实的 Owner（`runtime.channel_notification` 一行）；本模块只描述受信发送方
（Runtime 投递泵、Gateway 活跃回复收尾/兜底交付、Worker 结果投递）与它之间的线协议。
持久状态转换与 PG 时钟裁决留在 Runtime 应用层（`application/channel_delivery.py`）。

三个身份是**同一件事的三半**，缺一不可：

- `delivery_key`：跨进程稳定的投递身份（`run:{run_id}:terminal`、`task:{task_id}:final` …），
  重投/重放都命中同一回执；
- `attempt_id`：本次发送方尝试的身份（续租/确认必须由同一个尝试提交）；
- `epoch`：租约代次，只有**当前**租约持有者能确认（被接管/过期的旧尝试一律停手）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    AwareDatetime,
    ConfigDict,
    Field,
    JsonValue,
    StrictStr,
    field_validator,
)

from .canonical import canonical_json, ensure_strict_json
from .tasks import ContractModel

#: 租约时长的线协议边界：发送方按自己的 HTTP 预算选择，协议只保证**有界**。
DELIVERY_LEASE_MIN_SEC = 5
DELIVERY_LEASE_MAX_SEC = 3600
DELIVERY_LEASE_DEFAULT_SEC = 60
#: ack 结果 JSON 的字节上限：回执会被重放，不能成为无界载荷。
DELIVERY_RESULT_MAX_BYTES = 4096

#: `delivery_state` 词表（持久事实的数据库 CHECK 是权威，这里只是线协议投影）。
DeliveryState = Literal["PENDING", "DELIVERING", "DELIVERED", "UNKNOWN", "NOT_REQUIRED"]
#: 只有 `DELIVERED` 是"实际发出去"；`NOT_SENT` 是"确定没发出去"（可重试）；
#: `UNKNOWN` 是"没拿到结论"（保留核实：不自动重发、不标已送达）。
DeliveryAckOutcome = Literal["DELIVERED", "NOT_SENT", "UNKNOWN"]


def _delivery_key(value: str) -> str:
    if not value.strip():
        raise ValueError("delivery_key must not be blank")
    return value


DeliveryKey = Annotated[StrictStr, Field(min_length=1, max_length=128), AfterValidator(_delivery_key)]
LeaseSeconds = Annotated[
    int, Field(strict=True, ge=DELIVERY_LEASE_MIN_SEC, le=DELIVERY_LEASE_MAX_SEC)
]
DeliveryEpoch = Annotated[int, Field(strict=True, ge=0, le=(1 << 63) - 1)]
DeliveryAttempt = Annotated[int, Field(strict=True, ge=0, le=32767)]


def validate_delivery_result(
    result: Mapping[str, JsonValue] | None,
) -> dict[str, JsonValue] | None:
    """ack 结果必须是**严格 JSON 对象**且在字节上限内。

    `None` 是合法表达（"只有结局，没有细节"）；非对象、`NaN`/`Infinity`、未知类型或超长
    载荷一律显式失败——回执会随重放回到调用方，不能把不可序列化的东西写进 jsonb 再让它
    在读取时炸掉。
    """
    if result is None:
        return None
    payload = dict(ensure_strict_json(result))
    if len(canonical_json(payload).encode("utf-8")) > DELIVERY_RESULT_MAX_BYTES:
        raise ValueError("delivery result exceeds the receipt bound")
    return payload


class ChannelDeliveryContract(ContractModel):
    model_config = ConfigDict(frozen=True, hide_input_in_errors=True, allow_inf_nan=False)


class DeliveryReservationRequest(ChannelDeliveryContract):
    """API-18 预占：稳定投递键 + 本次尝试；租期由发送方在自己的预算内选择。"""

    delivery_key: DeliveryKey
    attempt_id: UUID
    lease_sec: LeaseSeconds = DELIVERY_LEASE_DEFAULT_SEC


class DeliveryLeaseRenewal(ChannelDeliveryContract):
    """API-18 续租：核 epoch/attempt；期限只前进不倒退。"""

    delivery_key: DeliveryKey
    attempt_id: UUID
    epoch: DeliveryEpoch
    lease_sec: LeaseSeconds = DELIVERY_LEASE_DEFAULT_SEC


class DeliveryAck(ChannelDeliveryContract):
    """API-18 确认：`outcome` 是真实发送结局，`result` 是首次投递结果（可选）。"""

    delivery_key: DeliveryKey
    attempt_id: UUID
    epoch: DeliveryEpoch
    outcome: DeliveryAckOutcome
    result: dict[str, JsonValue] | None = None

    @field_validator("result", mode="before")
    @classmethod
    def _strict_result(cls, value: object) -> dict[str, JsonValue] | None:
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise ValueError("delivery result must be a JSON object")
        return validate_delivery_result(value)


class DeliveryReceiptPayload(ChannelDeliveryContract):
    """首次投递结果：ack 时冻结，重放/UNKNOWN 核实读取同一份，不重写。"""

    outcome: DeliveryAckOutcome
    attempt: DeliveryAttempt
    attempt_id: UUID
    lease_epoch: DeliveryEpoch
    acked_at: AwareDatetime
    result: dict[str, JsonValue] | None = None


class DeliveryLeaseGrant(ChannelDeliveryContract):
    """reserve/renew 的栅栏事实。

    `lease_epoch=0`/`lease_until=null` 表示**本次没有授出租约**（已送达重放、UNKNOWN 保留、
    NOT_REQUIRED 等）——存在占位不等于成功：只有 `receipt.outcome=DELIVERED` 才是送达事实。
    """

    notification_id: UUID
    delivery_key: str
    state: DeliveryState
    lease_epoch: DeliveryEpoch
    lease_until: AwareDatetime | None
    attempt: DeliveryAttempt
    reused: bool
    receipt: DeliveryReceiptPayload | None = None


class DeliveryAckReceipt(ChannelDeliveryContract):
    """ack 结果：`reused=true` 表示本次回放冻结的首次结果（幂等重试），未再次推进状态。"""

    notification_id: UUID
    delivery_key: str
    state: DeliveryState
    attempt: DeliveryAttempt
    reused: bool
    receipt: DeliveryReceiptPayload | None = None
