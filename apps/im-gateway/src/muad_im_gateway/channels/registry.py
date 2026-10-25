"""标准通道适配器的装配与快照分发（TASK-047；设计 §3.3「装配根按类型分发」）。

**Gateway 是请求面（web 角色）**：这里只装配不含渠道长连接/私钥的适配器 —— WEBCHAT 稳定入口
（HTTP 提交 + SSE）与**通用 webhook**（配置驱动的验签入口）。企微等需要长连接与 bot secret 的
通道活在 Connector 的 adapter surface（`apps/im-connector/.../channels/`），Gateway 不 import
它们的 SDK，也不会从 Console 快照里拿到它们的 secret（快照按 channel 过滤）。

装配根本身**不按通道分支**：新增通道 = 多一条 `GATEWAY_WEBHOOK_CHANNELS` 配置（或新适配器），
上层一行不用改。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence

from muad_common import SharedSettings
from muad_contracts import BotSnapshotItem

from .base import (
    ChannelAdapter,
    ChannelRegistry,
    SnapshotAware,
    UnknownChannelAdapterError,
)
from .webchat import WebchatChannelAdapter
from .webhook import WebhookChannelAdapter, parse_webhook_channels

logger = logging.getLogger(__name__)

#: 快照回调签名（`muad_im_core.BotSnapshotCache` 的 `SnapshotChangeCallback` 同形；
#: 这里刻意不反向 import 应用层 —— channels 是装配根的下游，依赖方向单向）。
SnapshotChangeCallback = Callable[[tuple[BotSnapshotItem, ...]], Awaitable[None]]


def build_channel_registry(settings: SharedSettings) -> ChannelRegistry:
    """装配本 Gateway 声明拥有的通道面（装配根的唯一入口）。"""
    registry = ChannelRegistry()
    registry.register(WebchatChannelAdapter())
    for config in parse_webhook_channels(settings.gateway_webhook_channels):
        registry.register(
            WebhookChannelAdapter(
                config,
                timestamp_tolerance_sec=settings.webhook_timestamp_tolerance_sec,
            )
        )
    return registry


def dispatch_snapshot(registry: ChannelRegistry) -> SnapshotChangeCallback:
    """按 `BotSnapshotItem.channel` 分发的快照回调（交给 `BotSnapshotCache`）。

    - 每个适配器只拿到**自己通道**的条目（"这份就是全部"的语义按通道成立）；
    - 未装配/不支持快照的通道：记 WARNING 并跳过（不静默），不影响其他通道；
    - 某适配器应用失败：**向上抛**——快照缓存据此不认定该 revision 生效，下一拍整批重试。
    """

    async def on_snapshot_changed(items: tuple[BotSnapshotItem, ...]) -> None:
        for channel, channel_items in _group_by_channel(items).items():
            adapter = _adapter_for(registry, channel)
            if adapter is None:
                continue
            if not isinstance(adapter, SnapshotAware):
                logger.warning("snapshot_apply_unsupported channel=%s", channel)
                continue
            await adapter.apply_snapshot(channel_items)

    return on_snapshot_changed


def _group_by_channel(
    items: Sequence[BotSnapshotItem],
) -> dict[str, tuple[BotSnapshotItem, ...]]:
    grouped: dict[str, list[BotSnapshotItem]] = {}
    for item in items:
        grouped.setdefault(item.channel, []).append(item)
    return {channel: tuple(channel_items) for channel, channel_items in grouped.items()}


def _adapter_for(registry: ChannelRegistry, channel: str) -> ChannelAdapter | None:
    try:
        return registry.get(channel)
    except UnknownChannelAdapterError:
        logger.warning("snapshot_channel_unregistered channel=%s", channel)
        return None
