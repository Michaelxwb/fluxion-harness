"""渠道无关的消息内核（TASK-006）：能力路由与 SYNC/ASYNC 受理 SPI。"""

from .gateway import MessageGateway, MessageGatewayPorts
from .spi import AsyncAdmission, ChannelAdapter, SyncAdmission
from .streams import stream_keys

__all__ = [
    "AsyncAdmission",
    "ChannelAdapter",
    "MessageGateway",
    "MessageGatewayPorts",
    "SyncAdmission",
    "stream_keys",
]
