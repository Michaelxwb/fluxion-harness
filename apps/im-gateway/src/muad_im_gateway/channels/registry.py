"""标准通道适配器的装配与快照分发（TASK-047；设计 §3.3「装配根按类型分发」）。

**Gateway 是请求面（web 角色）**：这里只装配不含渠道长连接/私钥的适配器 —— WEBCHAT 稳定入口
（HTTP 提交 + SSE）与**通用 webhook**（配置驱动的验签入口）。企微等需要长连接与 bot secret 的
通道活在 Connector 的 adapter surface（`apps/im-connector/.../channels/`），Gateway 不 import
它们的 SDK，也不会从 Console 快照里拿到它们的 secret（快照按 channel 过滤）。

装配根本身**不按通道分支**：新增通道 = 多一条 `GATEWAY_WEBHOOK_CHANNELS` 配置（或新适配器），
上层一行不用改。
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from decimal import Decimal

from muad_common import SharedSettings
from muad_contracts import DEFAULT_EXTERNAL_CHANNEL, BotSnapshotItem
from muad_logging import get_logger

from .base import (
    ChannelAdapter,
    ChannelRegistry,
    SnapshotAware,
    UnknownChannelAdapterError,
)
from .routed import (
    ConnectorCommandSink,
    RoutedChannelAdapter,
    parse_routed_channels,
)
from .webchat import WebchatChannelAdapter
from .webhook import WebhookChannelAdapter, parse_webhook_channels

logger = get_logger(__name__)

#: 快照回调签名（`muad_im_core.BotSnapshotCache` 的 `SnapshotChangeCallback` 同形；
#: 这里刻意不反向 import 应用层 —— channels 是装配根的下游，依赖方向单向）。
SnapshotChangeCallback = Callable[[tuple[BotSnapshotItem, ...]], Awaitable[None]]


#: 外部断言平台的装配摘要：`platform_key` → (channel, assertion_header)。
#: Gateway 只需通道名与断言头名，验签/身份键解析仍由认证侧（Console）负责。
DEFAULT_ASSERTION_HEADER = "soc-token"


def parse_external_platform_surfaces(
    settings: SharedSettings,
) -> dict[str, tuple[str, str]]:
    """从 Secret 解析每个外部平台要挂的 (channel, assertion_header)。

    返回 ``{platform_key: (channel, assertion_header)}``。``channel`` 缺省为
    ``MSSWWEB``、``assertion_header`` 缺省为 ``soc-token``，保证存量单平台部署零变化。
    解析失败/非 dict 返回空表（等价"未配置"，走既有的 503 路径，不静默放行）。

    **一平台一通道**：若两个平台解析出同一个 ``channel``，入口层（只按 channel 区分）会
    冲突，这里显式记 ERROR 但保留首个，交给上层按需失败，不静默合并。
    """
    raw = getattr(settings, "external_assertion_platforms_json", "") or ""
    try:
        # `parse_float=Decimal`：平台 id 可能是超长数字，按 float 解析会丢精度。
        parsed = json.loads(raw, parse_float=Decimal) if raw.strip() else {}
    except ValueError:
        logger.error("external_assertion_platforms_unparseable")
        return {}
    if not isinstance(parsed, dict):
        logger.error("external_assertion_platforms_not_object")
        return {}

    surfaces: dict[str, tuple[str, str]] = {}
    seen_channels: dict[str, str] = {}
    for platform_key, item in parsed.items():
        if not isinstance(item, dict):
            logger.error("external_assertion_platform_item_invalid platform=%s", platform_key)
            continue
        channel = str(item.get("channel") or DEFAULT_EXTERNAL_CHANNEL)
        header = str(item.get("assertion_header") or DEFAULT_ASSERTION_HEADER)
        if channel in seen_channels:
            logger.error(
                "external_assertion_channel_conflict channel=%s platforms=%s,%s",
                channel,
                seen_channels[channel],
                platform_key,
            )
            continue
        seen_channels[channel] = platform_key
        surfaces[str(platform_key)] = (channel, header)
    return surfaces


def external_platforms_enabled(settings: SharedSettings) -> bool:
    """是否配置了外部断言平台（外部断言面的启用开关）。

    只判断配置是否非空：具体平台的验签/身份键解析在认证侧（TASK-007/012）负责，
    Gateway 注册表只需知道"该面是否启用"。
    """
    return bool(parse_external_platform_surfaces(settings))


def build_channel_registry(
    settings: SharedSettings, *, routed_sink: ConnectorCommandSink | None = None
) -> ChannelRegistry:
    """装配本 Gateway 声明拥有的通道面（装配根的唯一入口）。

    `GATEWAY_ROUTED_CHANNELS` 声明的通道是**远程 Connector 的请求面**：入站只路由、
    出站写 `im.outbound`；没有 sink（未接线）时装配仍成立，任何发送都显式失败。
    """
    registry = ChannelRegistry()
    # 本地流式对话面：WEBCHAT 恒定注册；每个外部断言平台按它配置的 `channel` 名各注册
    # 一个适配器。未配置外部平台的部署与原来完全一致（不暴露该面，见设计 §I4 / RULE-im-006）。
    registry.register(WebchatChannelAdapter(name="WEBCHAT"))
    for channel, _header in parse_external_platform_surfaces(settings).values():
        registry.register(WebchatChannelAdapter(name=channel))
    for config in parse_webhook_channels(settings.gateway_webhook_channels):
        registry.register(
            WebhookChannelAdapter(
                config,
                timestamp_tolerance_sec=settings.webhook_timestamp_tolerance_sec,
            )
        )
    for channel in parse_routed_channels(settings.gateway_routed_channels):
        registry.register(RoutedChannelAdapter(channel, sink=routed_sink))
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
