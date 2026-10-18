"""标准通道适配器的装配与快照分发（TASK-047；设计 §3.3「装配根按类型分发」）。

通道字样只活在 `channels/` 与装配根：装配根本身**不按通道分支**——它调用
`build_channel_registry` 拿到注册表，并把整份 bot 快照交给 `dispatch_snapshot`
按 `BotSnapshotItem.channel` 分发。新增通道 = 在这里多注册一个适配器，上层一行不用改。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence

from muad_artifact_store import NfsArtifactStore
from muad_common import SharedSettings
from muad_contracts import BotSnapshotItem

from .base import (
    ArtifactLinkIssuer,
    ChannelAdapter,
    ChannelRegistry,
    SnapshotAware,
    UnknownChannelAdapterError,
)
from .probe import HttpProbeChannelAdapter
from .webchat import WebchatChannelAdapter
from .wecom.adapter import WeComAdapter

logger = logging.getLogger(__name__)

#: 快照回调签名（与 `application/bot_snapshot.py` 的 `SnapshotChangeCallback` 同形；
#: 这里刻意不反向 import application —— channels 是装配根的下游，依赖方向单向）。
SnapshotChangeCallback = Callable[[tuple[BotSnapshotItem, ...]], Awaitable[None]]


def build_channel_registry(
    settings: SharedSettings, fetch_links: ArtifactLinkIssuer
) -> ChannelRegistry:
    """装配全部标准适配器（装配根的唯一入口）。

    - `WECOM`：配置了 `channel_probe_url` 时用本地真实 HTTP 探针替代第三方实网渠道；
      否则用真实 `WeComAdapter`（产物按 storage_key 直读共享 store，取件直链经 `fetch_links`）。
    - `WEBCHAT`：标准稳定入口适配器——HTTP 提交入站、无长连接、无 secret；出站订阅面由
      后续装配（TASK-052/061）注入，未装配时出站显式失败。
    """
    registry = ChannelRegistry()
    if settings.channel_probe_url:
        # 本地真实 HTTP 探针：验收/联调环境替代第三方实网渠道。
        registry.register(HttpProbeChannelAdapter(settings.channel_probe_url))
    else:
        registry.register(
            WeComAdapter(
                artifact_store=NfsArtifactStore(settings.artifact_root),
                fetch_links=fetch_links,
                status_updates_per_second=settings.im_progress_updates_per_second,
            )
        )
    registry.register(WebchatChannelAdapter())
    return registry


def dispatch_snapshot(registry: ChannelRegistry) -> SnapshotChangeCallback:
    """按 `BotSnapshotItem.channel` 分发的快照回调（交给 `BotSnapshotCache`）。

    - 每个适配器只拿到**自己通道**的条目（"这份就是全部"的语义按通道成立）；
    - 未装配/不支持快照的通道：记 WARNING 并跳过（不静默），不影响其他通道；
    - 某适配器应用失败：**向上抛**——快照缓存据此不认定该 revision 生效，下一拍整批重试，
      否则那个通道会永远停在旧配置（`BotSnapshotCache.refresh` 的既有口径）。
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
