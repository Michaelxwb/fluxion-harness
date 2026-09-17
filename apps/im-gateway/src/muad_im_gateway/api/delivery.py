import logging
from typing import Any

from fastapi import APIRouter, Request
from muad_api import ApiResponse, AppError, ErrorCode, ok
from muad_contracts import DeliveryRequest

from ..channels.base import ChannelAdapterUnavailable, ChannelRegistryError
from ..infrastructure.dedupe import DedupeStoreError
from .deps import DedupeStoreDep, RegistryDep

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal/deliveries", tags=["delivery"])

DELIVERY_DEDUPE_PREFIX = "delivery:dedupe"
DELIVERY_DEDUPE_TTL_SEC = 604800


@router.post("")
async def deliver(
    body: DeliveryRequest,
    request: Request,
    registry: RegistryDep,
    dedupe: DedupeStoreDep,
) -> ApiResponse[Any]:
    key = f"{DELIVERY_DEDUPE_PREFIX}:{body.delivery_key}"
    try:
        if await dedupe.exists(key):
            return ok(
                request.app.state.message_catalog,
                {"accepted": True, "duplicate": True},
            )
    except DedupeStoreError as exc:
        logger.warning("delivery_dedupe_failed delivery_key=%s error=%s", body.delivery_key, exc)
        raise AppError(ErrorCode.COMMON_INTERNAL_ERROR) from exc
    try:
        adapter = registry.get(body.route.channel)
        await adapter.send(body.route, body.message)
    except (ChannelRegistryError, ChannelAdapterUnavailable) as exc:
        logger.warning("delivery_send_failed delivery_key=%s error=%s", body.delivery_key, exc)
        raise AppError(ErrorCode.COMMON_INTERNAL_ERROR) from exc
    try:
        await dedupe.mark(key, DELIVERY_DEDUPE_TTL_SEC)
    except DedupeStoreError as exc:
        logger.warning("delivery_dedupe_mark_failed delivery_key=%s error=%s", body.delivery_key, exc)
    return ok(
        request.app.state.message_catalog,
        {"accepted": True, "duplicate": False},
    )
