"""统一消息内核：WebChat 走 SYNC，IM 走 ASYNC；路由只依赖能力清单。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from muad_contracts.im import ChannelCapabilities, InboundEvent

from .spi import AsyncAdmission, SyncAdmission, SyncPrincipal


class ChannelIdentity(Protocol):
    """核心做通道一致性校验所需的最小面（完整适配器天然满足）。"""

    @property
    def channel(self) -> str: ...


@dataclass(frozen=True, slots=True)
class MessageGatewayPorts:
    """装配根注入的副作用：本模块不持 DB/Redis，也不做渠道 SDK 调用。

    未注入的动词被调用时**显式失败**（不静默降级）；本进程只提供它实际承接的路径，
    例如 WebChat 请求面只注入 `admit_sync`，IM ASYNC 由 Connector 的入站 Stream 消费。
    """

    admit_sync: Callable[[InboundEvent, SyncPrincipal], Awaitable[SyncAdmission]] | None = None
    admit_async: Callable[[InboundEvent], AsyncAdmission] | None = None
    capabilities: Callable[[str], ChannelCapabilities] | None = None


class MessageGateway:
    """按事件语义选择 SYNC/ASYNC；能力路由失败显式报错，不静默降级。"""

    def __init__(self, ports: MessageGatewayPorts) -> None:
        self._ports = ports

    async def admit_sync(
        self,
        event: InboundEvent,
        *,
        adapter: ChannelIdentity,
        principal_context: SyncPrincipal,
    ) -> SyncAdmission:
        """WebChat SYNC：入口已验证的主体原样进入受理端口，核心不再解析身份。"""
        self._require_channel(event, adapter)
        port = self._ports.admit_sync
        if port is None:
            raise RuntimeError("message gateway has no sync admission port")
        return await port(event, principal_context)

    def admit_async(self, event: InboundEvent, *, adapter: ChannelIdentity) -> AsyncAdmission:
        self._require_channel(event, adapter)
        port = self._ports.admit_async
        if port is None:
            raise RuntimeError("message gateway has no async admission port")
        return port(event)

    def capabilities_for(self, channel: str) -> ChannelCapabilities:
        port = self._ports.capabilities
        if port is None:
            raise RuntimeError("message gateway has no capabilities port")
        return port(channel)

    @staticmethod
    def _require_channel(event: InboundEvent, adapter: ChannelIdentity) -> None:
        if event.channel != adapter.channel:
            raise ValueError(f"event channel {event.channel!r} != adapter {adapter.channel!r}")
