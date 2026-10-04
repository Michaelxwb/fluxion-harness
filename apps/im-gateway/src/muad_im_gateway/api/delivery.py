import asyncio
import logging
import time
from typing import Any

from fastapi import APIRouter, Request
from muad_api import ApiResponse, AppError, ErrorCode, metrics, ok
from muad_api.context import current_trace_id
from muad_contracts import (
    ArtifactDeliveryAuditRequest,
    DeliveryAuditOutcome,
    DeliveryRequest,
)

from ..channels.base import (
    ArtifactDeliveryError,
    ArtifactDeliveryOutcome,
    ChannelAdapter,
    ChannelAdapterUnavailable,
    ChannelBotNotFound,
    ChannelRegistry,
    ChannelRegistryError,
    OutboundArtifactDelivery,
)
from ..infrastructure.dedupe import DELIVERED_VALUE, DedupeStore, DedupeStoreError
from .deps import ConsoleClientDep, DedupeStoreDep, RegistryDep

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
    console: ConsoleClientDep,
) -> ApiResponse[Any]:
    key = f"{DELIVERY_DEDUPE_PREFIX}:{body.delivery_key}"
    trace_id = current_trace_id() or ""
    try:
        reserved = await dedupe.reserve(key, DELIVERY_IN_FLIGHT_TTL_SEC)
    except DedupeStoreError as exc:
        # RULE-13：Redis 运行期不可用时降级为 at-least-once（可能重复），不能因此停发。
        logger.warning(
            "delivery_dedupe_degraded delivery_key=%s error=%s", body.delivery_key, exc
        )
        outcome = await _send_audited(
            registry, console, body, dedupe=None, key=key, trace_id=trace_id
        )
        _count_delivery("accepted")
        return ok(request.app.state.message_catalog, _result(outcome))
    if not reserved:
        return await _replay(request, dedupe, key)
    outcome = await _send_audited(
        registry, console, body, dedupe=dedupe, key=key, trace_id=trace_id
    )
    try:
        await dedupe.mark(key, DELIVERY_DEDUPE_TTL_SEC)
    except DedupeStoreError as exc:
        # 已真实发送：保留短 TTL 占位，最坏情况按 at-least-once 重复投递。
        logger.warning("delivery_dedupe_mark_failed delivery_key=%s error=%s", body.delivery_key, exc)
    _count_delivery("accepted")
    return ok(request.app.state.message_catalog, _result(outcome))


async def _send_audited(
    registry: ChannelRegistry,
    console: ConsoleClientDep,
    body: DeliveryRequest,
    *,
    dedupe: DedupeStore | None,
    key: str,
    trace_id: str,
) -> ArtifactDeliveryOutcome | None:
    """发送 + 写交付审计（设计 §3.6 跳 5）。

    **审计写不进去不阻断已完成的交付**——东西已经送到用户手里了，为了一条记录去回滚送达
    是把事情做反了；但必须留 ERROR 痕迹（"交付过却查不到"比失败更难查）。
    """
    try:
        outcome = await _send(registry, body, dedupe=dedupe, key=key)
    except AppError as exc:
        # 失败也要留痕：这正是 E-06 断言"审计记 FAILED"的来源
        await _audit_delivery(
            registry, console, body, outcome="FAILED",
            reason_code=str(exc.code), trace_id=trace_id,
        )
        raise
    await _audit_delivery(
        registry,
        console,
        body,
        outcome=outcome.outcome if outcome is not None else "DELIVERED",
        reason_code=outcome.reason_code if outcome is not None else "",
        trace_id=trace_id,
    )
    return outcome


