"""通用 Webhook 通道适配器（S-07：通道接入 = adapter 配置 + 路由，不新增部署单元）。

渠道差异只活在这里：签名算法、时间戳容差与回调形状由**配置**给定（`GATEWAY_WEBHOOK_CHANNELS`），
核心域与路由只认"验签通过的事件"这一件事。

安全边界（NFR-SEC-03 / S-07）：

- 验签失败、时间戳超出容差、缺少签名头 —— 一律拒绝，**不落库**（路由在 XADD 之前调用）；
- 签名覆盖 `{timestamp}.{raw_body}`：时间戳被篡改就不能复用旧签名；
- 比较用 `hmac.compare_digest`（常数时间），失败原因不回显（不告诉攻击者是签名还是时间戳错）。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
from muad_contracts import BotSnapshotItem, ChannelEnvelope, DeliveryMessage, DeliveryRouteInput
from muad_contracts.im import InboundEvent
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .base import ChannelAdapterUnavailable

logger = logging.getLogger(__name__)

TIMESTAMP_HEADER = "X-Webhook-Timestamp"
SIGNATURE_HEADER = "X-Webhook-Signature"
DEFAULT_TIMESTAMP_TOLERANCE_SEC = 300
#: 请求体硬上限：webhook 载荷是文本/引用，超过这个体积说明调用方搞错了（不缓冲无界内存）。
MAX_EVENT_BODY_BYTES = 1 << 20


class WebhookAuthError(Exception):
    """验签/时间戳失败（路由映射为 403，且不落库）。"""


@dataclass(frozen=True, slots=True)
class WebhookChannelConfig:
    """一个 webhook 通道的接入配置（secret 是可配置的签名密钥，不是 bot 连接密钥）。"""

    channel: str
    #: 验签密钥：不进 repr/日志（RULE-secret-001 的同类约束）。
    secret: str = field(repr=False)
    reply_url: str | None = None

    def __post_init__(self) -> None:
        if not self.channel.strip():
            raise ValueError("webhook channel must not be empty")
        if not self.secret:
            raise ValueError("webhook channel secret must not be empty")


class WebhookEventPayload(BaseModel):
    """提供方 → Gateway 的事件体（渠道中立；不含任何 bot 连接密钥/取件凭据）。"""

    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    bot_id: str = Field(min_length=1)
    external_user_id: str = Field(min_length=1)
    external_conversation_id: str | None = None
    message_id: str = Field(min_length=1)
    text: str = ""
    received_at: datetime | None = None
    trace_id: str = ""


def parse_webhook_channels(raw: str) -> tuple[WebhookChannelConfig, ...]:
    """解析 `GATEWAY_WEBHOOK_CHANNELS`（JSON 数组）；空值 = 本部署没有 webhook 通道。

    配置非法**启动即失败**（不退回"没有通道"这种看起来正常的空部署）。
    """
    if not raw.strip():
        return ()
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ValueError("GATEWAY_WEBHOOK_CHANNELS must be a JSON array") from exc
    if not isinstance(data, list):
        raise ValueError("GATEWAY_WEBHOOK_CHANNELS must be a JSON array")
    configs: list[WebhookChannelConfig] = []
    for item in data:
        if not isinstance(item, Mapping):
            raise ValueError("each webhook channel entry must be an object")
        configs.append(
            WebhookChannelConfig(
                channel=str(item.get("channel", "")),
                secret=str(item.get("secret", "")),
                reply_url=(str(item["reply_url"]) if item.get("reply_url") else None),
            )
        )
    channels = [config.channel for config in configs]
    if len(set(channels)) != len(channels):
        raise ValueError("GATEWAY_WEBHOOK_CHANNELS contains duplicate channels")
    return tuple(configs)


class WebhookChannelAdapter:
    """一个 webhook 通道：入站验签/规范化 + 出站回调投递。

    入站事件由路由 XADD 后经 Stream 消费（不在本适配器的 `iter_events` 里）：适配器只回答
    "这条请求是不是本通道的、验签是否通过、规范化后长什么样"。
    """

    def __init__(
        self,
        config: WebhookChannelConfig,
        *,
        timestamp_tolerance_sec: int = DEFAULT_TIMESTAMP_TOLERANCE_SEC,
        timeout_sec: float = 10.0,
    ) -> None:
        if timestamp_tolerance_sec < 1:
            raise ValueError("timestamp tolerance must be positive")
        self._config = config
        self._tolerance = timestamp_tolerance_sec
        self._timeout_sec = timeout_sec
        self._started = False
        self._bots: tuple[BotSnapshotItem, ...] = ()

    @property
    def name(self) -> str:
        return self._config.channel

    @property
    def reply_url(self) -> str | None:
        return self._config.reply_url

    async def start(self) -> None:
        self._started = True

    async def stop(self) -> None:
        self._started = False

    async def iter_events(self) -> AsyncIterator[ChannelEnvelope]:
        """本适配器的入站来自 HTTP + Stream，队列为空（保持协议形状）。"""
        return self._drain()

    async def _drain(self) -> AsyncIterator[ChannelEnvelope]:
        return
        yield ChannelEnvelope(  # pragma: no cover - 仅为声明异步生成器类型
            channel=self.name, bot_id="", external_user_id="", message_id=""
        )

    async def apply_snapshot(self, items: Sequence[BotSnapshotItem]) -> None:
        self._bots = tuple(item for item in items if item.channel == self.name)

    def verify(self, *, raw_body: bytes, headers: Mapping[str, str]) -> None:
        """验签 + 时间戳窗口；任一项不满足都抛 `WebhookAuthError`（不落库）。"""
        timestamp_raw = headers.get(TIMESTAMP_HEADER, "")
        signature = headers.get(SIGNATURE_HEADER, "")
        if not timestamp_raw or not signature:
            raise WebhookAuthError("missing webhook signature headers")
        try:
            timestamp = int(timestamp_raw)
        except ValueError as exc:
            raise WebhookAuthError("malformed webhook timestamp") from exc
        if abs(int(time.time()) - timestamp) > self._tolerance:
            raise WebhookAuthError("webhook timestamp is outside the accepted window")
        expected = self._signature(timestamp_raw, raw_body)
        if not hmac.compare_digest(expected, signature.strip().lower()):
            raise WebhookAuthError("webhook signature mismatch")

    def normalize(self, *, raw_body: bytes, tenant_id: str) -> InboundEvent:
        """请求体 → 统一入站事实；非法载荷抛 `ValidationError`（路由映射 422）。"""
        payload = WebhookEventPayload.model_validate_json(raw_body)
        return InboundEvent(
            event_id=payload.event_id,
            channel=self.name,
            tenant_id=tenant_id,
            bot_id=payload.bot_id,
            external_message_id=payload.message_id,
            external_conversation_id=payload.external_conversation_id,
            external_user_id=payload.external_user_id,
            text=payload.text,
            received_at=payload.received_at or datetime.now(UTC),
            trace_id=payload.trace_id,
        )

    def _signature(self, timestamp: str, raw_body: bytes) -> str:
        message = timestamp.encode() + b"." + raw_body
        return hmac.new(self._config.secret.encode(), message, hashlib.sha256).hexdigest()

    async def send(self, route: DeliveryRouteInput, message: DeliveryMessage) -> None:
        await self._post(
            {
                "channel": self.name,
                "bot_id": route.bot_id,
                "external_user_id": route.external_user_id,
                "external_conversation_id": route.external_conversation_id,
                "type": message.type,
                "text": message.text,
            }
        )

    async def stream(self, route: DeliveryRouteInput, chunks: AsyncIterator[str]) -> None:
        await self.send(route, DeliveryMessage(text="".join([chunk async for chunk in chunks])))

    async def _post(self, payload: dict[str, Any]) -> None:
        """回调投递：未配置回调地址即**显式失败**（不静默丢，也不假装已送达）。"""
        if not self._started:
            raise ChannelAdapterUnavailable("webhook adapter is not started")
        if not self._config.reply_url:
            raise ChannelAdapterUnavailable("webhook channel has no reply_url configured")
        async with httpx.AsyncClient(timeout=self._timeout_sec) as client:
            try:
                response = await client.post(self._config.reply_url, json=payload)
            except httpx.HTTPError as exc:
                raise ChannelAdapterUnavailable(f"webhook reply unreachable: {exc}") from exc
        if response.status_code >= 400:
            raise ChannelAdapterUnavailable(f"webhook reply rejected: {response.status_code}")


__all__ = [
    "DEFAULT_TIMESTAMP_TOLERANCE_SEC",
    "MAX_EVENT_BODY_BYTES",
    "SIGNATURE_HEADER",
    "TIMESTAMP_HEADER",
    "ValidationError",
    "WebhookAuthError",
    "WebhookChannelAdapter",
    "WebhookChannelConfig",
    "WebhookEventPayload",
    "parse_webhook_channels",
]
