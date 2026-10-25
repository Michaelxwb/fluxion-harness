"""统一 IM 入站/出站契约（TASK-006；design §3.4.2）。

只描述**渠道中立**的事实：渠道私有取件/回复协议留在 Connector adapter，核心域看不到。
所有模型 `extra="forbid"`；route/shard key 由显式字段稳定派生，禁止随机或进程内状态。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import Field, field_validator

from .canonical import ensure_strict_json
from .tasks import ContractModel

__all__ = [
    "ChannelCapabilities",
    "ConnectorLease",
    "DeliveryCommand",
    "DeliveryKind",
    "InboundEvent",
    "inbound_stream_key",
    "lease_key",
    "outbound_stream_key",
    "route_key",
    "shard_for",
]


class DeliveryKind(StrEnum):
    """出站命令的公开语义（adapter 决定具体协议投递方式）。"""

    TEXT = "TEXT"
    DELTA = "DELTA"
    TERMINAL = "TERMINAL"


class InboundEvent(ContractModel):
    """Connector → 统一核心的入站事实（已规范化，不含渠道取件凭据）。"""

    event_id: UUID
    channel: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    bot_id: str = Field(min_length=1)
    external_message_id: str = Field(min_length=1)
    external_conversation_id: str | None = None
    external_user_id: str = Field(min_length=1)
    text: str = ""
    attachment_keys: tuple[str, ...] = ()
    reply_ref: str | None = None
    received_at: datetime
    trace_id: str = ""


class DeliveryCommand(ContractModel):
    """统一核心 → Connector 的出站命令（幂等 identity + fencing epoch）。"""

    command_id: UUID
    channel: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    bot_id: str = Field(min_length=1)
    run_id: UUID
    external_conversation_id: str | None = None
    external_user_id: str = Field(min_length=1)
    kind: DeliveryKind
    text: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    reply_ref: str | None = None
    epoch: int = Field(ge=0)
    created_at: datetime

    @field_validator("payload", mode="before")
    @classmethod
    def _strict_payload(cls, value: dict[str, Any]) -> dict[str, Any]:
        return ensure_strict_json(value)


class ChannelCapabilities(ContractModel):
    """一个通道/租户组合的能力清单；路由与 UI 只依赖这里，不猜渠道名。"""

    channel: str = Field(min_length=1)
    text: bool = True
    streaming: bool = False
    media: bool = False
    max_text_bytes: int = Field(gt=0)
    features: tuple[str, ...] = ()


class ConnectorLease(ContractModel):
    """Connector 对 bot 传输所有权的 fencing 事实。"""

    channel: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    bot_id: str = Field(min_length=1)
    owner_id: str = Field(min_length=1)
    epoch: int = Field(ge=1)
    expires_at: datetime


def _canonical(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def route_key(
    *,
    channel: str,
    tenant_id: str,
    bot_id: str,
    external_conversation_id: str | None,
    external_user_id: str,
) -> str:
    """稳定路由键：同输入跨进程/重启一致，不含随机数或时间。"""
    return _canonical(
        {
            "bot_id": bot_id,
            "channel": channel,
            "external_conversation_id": external_conversation_id or "",
            "external_user_id": external_user_id,
            "tenant_id": tenant_id,
        }
    )


def shard_for(key: str, shards: int) -> int:
    """稳定分片：sha256(key) 对 shards 取模；shards 必须为正整数。"""
    if shards < 1:
        raise ValueError("shards must be positive")
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % shards


def inbound_stream_key(event: InboundEvent, *, shards: int = 32) -> str:
    key = route_key(
        channel=event.channel,
        tenant_id=event.tenant_id,
        bot_id=event.bot_id,
        external_conversation_id=event.external_conversation_id,
        external_user_id=event.external_user_id,
    )
    return f"im.inbound:{shard_for(key, shards)}"


def outbound_stream_key(command: DeliveryCommand, *, shards: int = 32) -> str:
    key = route_key(
        channel=command.channel,
        tenant_id=command.tenant_id,
        bot_id=command.bot_id,
        external_conversation_id=command.external_conversation_id,
        external_user_id=command.external_user_id,
    )
    return f"im.outbound:{shard_for(key, shards)}"


def lease_key(lease: ConnectorLease) -> str:
    return f"connector:bot:{lease.tenant_id}:{lease.bot_id}"