async def _audit_delivery(
    registry: ChannelRegistry,
    console: ConsoleClientDep,
    body: DeliveryRequest,
    *,
    outcome: DeliveryAuditOutcome,
    reason_code: str,
    trace_id: str,
) -> None:
    """写一条交付审计。**只对产物形态**——审计表要求 `artifact_id`，而文本投递没有
    "把哪个产物交付给哪个路由"这件事可言，硬记一行只会把那张表灌满噪声。

    `route_key` **由适配器产出**（渠道私有形状不进应用层）：适配器拿不到就跳过并留痕，
    不自己拼一个"看起来像"的串。
    """
    artifact = body.message.artifact
    if body.message.type == "text" or artifact is None or artifact.artifact_id is None:
        return
    try:
        adapter = registry.get(body.route.channel)
    except (ChannelRegistryError, ChannelBotNotFound):
        logger.warning("delivery_audit_skipped_no_adapter delivery_key=%s", body.delivery_key)
        return
    if not isinstance(adapter, OutboundArtifactDelivery):
        logger.warning("delivery_audit_skipped_no_capability delivery_key=%s", body.delivery_key)
        return
    try:
        await console.delivery_audit(
            ArtifactDeliveryAuditRequest(
                artifact_id=artifact.artifact_id,
                channel=body.route.channel,
                route_key=adapter.route_key(body.route),
                delivery_key=body.delivery_key,
                outcome=outcome,
                reason_code=reason_code,
                trace_id=trace_id or None,
            ),
            body.tenant_id,
        )
    except AppError as exc:
        logger.error(
            "delivery_audit_write_failed delivery_key=%s code=%s", body.delivery_key, exc.code
        )


def _result(outcome: ArtifactDeliveryOutcome | None) -> dict[str, Any]:
    """投递响应体。文本形态没有 `outcome` 可言，产物形态才有——所以是按需加字段而不是恒填。"""
    payload: dict[str, Any] = {
        "accepted": True,
        "duplicate": False,
        "delivered": True,
        "deduplicated": False,
    }
    if outcome is not None:
        payload["outcome"] = outcome.outcome
        payload["fallback_url"] = outcome.fallback_url
    return payload


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
) -> ArtifactDeliveryOutcome | None:
    """真实发送；失败时释放占位（若有）让调用方重试，未送达不宣称成功。

    两个形态共用一个出口：文本走既有的 `adapter.send`，产物走**可选出站能力**。
    """
    try:
        adapter = registry.get(body.route.channel)
        outcome = await _deliver(adapter, body)
    except ChannelBotNotFound as exc:
        # 未配置/已停用 bot：与"暂时不可用"区分（设计 API-05 错误码）
        logger.warning("delivery_bot_not_found delivery_key=%s error=%s", body.delivery_key, exc)
        if dedupe is not None:
            await _release(dedupe, key, body.delivery_key)
        _count_delivery("failed")
        raise AppError(ErrorCode.BOT_NOT_FOUND) from exc
    except ArtifactDeliveryError as exc:
        # 渠道侧发不出去（含"本通道根本没有发产物的能力"）：显式失败 + 释放占位。
        # **不得静默丢**——静默丢等于告诉调用方"发成功了"。
        logger.warning("delivery_artifact_failed delivery_key=%s code=%s", body.delivery_key, exc.code)
        if dedupe is not None:
            await _release(dedupe, key, body.delivery_key)
        _count_delivery("failed")
        raise AppError(ErrorCode.ARTIFACT_DELIVERY_FAILED) from exc
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
    return outcome


async def _deliver(adapter: ChannelAdapter, body: DeliveryRequest) -> ArtifactDeliveryOutcome | None:
    """按载荷形态分发。**渠道差异只活在适配器里**（RULE-im-002）：这里只问"你能不能发"。

    适配器没实现 `OutboundArtifactDelivery` = 本通道不会发产物 —— **显式失败**而不是悄悄
    换成一段文字（那会让用户以为文件发了）。降级成取件链接是**适配器内部**的决定（它会返回
    `DEGRADED` + `fallback_url`），不是这里的兜底。
    """
    message = body.message
    if message.type == "text":
        await adapter.send(body.route, message)
        return None
    artifact = message.artifact
    if artifact is None:  # 契约已保证 type != text 时 artifact 必填；这里是纵深防御
        raise ArtifactDeliveryError("ARTIFACT_DELIVERY_FAILED")
    if not isinstance(adapter, OutboundArtifactDelivery):
        raise ArtifactDeliveryError("ARTIFACT_DELIVERY_FAILED")
    return await adapter.deliver_artifact(
        body.route, artifact, tenant_id=body.tenant_id, run_id=body.run_id
    )


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
