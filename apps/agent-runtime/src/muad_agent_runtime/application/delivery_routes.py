"""Run 持久化入口 → 渠道中立投递路由（design §4.4/§4.5）。

放在独立模块：Run 终态写入（`channel_notifications`）、投递泵与 Run 服务都要读同一条
路由事实，从 `run_service` 导入会形成环。函数保持唯一实现，`run_service` 继续 re-export。
"""

from __future__ import annotations

from typing import Any

from muad_contracts import ChannelContext, DeliveryRouteInput
from pydantic import ValidationError


def delivery_route_of(channel_json: dict[str, Any] | None) -> DeliveryRouteInput | None:
    """从 Run 持久化的入站渠道构建投递路由；缺接收方时返回 None（不投递）。"""
    if not channel_json:
        return None
    try:
        channel = ChannelContext.model_validate(channel_json)
    except ValidationError:
        return None
    if not channel.external_user_id:
        return None
    return DeliveryRouteInput(
        channel=channel.type,
        bot_id=channel.bot_id,
        external_user_id=channel.external_user_id,
        external_conversation_id=channel.external_conversation_id,
    )
