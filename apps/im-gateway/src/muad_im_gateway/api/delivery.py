import logging
import asyncio
import time
from typing import Any

from fastapi import APIRouter, Request
from muad_api import metrics
from muad_api import ApiResponse, AppError, ErrorCode, ok
from muad_contracts import DeliveryRequest

from ..channels.base import (
    ChannelAdapterUnavailable,
    ChannelBotNotFound,
    ChannelRegistry,
    ChannelRegistryError,
)
from ..infrastructure.dedupe import DELIVERED_VALUE, DedupeStore, DedupeStoreError
from .deps import DedupeStoreDep, RegistryDep

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal/deliveries", tags=["delivery"])

DELIVERY_DEDUPE_PREFIX = "delivery:dedupe"
DELIVERY_DEDUPE_TTL_SEC = 604800
# 占位（in-flight）TTL 远短于送达标记：进程在占位后崩溃时占位会自动过期，
# 允许 Worker 的重试真正补发，而不是被占位永久挡住。
DELIVERY_IN_FLIGHT_TTL_SEC = 30
# 已在处理中（占位存在但尚无成功键）时的有界等待：超时回可重试错误，不把占位当成功。
DELIVERY_IN_FLIGHT_WAIT_SEC = 2.0
BACKGROUND_DELIVERY_METRIC = "im_background_delivery_total"
DELIVERY_IN_FLIGHT_POLL_SEC = 0.1


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
        _count_delivery("accepted")
        return ok(
            request.app.state.message_catalog,
            {"accepted": True, "duplicate": False, "delivered": True, "deduplicated": False},
        )
    if not reserved:
        return await _replay(request, dedupe, key)
    await _send(registry, body, dedupe=dedupe, key=key)
    try:
        await dedupe.mark(key, DELIVERY_DEDUPE_TTL_SEC)
    except DedupeStoreError as exc:
        # 已真实发送：保留短 TTL 占位，最坏情况按 at-least-once 重复投递。
        logger.warning("delivery_dedupe_mark_failed delivery_key=%s error=%s", body.delivery_key, exc)
    _count_delivery("accepted")
    return ok(
        request.app.state.message_catalog,
        {"accepted": True, "duplicate": False, "delivered": True, "deduplicated": False},
    )


def _count_delivery(status: str) -> None:
    """投递结果计数（标签只含状态，不含 delivery_key/正文）。"""
    metrics.inc_counter(
        BACKGROUND_DELIVERY_METRIC, 1, {"status": status}, help="Background deliveries by status"
    )


async def _replay(request: Request, dedupe: DedupeStore, key: str) -> ApiResponse[Any]:
    """已存在占位/成功键：仅成功键算成功；只有占位时有界等待，超时报可重试错误。"""
    deadline = time.monotonic() + DELIVERY_IN_FLIGHT_WAIT_SEC
    while not await _is_delivered(dedupe, key):
        if time.monotonic() >= deadline:
            logger.warning("delivery_in_flight_timeout key=%s", key)
            _count_delivery("failed")
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
        await asyncio.sleep(DELIVERY_IN_FLIGHT_POLL_SEC)
    _count_delivery("deduplicated")
    return ok(
        request.app.state.message_catalog,
        {"accepted": True, "duplicate": True, "delivered": True, "deduplicated": True},
    )


async def _send(
    registry: ChannelRegistry, body: DeliveryRequest, *, dedupe: DedupeStore | None, key: str
) -> None:
    """真实发送；失败时释放占位（若有）让 Worker 重试，未送达不宣称成功。"""
    try:
        adapter = registry.get(body.route.channel)
        await adapter.send(body.route, body.message)
    except ChannelBotNotFound as exc:
        # 未配置/已停用 bot：与"暂时不可用"区分（设计 API-05 错误码）
        logger.warning("delivery_bot_not_found delivery_key=%s error=%s", body.delivery_key, exc)
        if dedupe is not None:
            await _release(dedupe, key, body.delivery_key)
        _count_delivery("failed")
        raise AppError(ErrorCode.BOT_NOT_FOUND) from exc
    except (ChannelRegistryError, ChannelAdapterUnavailable) as exc:
        logger.warning("delivery_send_failed delivery_key=%s error=%s", body.delivery_key, exc)
        if dedupe is not None:
            await _release(dedupe, key, body.delivery_key)
        _count_delivery("failed")
        raise AppError(ErrorCode.COMMON_INTERNAL_ERROR) from exc
    except Exception as exc:
        # 官方 SDK 以任意异常回传发送失败（实测 RuntimeError: Reply ack error）；占位必须释放
        # 让 Worker 能重试，且不得宣称 accepted/delivered。
        logger.warning("delivery_send_error delivery_key=%s error=%s", body.delivery_key, type(exc).__name__)
        if dedupe is not None:
            await _release(dedupe, key, body.delivery_key)
        _count_delivery("failed")
        raise AppError(ErrorCode.COMMON_INTERNAL_ERROR) from exc


async def _is_delivered(dedupe: DedupeStore, key: str) -> bool:
    """占位≠送达：只有送达标记才算 delivered，占位中的重复必须回报未送达。"""
    try:
        return await dedupe.get_value(key) == DELIVERED_VALUE
    except DedupeStoreError as exc:
        logger.warning("delivery_dedupe_read_failed key=%s error=%s", key, exc)
        _count_delivery("failed")
        raise AppError(ErrorCode.COMMON_INTERNAL_ERROR) from exc


async def _release(dedupe: DedupeStore, key: str, delivery_key: str) -> None:
    try:
        await dedupe.release(key)
    except DedupeStoreError as exc:
        # 释放失败只能等占位 TTL 过期；此时不宣称已送达，Worker 会重试。
        logger.warning("delivery_dedupe_release_failed delivery_key=%s error=%s", delivery_key, exc)
