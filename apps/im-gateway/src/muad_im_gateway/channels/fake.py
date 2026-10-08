from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass

from muad_contracts import ChannelEnvelope, DeliveryMessage, DeliveryRouteInput

from .base import (
    ATTACHMENT_FETCH_FAILED,
    AttachmentFetchError,
    FetchedAttachment,
)


@dataclass(frozen=True, slots=True)
class FakeBlob:
    """伪渠道的待取件字节：**没有 url、没有 aes_key**——取件路径与企微完全不同（AD-8）。

    渠道私有形状只活在适配器里：核心域拿到的仍是 `FetchedAttachment`（已解密字节 + 元信息）。
    """

    data: bytes
    media_type: str
    filename: str | None = None


class FakeChannelAdapter:
    name: str

    def __init__(self, name: str = "WECOM") -> None:
        self.name = name
        self.started = False
        self._events: asyncio.Queue[ChannelEnvelope] = asyncio.Queue()
        self._sent: list[tuple[DeliveryRouteInput, DeliveryMessage]] = []
        self._active_sent: list[tuple[DeliveryRouteInput, DeliveryMessage]] = []
        self._streamed: list[tuple[DeliveryRouteInput, tuple[str, ...]]] = []
        self._pending: dict[str, tuple[FakeBlob, ...]] = {}

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.started = False

    async def iter_events(self) -> AsyncIterator[ChannelEnvelope]:
        return self._drain()

    async def _drain(self) -> AsyncIterator[ChannelEnvelope]:
        while True:
            yield await self._events.get()

    async def push(self, envelope: ChannelEnvelope, *, blobs: Sequence[FakeBlob] = ()) -> None:
        """推入一条入站消息；`blobs` 是它待取件的字节（与企微的 `{url, aeskey}` 同义、异形）。"""
        if blobs:
            self._pending[envelope.message_id] = tuple(blobs)
        await self._events.put(envelope)

    def attachment_count(self, envelope: ChannelEnvelope) -> int:
        return len(self._pending.get(envelope.message_id, ()))

    async def fetch_attachment(
        self, envelope: ChannelEnvelope, index: int, *, max_bytes: int
    ) -> FetchedAttachment:
        """内存取件：不判 `max_bytes`（这里没有真实下载，体积由**实检门控**统一判）。

        越界/已驱逐与企微同口径地**明确失败**，不退化成空字节。
        """
        blobs = self._pending.get(envelope.message_id, ())
        if index < 0 or index >= len(blobs):
            raise AttachmentFetchError(ATTACHMENT_FETCH_FAILED)
        blob = blobs[index]
        return FetchedAttachment(
            data=blob.data,
            media_type=blob.media_type,
            filename=blob.filename,
            checksum=hashlib.sha256(blob.data).hexdigest(),
        )

    async def send(self, route: DeliveryRouteInput, message: DeliveryMessage) -> None:
        self._sent.append((route, message))

    async def send_active(self, route: DeliveryRouteInput, message: DeliveryMessage) -> None:
        """主动投递（不借入站回调）：单独记账，便于断言终态文本走的是哪条路径。"""
        self._active_sent.append((route, message))

    async def stream(self, route: DeliveryRouteInput, chunks: AsyncIterator[str]) -> None:
        collected = tuple([chunk async for chunk in chunks])
        self._streamed.append((route, collected))

    @property
    def sent(self) -> tuple[tuple[DeliveryRouteInput, DeliveryMessage], ...]:
        return tuple(self._sent)

    @property
    def sent_active(self) -> tuple[tuple[DeliveryRouteInput, DeliveryMessage], ...]:
        return tuple(self._active_sent)

    @property
    def streamed(self) -> tuple[tuple[DeliveryRouteInput, tuple[str, ...]], ...]:
        return tuple(self._streamed)
