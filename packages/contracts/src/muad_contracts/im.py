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
from .channel import AttachmentRef
from .tasks import ContractModel

__all__ = [
    "ChannelCapabilities",
    "ConnectorLease",
    "DeliveryCommand",
    "DeliveryKind",
    "InboundEvent",
    "command_result_key",
    "inbound_dedupe_key",
    "inbound_stream_key",
    "lease_epoch_key",
    "lease_key",
    "outbound_cursor_key",
    "outbound_stream_key",
    "route_key",
    "shard_for",
]


class DeliveryKind(StrEnum):
    """出站命令的公开语义（adapter 决定具体协议投递方式）。"""

    TEXT = "TEXT"
    DELTA = "DELTA"
    TERMINAL = "TERMINAL"
    ARTIFACT = "ARTIFACT"


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
    #: 已由 Connector 落共享存储的附件（渠道私有取件凭据不出 Connector）；空表示无附件。
    attachments: tuple[AttachmentRef, ...] = ()
    #: 未取件附件的不透明计数键：Gateway 可据此知道"来过几个媒体项"，键本身不含 URL/密钥。
    attachment_keys: tuple[str, ...] = ()
    unsupported_media: str | None = None
    reply_ref: str | None = None
    received_at: datetime
    trace_id: str = ""


class DeliveryCommand(ContractModel):
    """统一核心 → Connector 的出站命令（幂等 identity + fencing epoch）。"""

    command_id: UUID
    channel: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    bot_id: str = Field(min_length=1)
    run_id: UUID | None = None
    external_conversation_id: str | None = None
    external_user_id: str = Field(min_length=1)
    kind: DeliveryKind
    text: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    #: 产物命令必填：与文本同样从受信解析结果构造，不含渠道取件凭据。
    artifact: AttachmentRef | None = None
    #: 会话内回复目标；epoch/过期不匹配时 Connector 必须降级主动发送并回报 DEGRADED。
    reply_ref: str | None = None
    stream_id: str | None = None
    epoch: int = Field(default=0, ge=0)
    created_at: datetime

    @field_validator("artifact")
    @classmethod
    def _artifact_required_for_artifact_kind(
        cls, value: AttachmentRef | None, info: Any
    ) -> AttachmentRef | None:
        if info.data.get("kind") == DeliveryKind.ARTIFACT and value is None:
            raise ValueError("artifact is required when kind=ARTIFACT")
        return value

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


def outbound_stream_key(command: DeliveryCommand) -> str:
    """按 **bot** 分流出站命令。

    不能用全局 shard：bot lease 是逐 bot 的，两个 Connector 可能各自持有同一 shard 里的不同
    bot；只有"每个 bot 一条 stream + 只读自己拥有的 bot"才能保证命令不会被非 owner 抢走。
    """
    return f"im.outbound:{command.tenant_id}:{command.bot_id}"


def command_result_key(command_id: UUID | str) -> str:
    return f"im:outbound:result:{command_id}"


def outbound_cursor_key(tenant_id: str, bot_id: str) -> str:
    return f"connector:outbound-cursor:{tenant_id}:{bot_id}"


def inbound_dedupe_key(tenant_id: str, bot_id: str, message_id: str) -> str:
    return f"connector:inbound:{tenant_id}:{bot_id}:{message_id}"


def lease_key(lease: ConnectorLease) -> str:
    return f"connector:bot:{lease.tenant_id}:{lease.bot_id}"


def lease_epoch_key(tenant_id: str, bot_id: str) -> str:
    """lease epoch 计数器（不随 lease 过期删除）：保证 fencing token 单调。"""
    return f"connector:bot:{tenant_id}:{bot_id}:epoch"
