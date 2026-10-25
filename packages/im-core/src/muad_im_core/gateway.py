"""统一消息内核：WebChat 走 SYNC，IM 走 ASYNC；路由只依赖能力清单。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from muad_contracts.im import ChannelCapabilities, InboundEvent

from .spi import AsyncAdmission, ChannelAdapter, SyncAdmission


@dataclass(frozen=True, slots=True)
class MessageGatewayPorts:
    """装配根注入的副作用：本模块不持 DB/Redis，也不做渠道 SDK 调用。"""

    admit_sync: Callable[[InboundEvent], SyncAdmission]
    admit_async: Callable[[InboundEvent], AsyncAdmission]
    capabilities: Callable[[str], ChannelCapabilities]


class MessageGateway:
    """按事件语义选择 SYNC/ASYNC；能力路由失败显式报错，不静默降级。"""

    def __init__(self, ports: MessageGatewayPorts) -> None:
        self._ports = ports

    def admit_sync(self, event: InboundEvent, *, adapter: ChannelAdapter) -> SyncAdmission:
        self._require_channel(event, adapter)
        return self._ports.admit_sync(event)

    def admit_async(self, event: InboundEvent, *, adapter: ChannelAdapter) -> AsyncAdmission:
        self._require_channel(event, adapter)
        return self._ports.admit_async(event)

    def capabilities_for(self, channel: str) -> ChannelCapabilities:
        return self._ports.capabilities(channel)

    @staticmethod
    def _require_channel(event: InboundEvent, adapter: ChannelAdapter) -> None:
        if event.channel != adapter.channel:
            raise ValueError(f"event channel {event.channel!r} != adapter {adapter.channel!r}")
