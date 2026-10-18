"""WEBCHAT 标准通道适配器（TASK-047；设计 §3.2 架构与执行边界、§3.3 数据设计）。

WEBCHAT 是**稳定入口账号**：用户经 Gateway 自己的 HTTP 提交消息、经 SSE 订阅结果，
所以本适配器与企微完全同构地接入既有通道接口，但没有第三方长连接，也不需要任何 secret。

- **入站**：HTTP 提交的输入规范化为统一 `ChannelEnvelope`（`submit`），进**有界**队列；
  既有入站管线经**唯一** `iter_events()` 入口消费——渠道差异只活在适配器里，上层一行不用改。
- **出站**：本通道没有外部推送目标（终端用户靠订阅面拉取）。文本/增量交给装配时注入的
  `WebchatDeliverySink`（TASK-052/061 的 HTTP/SSE 面同形）；**未装配即显式失败**，绝不静默丢。
- **快照**：只认本通道的条目（`apply_snapshot`）；企微适配器同理按实际 channel 过滤，
  WEBCHAT 条目一步都进不了企微的长连接/secret 路径。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from muad_contracts import (
    AttachmentRef,
    BotSnapshotItem,
    ChannelEnvelope,
    ChannelName,
    DeliveryMessage,
    DeliveryRouteInput,
)

from .base import ChannelAdapterUnavailable

#: 入站队列上限：进程内缓存**有界**（HTTP 提交与消费循环之间的缓冲）。
#: 到上限即显式拒绝（`WebchatInboundOverloaded`），不撑大内存、不静默丢。
WEBCHAT_INBOUND_QUEUE_MAX = 256


class WebchatInboundOverloaded(ChannelAdapterUnavailable):
    """入站队列已满：**显式拒绝**（HTTP 层据此回明确的过载错误），绝不静默丢。"""


@dataclass(frozen=True, slots=True)
class WebchatInboundMessage:
    """HTTP 提交的入站输入（API-06 的形状：入口 + CHAT 主体 + 显式会话 + 幂等消息 ID）。

    刻意只有渠道中立的身份与内容：会话是**显式**的（RULE-10 不允许隐式最新指针），
    `platform_user_id` 就是本通道的外部用户身份（复用已有平台用户，不另建账号体系）。
    """

    entry_id: str
    platform_user_id: str
    conversation_id: str
    message_id: str
    text: str = ""
    attachments: tuple[AttachmentRef, ...] = ()


@runtime_checkable
class WebchatDeliverySink(Protocol):
    """WEBCHAT 出站落点：把**渠道中立**的 route/message 交给本通道的订阅面。

    与 `ArtifactLinkIssuer` 同一条接缝：适配器只认这个动词，不认识订阅面的形状
    （端点、游标、分片都在应用层，`RULE-im-002`）。
    """

    async def deliver(self, route: DeliveryRouteInput, message: DeliveryMessage) -> None: ...

    async def deliver_stream(
        self, route: DeliveryRouteInput, chunks: AsyncIterator[str]
    ) -> None: ...


class WebchatChannelAdapter:
    """标准 WEBCHAT 适配器：无长连接、无 secret，入站/出站都映射到统一封套。"""

    name: ChannelName = "WEBCHAT"

    def __init__(
        self,
        *,
        sink: WebchatDeliverySink | None = None,
        max_pending: int = WEBCHAT_INBOUND_QUEUE_MAX,
    ) -> None:
        if max_pending < 1:
            raise ValueError("webchat inbound queue size must be at least one")
        self._sink = sink
        self._queue: asyncio.Queue[ChannelEnvelope] = asyncio.Queue(maxsize=max_pending)
        self._entries: tuple[BotSnapshotItem, ...] = ()
        self._started = False

    @property
    def max_pending(self) -> int:
        return self._queue.maxsize

    @property
    def pending(self) -> int:
        """待消费的入站事件数（有界缓存的观测面：`/readyz` 与容量观测用）。"""
        return self._queue.qsize()

    @property
    def enabled_entries(self) -> tuple[BotSnapshotItem, ...]:
        """已启用的本通道入口（只含 WEBCHAT 条目；稳定入口不需要 secret）。"""
        return self._entries

    async def start(self) -> None:
        """无第三方连接：本地队列就绪即可服务（缺 secret 不影响本通道）。"""
        self._started = True

    async def stop(self) -> None:
        self._started = False

    async def apply_snapshot(self, items: Sequence[BotSnapshotItem]) -> None:
        """只认**本通道**的条目：别人的 bot 不进本适配器的缓存（通道差异只活在这里）。"""
        self._entries = tuple(
            item for item in items if item.enabled and item.channel == self.name
        )

    def submit(self, message: WebchatInboundMessage) -> ChannelEnvelope:
        """HTTP 入站：规范化为统一事件并入队；队满/未启动都**显式失败**。"""
        self._ensure_started()
        envelope = self._normalize(message)
        try:
            self._queue.put_nowait(envelope)
        except asyncio.QueueFull as exc:
            raise WebchatInboundOverloaded(
                f"webchat inbound queue is full max_pending={self.max_pending}"
            ) from exc
        return envelope

    async def iter_events(self) -> AsyncIterator[ChannelEnvelope]:
        """入站**唯一**消费入口：与企微/探针适配器同形，管线无需知道通道是谁。"""
        self._ensure_started()
        return self._drain()

    async def _drain(self) -> AsyncIterator[ChannelEnvelope]:
        while self._started:
            yield await self._queue.get()

    async def send(self, route: DeliveryRouteInput, message: DeliveryMessage) -> None:
        await self._require_sink().deliver(route, message)

    async def stream(self, route: DeliveryRouteInput, chunks: AsyncIterator[str]) -> None:
        await self._require_sink().deliver_stream(route, chunks)

    def _normalize(self, message: WebchatInboundMessage) -> ChannelEnvelope:
        """HTTP 形状 → 统一事件：显式会话、幂等消息 ID、平台用户即外部用户身份。"""
        return ChannelEnvelope(
            channel=self.name,
            bot_id=message.entry_id,
            external_user_id=message.platform_user_id,
            external_conversation_id=message.conversation_id,
            message_id=message.message_id,
            text=message.text,
            attachments=list(message.attachments),
        )

    def _ensure_started(self) -> None:
        if not self._started:
            raise ChannelAdapterUnavailable("webchat adapter is not started")

    def _require_sink(self) -> WebchatDeliverySink:
        if self._sink is None:
            # 未装配订阅面：显式失败，绝不把文本悄悄丢掉或假装已送达。
            raise ChannelAdapterUnavailable("webchat delivery sink is not wired")
        return self._sink
