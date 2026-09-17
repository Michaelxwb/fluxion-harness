from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from muad_contracts import ChannelEnvelope, DeliveryMessage, DeliveryRouteInput


class FakeChannelAdapter:
    name: str

    def __init__(self, name: str = "WECOM") -> None:
        self.name = name
        self.started = False
        self._events: asyncio.Queue[ChannelEnvelope] = asyncio.Queue()
        self._sent: list[tuple[DeliveryRouteInput, DeliveryMessage]] = []
        self._streamed: list[tuple[DeliveryRouteInput, tuple[str, ...]]] = []

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.started = False

    async def iter_events(self) -> AsyncIterator[ChannelEnvelope]:
        return self._drain()

    async def _drain(self) -> AsyncIterator[ChannelEnvelope]:
        while True:
            yield await self._events.get()

    async def push(self, envelope: ChannelEnvelope) -> None:
        await self._events.put(envelope)

    async def send(self, route: DeliveryRouteInput, message: DeliveryMessage) -> None:
        self._sent.append((route, message))

    async def stream(self, route: DeliveryRouteInput, chunks: AsyncIterator[str]) -> None:
        collected = tuple([chunk async for chunk in chunks])
        self._streamed.append((route, collected))

    @property
    def sent(self) -> tuple[tuple[DeliveryRouteInput, DeliveryMessage], ...]:
        return tuple(self._sent)

    @property
    def streamed(self) -> tuple[tuple[DeliveryRouteInput, tuple[str, ...]], ...]:
        return tuple(self._streamed)
