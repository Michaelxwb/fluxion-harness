"""Webhook 入站路由（S-07）：验签 → XADD `im.inbound` → 快速 2xx。

**顺序是契约**：合法事件先持久化再 ACK（崩溃也不会丢）；验签/时间戳/载荷不合法的请求在
XADD **之前**被拒，不落库、不建 Run。重复 `event_id` 由入站发布器的标记去重（XADD 之前收敛），
因此不会出现第二条 Stream 记录或第二个 Run。

路由本身**不认识任何具体渠道**：验签算法/密钥/回调都在 `WebhookChannelAdapter`（配置驱动）。
"""

from __future__ import annotations

import logging
from typing import Any

import redis.asyncio
from fastapi import APIRouter, Request
from muad_api import ApiResponse, AppError, ErrorCode, ok
from pydantic import ValidationError

from ..channels.base import ChannelRegistry, UnknownChannelAdapterError
from ..channels.webhook import MAX_EVENT_BODY_BYTES, WebhookAuthError, WebhookChannelAdapter
from .deps import GatewayTenantDep, InboundPublisherDep, RegistryDep

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/channels", tags=["webhook"])


@router.post("/{channel}/events", status_code=202)
async def receive_event(
    channel: str,
    request: Request,
    registry: RegistryDep,
    publisher: InboundPublisherDep,
    tenant_id: GatewayTenantDep,
) -> ApiResponse[Any]:
    """接收一条 webhook 事件：验签通过才持久化，2xx 只表示"已入队"。"""
    adapter = _webhook_adapter(registry, channel)
    raw = await request.body()
    if len(raw) > MAX_EVENT_BODY_BYTES:
        logger.warning("webhook_body_too_large channel=%s bytes=%s", channel, len(raw))
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
    try:
        adapter.verify(raw_body=raw, headers=request.headers)
    except WebhookAuthError as exc:
        # 不回显"签名错还是时间戳错"：拒绝原因只留在服务端日志。
        logger.warning("webhook_rejected channel=%s reason=%s", channel, exc)
        raise AppError(ErrorCode.FORBIDDEN) from exc
    try:
        event = adapter.normalize(raw_body=raw, tenant_id=tenant_id)
    except ValidationError as exc:
        logger.warning("webhook_payload_invalid channel=%s", channel)
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR) from exc
    try:
        outcome = await publisher.publish(event, dedupe_id=str(event.event_id))
    except (redis.RedisError, OSError, ValueError) as exc:
        # 不能持久化就不能 ACK：让提供方重试，而不是假装收下了。
        logger.error("webhook_persist_failed channel=%s error=%s", channel, type(exc).__name__)
        raise AppError(ErrorCode.COMMON_INTERNAL_ERROR) from exc
    return ok(
        request.app.state.message_catalog,
        {"accepted": True, "duplicate": outcome.value == "DUPLICATE"},
    )


def _webhook_adapter(registry: ChannelRegistry, channel: str) -> WebhookChannelAdapter:
    try:
        adapter = registry.get(channel)
    except UnknownChannelAdapterError as exc:
        raise AppError(ErrorCode.COMMON_NOT_FOUND) from exc
    if not isinstance(adapter, WebhookChannelAdapter):
        raise AppError(ErrorCode.COMMON_NOT_FOUND)
    return adapter
