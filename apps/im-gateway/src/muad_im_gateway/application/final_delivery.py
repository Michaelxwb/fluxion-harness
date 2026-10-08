"""Run 终态文本的渠道中立主动投递：ReplySession 失效时的兜底（稳定投递键）。

投递键固定为 `run:{run_id}:final`：同一 Run 的终态文本至多**真正发一次**（进程内重试、
崩溃后重来都由这个键兜底），只有明确送达才算成功。渠道能力（`ActiveTextDelivery`）不存在
时显式失败，绝不把文本塞回"最新回调"或回报"已送达"。

与 `/internal/deliveries` 共用 `delivery:dedupe:{delivery_key}` 键空间与送达记录形状：
投递身份（租户/交付键/路由/文本形态）参与指纹，正文不参与 —— 重试不因重渲染而 409。
"""

from __future__ import annotations

import hashlib
import logging
from typing import Protocol

from muad_contracts import DeliveryMessage, DeliveryRouteInput, canonical_json

from ..channels.base import ActiveTextDelivery, ChannelAdapter, ChannelAdapterUnavailable
from ..infrastructure.dedupe import (
    DedupeStore,
    DedupeStoreError,
    delivered_value,
    parse_delivered,
)

logger = logging.getLogger(__name__)

#: 与 `api/delivery.py` 同一端点判别键：同一个投递键在两条路径下算同一个幂等身份。
FINAL_DELIVERY_ENDPOINT = "/internal/deliveries"
FINAL_DELIVERY_DEDUPE_PREFIX = "delivery:dedupe"
FINAL_DELIVERY_TTL_SEC = 604800
#: 占位 TTL 远短于送达标记：进程中途崩溃时占位自动过期，允许重试真正补发。
FINAL_DELIVERY_IN_FLIGHT_SEC = 30


class FinalDeliveryPort(Protocol):
    async def deliver(
        self,
        adapter: ChannelAdapter,
        route: DeliveryRouteInput,
        *,
        tenant_id: str,
        run_id: str,
        text: str,
    ) -> bool: ...


def final_delivery_key(run_id: str) -> str:
    return f"run:{run_id}:final"


class ActiveFinalDelivery:
    """按稳定投递键把 Run 终态文本主动发到会话；返回是否**确认送达**。"""

    def __init__(self, dedupe: DedupeStore, *, ttl_sec: int = FINAL_DELIVERY_TTL_SEC) -> None:
        self._dedupe = dedupe
        self._ttl_sec = ttl_sec

    async def deliver(
        self,
        adapter: ChannelAdapter,
        route: DeliveryRouteInput,
        *,
        tenant_id: str,
        run_id: str,
        text: str,
    ) -> bool:
        content = text.strip()
        if not content:
            return False
        if not isinstance(adapter, ActiveTextDelivery):
            # 渠道没有主动投递能力：**显式失败**。退化成"最新回调"会把答案挂到别人头上。
            logger.error(
                "final_delivery_capability_missing run_id=%s channel=%s", run_id, route.channel
            )
            return False
        delivery_key = final_delivery_key(run_id)
        storage_key = f"{FINAL_DELIVERY_DEDUPE_PREFIX}:{delivery_key}"
        fingerprint = _fingerprint(tenant_id=tenant_id, delivery_key=delivery_key, route=route)
        try:
            owner = await self._dedupe.reserve(storage_key, FINAL_DELIVERY_IN_FLIGHT_SEC)
        except DedupeStoreError as exc:
            # 去重存储不可用：降级为 at-least-once（可能重复），不因此丢终态文本。
            logger.warning("final_delivery_dedupe_degraded run_id=%s error=%s", run_id, exc)
            return await self._send(adapter, route, run_id=run_id, text=content)
        if owner is None:
            return await self._already_delivered(storage_key, fingerprint, run_id)
        sent = await self._send(adapter, route, run_id=run_id, text=content)
        if not sent:
            await self._release(storage_key, owner)
            return False
        record = {"fingerprint": fingerprint, "outcome": None, "fallback_url": None}
        try:
            await self._dedupe.mark(
                storage_key, owner, delivered_value(record), self._ttl_sec
            )
        except DedupeStoreError as exc:
            # 已经真实发出去了，如实回报成功；最坏按 at-least-once 重复。
            logger.warning("final_delivery_dedupe_mark_failed run_id=%s error=%s", run_id, exc)
        return True

    async def _send(
        self,
        adapter: ChannelAdapter,
        route: DeliveryRouteInput,
        *,
        run_id: str,
        text: str,
    ) -> bool:
        assert isinstance(adapter, ActiveTextDelivery)
        try:
            await adapter.send_active(route, DeliveryMessage(text=text))
        except ChannelAdapterUnavailable as exc:
            logger.error(
                "final_delivery_failed run_id=%s channel=%s error=%s", run_id, route.channel, exc
            )
            return False
        logger.info("final_delivery_sent run_id=%s channel=%s", run_id, route.channel)
        return True

    async def _already_delivered(
        self, storage_key: str, fingerprint: str, run_id: str
    ) -> bool:
        try:
            value = await self._dedupe.get_value(storage_key)
        except DedupeStoreError as exc:
            logger.warning("final_delivery_dedupe_read_failed run_id=%s error=%s", run_id, exc)
            return False
        record = parse_delivered(value)
        if record is None:
            # 只有占位（另一次投递在飞/已过期）：不把占位当送达。
            logger.warning("final_delivery_in_flight run_id=%s", run_id)
            return False
        stored = record.get("fingerprint")
        if isinstance(stored, str) and stored != fingerprint:
            logger.warning("final_delivery_identity_mismatch run_id=%s", run_id)
            return False
        logger.info("final_delivery_deduplicated run_id=%s", run_id)
        return True

    async def _release(self, storage_key: str, owner: str) -> None:
        try:
            await self._dedupe.release(storage_key, owner)
        except DedupeStoreError as exc:
            logger.warning("final_delivery_dedupe_release_failed error=%s", exc)


def _fingerprint(*, tenant_id: str, delivery_key: str, route: DeliveryRouteInput) -> str:
    """文本形态的交付身份指纹（与 `api/delivery.py::_fingerprint` 同构、**不含正文**）。"""
    identity: dict[str, object] = {
        "endpoint": FINAL_DELIVERY_ENDPOINT,
        "tenant_id": tenant_id,
        "delivery_key": delivery_key,
        "route": route.model_dump(mode="json"),
        "message_type": "text",
        "artifact_ids": [],
        "artifact_id": None,
        "artifact_storage_key": None,
    }
    return hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()
