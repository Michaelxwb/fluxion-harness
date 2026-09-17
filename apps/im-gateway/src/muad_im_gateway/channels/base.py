from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from muad_contracts import ChannelEnvelope, DeliveryMessage, DeliveryRouteInput

logger = logging.getLogger(__name__)


class ChannelAdapterUnavailable(Exception):
    pass


class ChannelRegistryError(Exception):
    pass


class DuplicateChannelAdapterError(ChannelRegistryError):
    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"channel adapter already registered: {name}")


class UnknownChannelAdapterError(ChannelRegistryError):
    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"channel adapter not registered: {name}")


class ChannelAdapter(Protocol):
    name: str

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def iter_events(self) -> AsyncIterator[ChannelEnvelope]: ...
    async def send(self, route: DeliveryRouteInput, message: DeliveryMessage) -> None: ...
    async def stream(self, route: DeliveryRouteInput, chunks: AsyncIterator[str]) -> None: ...


@runtime_checkable
class AdapterHealth(Protocol):
    def healthy(self) -> bool: ...


@runtime_checkable
class StreamFinalizer(Protocol):
    async def finish_stream(self, route: DeliveryRouteInput) -> None: ...


def adapter_healthy(adapter: ChannelAdapter) -> bool:
    if isinstance(adapter, AdapterHealth):
        return adapter.healthy()
    return True


class ChannelRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, ChannelAdapter] = {}
        self._started: set[str] = set()

    def register(self, adapter: ChannelAdapter) -> None:
        if adapter.name in self._adapters:
            raise DuplicateChannelAdapterError(adapter.name)
        self._adapters[adapter.name] = adapter

    def get(self, channel: str) -> ChannelAdapter:
        adapter = self._adapters.get(channel)
        if adapter is None:
            raise UnknownChannelAdapterError(channel)
        return adapter

    def names(self) -> tuple[str, ...]:
        return tuple(self._adapters)

    @property
    def adapter_states(self) -> dict[str, bool]:
        return {name: name in self._started for name in self._adapters}

    @property
    def started_adapters(self) -> tuple[ChannelAdapter, ...]:
        return tuple(self._adapters[name] for name in self._started)

    @property
    def healthy_adapters(self) -> tuple[ChannelAdapter, ...]:
        return tuple(adapter for adapter in self.started_adapters if adapter_healthy(adapter))

    async def start_all(self) -> None:
        for name, adapter in self._adapters.items():
            try:
                await adapter.start()
            except ChannelAdapterUnavailable as exc:
                logger.warning("channel_adapter_unavailable name=%s error=%s", name, exc)
                continue
            self._started.add(name)

    async def stop_all(self) -> None:
        for name in tuple(self._started):
            adapter = self._adapters[name]
            try:
                await adapter.stop()
            except ChannelAdapterUnavailable as exc:
                logger.warning("channel_adapter_stop_unavailable name=%s error=%s", name, exc)
            finally:
                self._started.discard(name)
