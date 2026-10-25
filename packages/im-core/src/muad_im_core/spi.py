"""Connector/Gateway 可实现的渠道 SPI；核心不 import 任何 SDK。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from muad_contracts.im import ChannelCapabilities, DeliveryCommand, InboundEvent


@dataclass(frozen=True, slots=True)
class SyncAdmission:
    """WebChat SYNC 受理结果：调用方据此返回 202/幂等重放。"""

    run_id: str
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class AsyncAdmission:
    """IM ASYNC 受理结果：入站已持久化/入队后返回。"""

    accepted: bool
    duplicate: bool = False


class ChannelAdapter(Protocol):
    """新 IM 只需实现此协议；渠道 SDK/协议细节不得进入核心域。"""

    @property
    def channel(self) -> str: ...

    def capabilities(self) -> ChannelCapabilities: ...

    def normalize(self, raw: object) -> InboundEvent: ...

    async def send(self, command: DeliveryCommand) -> None: ...
