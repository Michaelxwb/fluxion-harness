import logging
from typing import Any

from fastapi import APIRouter, Request
from muad_api import ApiResponse, AppError, ErrorCode, ok
from muad_contracts import DeliveryRequest

from ..channels.base import ChannelAdapterUnavailable, ChannelRegistry, ChannelRegistryError
from ..infrastructure.dedupe import DELIVERED_VALUE, DedupeStore, DedupeStoreError
from .deps import DedupeStoreDep, RegistryDep

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal/deliveries", tags=["delivery"])

DELIVERY_DEDUPE_PREFIX = "delivery:dedupe"
DELIVERY_DEDUPE_TTL_SEC = 604800
# 占位（in-flight）TTL 远短于送达标记：进程在占位后崩溃时占位会自动过期，
# 允许 Worker 的重试真正补发，而不是被占位永久挡住。
DELIVERY_IN_FLIGHT_TTL_SEC = 30


@router.post("")
async def deliver(
    body: DeliveryRequest,
    request: Request,
    registry: RegistryDep,
    dedupe: DedupeStoreDep,
) -> ApiResponse[Any]:
    key = f"{DELIVERY_DEDUPE_PREFIX}:{body.delivery_key}"
    try:
        reserved = await dedupe.reserve(key, DELIVERY_IN_FLIGHT_TTL_SEC)
    except DedupeStoreError as exc:
        # RULE-13：Redis 运行期不可用时降级为 at-least-once（可能重复），不能因此停发。
        logger.warning(
            "delivery_dedupe_degraded delivery_key=%s error=%s", body.delivery_key, exc
        )
        await _send(registry, body, dedupe=None, key=key)
        return ok(
            request.app.state.message_catalog,
            {"accepted": True, "duplicate": False, "delivered": True, "deduplicated": False},
        )
    if not reserved:
        return ok(
            request.app.state.message_catalog,
            {
                "accepted": True,
                "duplicate": True,
                "delivered": await _is_delivered(dedupe, key),
            },
        )
    await _send(registry, body, dedupe=dedupe, key=key)
    try:
        await dedupe.mark(key, DELIVERY_DEDUPE_TTL_SEC)
    except DedupeStoreError as exc:
        # 已真实发送：保留短 TTL 占位，最坏情况按 at-least-once 重复投递。
        logger.warning("delivery_dedupe_mark_failed delivery_key=%s error=%s", body.delivery_key, exc)
    return ok(
        request.app.state.message_catalog,
        {"accepted": True, "duplicate": False, "delivered": True},
    )


async def _send(
    registry: ChannelRegistry, body: DeliveryRequest, *, dedupe: DedupeStore | None, key: str
) -> None:
    """真实发送；失败时释放占位（若有）让 Worker 重试，未送达不宣称成功。"""
    try:
        adapter = registry.get(body.route.channel)
        await adapter.send(body.route, body.message)
    except (ChannelRegistryError, ChannelAdapterUnavailable) as exc:
        logger.warning("delivery_send_failed delivery_key=%s error=%s", body.delivery_key, exc)
        if dedupe is not None:
            await _release(dedupe, key, body.delivery_key)
        raise AppError(ErrorCode.COMMON_INTERNAL_ERROR) from exc


async def _is_delivered(dedupe: DedupeStore, key: str) -> bool:
    """占位≠送达：只有送达标记才算 delivered，占位中的重复必须回报未送达。"""
    try:
        return await dedupe.get_value(key) == DELIVERED_VALUE
    except DedupeStoreError as exc:
        logger.warning("delivery_dedupe_read_failed key=%s error=%s", key, exc)
        raise AppError(ErrorCode.COMMON_INTERNAL_ERROR) from exc


async def _release(dedupe: DedupeStore, key: str, delivery_key: str) -> None:
    try:
        await dedupe.release(key)
    except DedupeStoreError as exc:
        # 释放失败只能等占位 TTL 过期；此时不宣称已送达，Worker 会重试。
        logger.warning("delivery_dedupe_release_failed delivery_key=%s error=%s", delivery_key, exc)
