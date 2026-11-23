"""渠道无关的消息内核（TASK-006）：能力路由、SYNC/ASYNC 受理 SPI、入口快照与入站持久化。"""

from .gateway import ChannelIdentity, MessageGateway, MessageGatewayPorts
from .inbound_batch import (
    BatchFrame,
    BatchJournal,
    BatchSettings,
    InboundBatchCoordinator,
    RedisBatchJournal,
)
from .ingress import InboundPublisher, PublishOutcome, SweepResult
from .snapshot import BotSnapshotCache, BotSnapshotSource
from .spi import AsyncAdmission, ChannelAdapter, SyncAdmission, SyncPrincipal
from .streams import stream_keys

__all__ = [
    "AsyncAdmission",
    "BatchFrame",
    "BatchJournal",
    "BatchSettings",
    "BotSnapshotCache",
    "BotSnapshotSource",
    "ChannelAdapter",
    "ChannelIdentity",
    "InboundBatchCoordinator",
    "InboundPublisher",
    "MessageGateway",
    "MessageGatewayPorts",
    "PublishOutcome",
    "RedisBatchJournal",
    "SweepResult",
    "SyncAdmission",
    "SyncPrincipal",
    "stream_keys",
]
