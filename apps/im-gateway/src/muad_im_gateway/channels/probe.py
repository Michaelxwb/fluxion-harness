"""本地真实 HTTP 渠道探针适配器（验收/联调用）。

设计允许外部渠道用本地 HTTP 探针替代第三方实网：投递请求以真实 HTTP 发出，
探针记录请求并返回结果，便于在无外网环境验证投递链路与去重语义。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

import httpx
from muad_contracts import (
    BotSnapshotItem,
    ChannelEnvelope,
    DeliveryMessage,
    DeliveryRouteInput,
)

from .base import ChannelAdapterUnavailable


class HttpProbeChannelAdapter:
    name = "WECOM"

    def __init__(self, url: str, *, timeout_sec: float = 10.0) -> None:
        self._url = url
        self._timeout_sec = timeout_sec
        self._started = False
        self._bots: tuple[BotSnapshotItem, ...] = ()

    async def start(self) -> None:
        self._started = True

    async def stop(self) -> None:
        self._started = False

    async def iter_events(self) -> AsyncIterator[ChannelEnvelope]:
        return self._drain()

    async def _drain(self) -> AsyncIterator[ChannelEnvelope]:
        """探针不产生入站事件：保持与 ChannelAdapter 协议一致的异步迭代器。"""
        return
        yield ChannelEnvelope(  # pragma: no cover - 仅为声明异步生成器类型
            channel="WECOM", bot_id="", external_user_id="", message_id=""
        )

    async def send(self, route: DeliveryRouteInput, message: DeliveryMessage) -> None:
        async with httpx.AsyncClient(timeout=self._timeout_sec) as client:
            try:
                response = await client.post(
                    self._url,
                    json={
                        "bot_id": route.bot_id,
                        "external_user_id": route.external_user_id,
                        "external_conversation_id": route.external_conversation_id,
                        "text": message.text,
                    },
                )
            except httpx.HTTPError as exc:
                raise ChannelAdapterUnavailable(f"probe unreachable: {exc}") from exc
        if response.status_code >= 400:
            raise ChannelAdapterUnavailable(f"probe rejected: {response.status_code}")

    async def stream(self, route: DeliveryRouteInput, chunks: AsyncIterator[str]) -> None:
        text = "".join([chunk async for chunk in chunks])
        await self.send(route, DeliveryMessage(text=text))

    def healthy(self) -> bool:
        return self._started

    async def apply_snapshot(self, bots: Sequence[BotSnapshotItem]) -> None:
        """与 WeComAdapter 同构：探针不维护 bot 连接，仅接受快照。"""
        self._bots = tuple(bots)
