"""渠道无关的消息内核（TASK-006）：能力路由、SYNC/ASYNC 受理 SPI、入口快照与入站持久化。"""

from .gateway import ChannelIdentity, MessageGateway, MessageGatewayPorts
from .ingress import InboundPublisher, PublishOutcome, SweepResult
from .snapshot import BotSnapshotCache, BotSnapshotSource
from .spi import AsyncAdmission, ChannelAdapter, SyncAdmission, SyncPrincipal
from .streams import stream_keys

__all__ = [
    "AsyncAdmission",
    "BotSnapshotCache",
    "BotSnapshotSource",
    "ChannelAdapter",
    "ChannelIdentity",
    "InboundPublisher",
    "MessageGateway",
    "MessageGatewayPorts",
    "PublishOutcome",
    "SweepResult",
    "SyncAdmission",
    "SyncPrincipal",
    "stream_keys",
]
