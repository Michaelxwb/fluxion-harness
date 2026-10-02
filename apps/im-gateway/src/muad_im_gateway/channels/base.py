from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from muad_contracts import ChannelEnvelope, DeliveryMessage, DeliveryRouteInput

logger = logging.getLogger(__name__)


class ChannelAdapterUnavailable(Exception):
    pass


class ChannelBotNotFound(ChannelAdapterUnavailable):
    """bot 未配置/已停用（快照里没有该 bot）：与"暂时不可用"区分，投递侧映射 BOT_NOT_FOUND。"""


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


#: 附件的原因码词汇表——**渠道中立**：渠道层（可能抛）与应用层（映射文案 + 写审计）共用同一套。
#: 单文件超限与门控实检里的 `ATTACHMENT_TOO_LARGE` 是**同一个码**：超限这件事只有一种说法。
ATTACHMENT_TOO_LARGE = "ATTACHMENT_TOO_LARGE"
ATTACHMENT_FETCH_TIMEOUT = "ATTACHMENT_FETCH_TIMEOUT"
ATTACHMENT_FETCH_FAILED = "ATTACHMENT_FETCH_FAILED"
ATTACHMENT_DECRYPT_FAILED = "ATTACHMENT_DECRYPT_FAILED"


class AttachmentFetchError(Exception):
    """取件失败（渠道中立）。

    渠道适配器**必须**把自己的私有异常翻译成这个类型——否则应用层就得 import 具体渠道的异常，
    "换通道只写适配器"就不成立了（RULE-07 / S-07）。`code` 同时用于用户可见反馈与审计原因码。
    """

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class FetchedAttachment:
    """渠道取回并**已解密**的媒体内容。

    刻意与契约 `AttachmentRef` 不同：这里已经有字节，但**还没有产物键**——落盘属应用编排层。
    """

    data: bytes
    media_type: str
    filename: str | None
    checksum: str


@runtime_checkable
class AttachmentSource(Protocol):
    """可选能力：本通道能否按 envelope 取回附件字节（AD-8 的跨层接缝）。

    核心域只给 envelope、下标与上限，**拿回的是已解密字节与元信息**——`url`/`aes_key` 这类
    渠道私有形状一步都不出去；新通道只要实现这两个方法，门控/落盘/契约那一侧一行不用改。
    """

    def attachment_count(self, envelope: ChannelEnvelope) -> int: ...

    async def fetch_attachment(
        self, envelope: ChannelEnvelope, index: int, *, max_bytes: int
    ) -> FetchedAttachment: ...


@runtime_checkable
class AdapterDegradation(Protocol):
    """连接管理器可报告未 CONNECTED 的 bot（`/readyz` 的 degraded 详情来源）。"""

    @property
    def degraded_bots(self) -> Mapping[str, str]: ...


@runtime_checkable
class StreamFinalizer(Protocol):
    async def finish_stream(self, route: DeliveryRouteInput) -> None: ...


def adapter_degraded(adapter: ChannelAdapter) -> Mapping[str, str]:
    if isinstance(adapter, AdapterDegradation):
        return adapter.degraded_bots
    return {}


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
    def degraded_bots(self) -> dict[str, str]:
        """未 CONNECTED 的 bot → 状态：只用于 `/readyz` 的 degraded 标记，不影响就绪（设计 §4.1）。"""
        degraded: dict[str, str] = {}
        for adapter in self.started_adapters:
            degraded.update(adapter_degraded(adapter))
        return degraded

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
