import asyncio
import hashlib
import json
import logging
import time
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Request
from muad_api import ApiResponse, AppError, ErrorCode, InternalServiceDep, metrics, ok
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
from ..infrastructure.artifact_resolver import ArtifactGoneError, ArtifactResolverPort
from ..infrastructure.dedupe import (
    DedupeStore,
    DedupeStoreError,
    delivered_value,
    parse_delivered,
)
from .deps import (
    ArtifactResolverDep,
    ConsoleClientDep,
    DedupeStoreDep,
    GatewayTenantDep,
    RegistryDep,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal/deliveries", tags=["delivery"])

#: 幂等指纹里的 endpoint 判别键（`RULE-api-002`）：同 key 换端点必须各算各的。
DELIVERY_ENDPOINT = "/internal/deliveries"
DELIVERY_DEDUPE_PREFIX = "delivery:dedupe"
DELIVERY_DEDUPE_TTL_SEC = 604800
# 占位（in-flight）TTL 远短于送达标记：进程在占位后崩溃时占位会自动过期，
# 允许 Worker 的重试真正补发，而不是被占位永久挡住。
DELIVERY_IN_FLIGHT_TTL_SEC = 30
# 长发送期间的续租节拍：上传大文件可能比占位 TTL 还长，不续租就等于放弃去重。
# 取 TTL 的 1/3 —— 偶发的一次失败不至于让占位在发送途中过期。
DELIVERY_IN_FLIGHT_RENEW_SEC = 10.0
# 已在处理中（占位存在但尚无成功键）时的有界等待：超时回可重试错误，不把占位当成功。
DELIVERY_IN_FLIGHT_WAIT_SEC = 2.0
BACKGROUND_DELIVERY_METRIC = "im_background_delivery_total"
DELIVERY_IN_FLIGHT_POLL_SEC = 0.1


@dataclass(frozen=True, slots=True)
class _Reservation:
    """本次投递的占位：键 + **所有者 token**。

    释放与升级都以 token 为条件（`infrastructure/dedupe.py` 的头注写了为什么）——过期的旧
    主人不能删掉新主人的成功标记。
    """

    key: str
    owner: str


#: 占位句柄：存储 + 占位本身。发送路径只在持有它时才允许改这个键。
_Held = tuple[DedupeStore, _Reservation]


@router.post("")
async def deliver(
    body: DeliveryRequest,
    request: Request,
    registry: RegistryDep,
    dedupe: DedupeStoreDep,
    console: ConsoleClientDep,
    gateway_tenant: GatewayTenantDep,
    resolver: ArtifactResolverDep,
    _internal: InternalServiceDep,
) -> ApiResponse[Any]:
    """受信服务（Runtime / Worker）投递一条消息给终端用户。

    **三重校验**（2026-10-06 加固，此前该端点无鉴权且完全相信请求体）：
    ① 服务身份 `X-Internal-Service`（与 `resolve-credentials` 等内部端点同门控）；
    ② 租户必须是**本网关部署的租户**（网关按单租户部署，一个 `bot_id` 只属于一个租户）；
    ③ 产物必须能按 `artifact_id` 在**本租户内**解析出来，且存储键与调用方声明一致
    —— 调用方不能自行提供一个未经验证的存储键去读共享存储里的任意文件。
    """
    key = f"{DELIVERY_DEDUPE_PREFIX}:{body.delivery_key}"
    trace_id = current_trace_id() or ""
    _require_gateway_tenant(body, gateway_tenant)
    fingerprint = _fingerprint(body)
    try:
        owner = await dedupe.reserve(key, DELIVERY_IN_FLIGHT_TTL_SEC)
    except DedupeStoreError as exc:
        # RULE-13：Redis 运行期不可用时降级为 at-least-once（可能重复），不能因此停发。
        logger.warning(
            "delivery_dedupe_degraded delivery_key=%s error=%s", body.delivery_key, exc
        )
        outcome = await _send_audited(
            registry, console, body, resolver=resolver, held=None, trace_id=trace_id
        )
        _count_delivery("accepted")
        return ok(request.app.state.message_catalog, _result(outcome))
    if owner is None:
        return await _replay(request, dedupe, key, fingerprint)
    held: _Held = (dedupe, _Reservation(key=key, owner=owner))
    renew = asyncio.create_task(_keep_placeholder(dedupe, key, owner))
    try:
        outcome = await _send_audited(
            registry,
            console,
            body,
            resolver=resolver,
            held=held,
            trace_id=trace_id,
        )
    finally:
        renew.cancel()
        with suppress(asyncio.CancelledError):
            await renew
    record = {
        "fingerprint": fingerprint,
        "outcome": outcome.outcome if outcome is not None else None,
        "fallback_url": outcome.fallback_url if outcome is not None else None,
    }
    try:
        # CAS 升级：占位已被别人接管（我们发得太久、占位过期）时这一步不改键，只留痕
        # —— 东西确实发出去了，如实回报成功；但去重记录可能已落到别的请求名下。
        if not await dedupe.mark(key, owner, delivered_value(record), DELIVERY_DEDUPE_TTL_SEC):
            logger.warning("delivery_dedupe_mark_lost_ownership delivery_key=%s", body.delivery_key)
    except DedupeStoreError as exc:
        # 已真实发送：保留短 TTL 占位，最坏情况按 at-least-once 重复投递。
        logger.warning("delivery_dedupe_mark_failed delivery_key=%s error=%s", body.delivery_key, exc)
    _count_delivery("accepted")
    return ok(request.app.state.message_catalog, _result(outcome))


def _require_gateway_tenant(body: DeliveryRequest, gateway_tenant: str) -> None:
    """投递声明的租户必须是本部署的租户：否则这条投递是在借本网关去够别人的数据。"""
    if body.tenant_id != gateway_tenant:
        logger.warning(
            "delivery_tenant_mismatch delivery_key=%s declared=%s gateway=%s",
            body.delivery_key,
            body.tenant_id,
            gateway_tenant,
        )
        raise AppError(ErrorCode.FORBIDDEN)


def _fingerprint(body: DeliveryRequest) -> str:
    """请求指纹（`RULE-api-002`）：规范化 JSON 的 SHA256，**含 endpoint 与 tenant_id**。

    指纹取的是**交付身份**——租户、交付键、路由、载荷形态、产物——**不含正文文本**。
    正文是任务/模型产出的**再渲染**：Worker 每次尝试都按当下的平台设置默认语言重新渲染
    （`build_delivery_message(task, locale)`），语言一变，同一任务同一个 delivery_key 就有
    了不同的文案。把正文算进指纹，会让一次**无害的重试**变成不可重试的 `409`（投递永久失败）。

    而"同键换了收件人 / 换了产物"是实打实的另一个请求：那种情况必须报
    `IDEMPOTENCY_MISMATCH`，而不是拿上一次的成功冒充这一次。
    """
    artifact = body.message.artifact
    identity = {
        "endpoint": DELIVERY_ENDPOINT,
        "tenant_id": body.tenant_id,
        "delivery_key": body.delivery_key,
        "route": body.route.model_dump(mode="json"),
        "message_type": body.message.type,
        "artifact_ids": sorted(str(value) for value in body.artifact_ids),
        "artifact_id": str(artifact.artifact_id) if artifact is not None else None,
        "artifact_storage_key": artifact.storage_key if artifact is not None else None,
    }
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _keep_placeholder(dedupe: DedupeStore, key: str, owner: str) -> None:
    """发送期间把占位续租住（有界循环，调用方在发送结束时取消它）。

    失去所有权（占位过期且已被新请求接管）即退出：此时再改这个键就是覆盖别人的状态。
    Redis 抖动只记日志并继续 —— 续租失败不该让已经开始的发送失败，最坏退化成 at-least-once。
    """
    while True:
        await asyncio.sleep(DELIVERY_IN_FLIGHT_RENEW_SEC)
        try:
            if not await dedupe.renew(key, owner, DELIVERY_IN_FLIGHT_TTL_SEC):
                logger.warning("delivery_placeholder_lost key=%s", key)
                return
        except DedupeStoreError as exc:
            logger.warning("delivery_placeholder_renew_failed key=%s error=%s", key, exc)


async def _send_audited(
    registry: ChannelRegistry,
    console: ConsoleClientDep,
    body: DeliveryRequest,
    *,
    resolver: ArtifactResolverPort,
    held: _Held | None,
    trace_id: str,
) -> ArtifactDeliveryOutcome | None:
    """发送 + 写交付审计（设计 §3.6 跳 5）。

    **审计写不进去不阻断已完成的交付**——东西已经送到用户手里了，为了一条记录去回滚送达
    是把事情做反了；但必须留 ERROR 痕迹（"交付过却查不到"比失败更难查）。
    """
    try:
        outcome = await _send(registry, body, resolver=resolver, held=held)
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


async def _replay(
    request: Request, dedupe: DedupeStore, key: str, fingerprint: str
) -> ApiResponse[Any]:
    """已存在占位/成功键：仅成功键算成功；只有占位时有界等待，超时报可重试错误。

    成功键里存着**首次交付的结果**（`outcome`/`fallback_url`）：调用方那一次没收到响应时，
    重放必须能拿回"用户实际收到了什么"——降级链接只在那一次响应里出现过，丢了就永远查不到。
    """
    deadline = time.monotonic() + DELIVERY_IN_FLIGHT_WAIT_SEC
    record = await _read_record(dedupe, key)
    while record is None:
        if time.monotonic() >= deadline:
            logger.warning("delivery_in_flight_timeout key=%s", key)
            _count_delivery("failed")
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
        await asyncio.sleep(DELIVERY_IN_FLIGHT_POLL_SEC)
        record = await _read_record(dedupe, key)
    stored = record.get("fingerprint")
    if isinstance(stored, str) and stored != fingerprint:
        # 同 key 不同请求：不能拿上一次的成功冒充这一次
        logger.warning("delivery_idempotency_mismatch key=%s", key)
        raise AppError(ErrorCode.IDEMPOTENCY_MISMATCH)
    _count_delivery("deduplicated")
    payload: dict[str, Any] = {
        "accepted": True,
        "duplicate": True,
        "delivered": True,
        "deduplicated": True,
    }
    outcome = record.get("outcome")
    if isinstance(outcome, str) and outcome:
        payload["outcome"] = outcome
        payload["fallback_url"] = record.get("fallback_url")
    return ok(request.app.state.message_catalog, payload)


async def _read_record(dedupe: DedupeStore, key: str) -> dict[str, object] | None:
    """读送达标记；**占位不算送达**（返回 None，调用方继续有界等待）。"""
    try:
        value = await dedupe.get_value(key)
    except DedupeStoreError as exc:
        logger.warning("delivery_dedupe_read_failed key=%s error=%s", key, exc)
        _count_delivery("failed")
        raise AppError(ErrorCode.COMMON_INTERNAL_ERROR) from exc
    return parse_delivered(value)


async def _send(
    registry: ChannelRegistry,
    body: DeliveryRequest,
    *,
    resolver: ArtifactResolverPort,
    held: _Held | None,
) -> ArtifactDeliveryOutcome | None:
    """真实发送；失败时释放占位（若有）让调用方重试，未送达不宣称成功。

    两个形态共用一个出口：文本走既有的 `adapter.send`，产物走**可选出站能力**。
    产物在发送前先做**归属解析**（见 `deliver` 的文档串第 ③ 条）。
    """
    try:
        verified = await _verified_body(body, resolver)
        adapter = registry.get(verified.route.channel)
        outcome = await _deliver(adapter, verified)
    except ArtifactGoneError as exc:
        logger.warning("delivery_artifact_unresolved delivery_key=%s error=%s", body.delivery_key, exc)
        await _release(held, body.delivery_key)
        _count_delivery("failed")
        raise AppError(ErrorCode.COMMON_NOT_FOUND) from exc
    except ChannelBotNotFound as exc:
        # 未配置/已停用 bot：与"暂时不可用"区分（设计 API-05 错误码）
        logger.warning("delivery_bot_not_found delivery_key=%s error=%s", body.delivery_key, exc)
        await _release(held, body.delivery_key)
        _count_delivery("failed")
        raise AppError(ErrorCode.BOT_NOT_FOUND) from exc
    except ArtifactDeliveryError as exc:
        # 渠道侧发不出去（含"本通道根本没有发产物的能力"）：显式失败 + 释放占位。
        # **不得静默丢**——静默丢等于告诉调用方"发成功了"。
        logger.warning("delivery_artifact_failed delivery_key=%s code=%s", body.delivery_key, exc.code)
        await _release(held, body.delivery_key)
        _count_delivery("failed")
        raise AppError(ErrorCode.ARTIFACT_DELIVERY_FAILED) from exc
    except (ChannelRegistryError, ChannelAdapterUnavailable) as exc:
        logger.warning("delivery_send_failed delivery_key=%s error=%s", body.delivery_key, exc)
        await _release(held, body.delivery_key)
        _count_delivery("failed")
        raise AppError(ErrorCode.COMMON_INTERNAL_ERROR) from exc
    except Exception as exc:
        # 官方 SDK 以任意异常回传发送失败（实测 RuntimeError: Reply ack error）；占位必须释放
        # 让 Worker 能重试，且不得宣称 accepted/delivered。
        logger.warning("delivery_send_error delivery_key=%s error=%s", body.delivery_key, type(exc).__name__)
        await _release(held, body.delivery_key)
        _count_delivery("failed")
        raise AppError(ErrorCode.COMMON_INTERNAL_ERROR) from exc
    return outcome


async def _verified_body(body: DeliveryRequest, resolver: ArtifactResolverPort) -> DeliveryRequest:
    """产物形态投递的**归属校验**：按 `artifact_id` 在本租户内解析，用解析结果覆盖调用方声明。

    存储键从此**只有一个来源**（Runtime 的解析端点，与取件直链、Console 取件走同一处口径）；
    调用方给的那份只用来**比对**——不一致说明它要么搞错了自己发的是什么，要么在指一件它没有
    权利交付的东西。文本形态原样返回（没有产物就没有可校验的东西）。
    """
    message = body.message
    if message.type == "text":
        return body
    artifact = message.artifact
    if artifact is None or artifact.artifact_id is None:
        # 契约允许 artifact_id 为空（入站方向就没有），但**交付方向必须有**：没有它就无法
        # 证明这份字节属于本租户，而降级取件链接也拼不出来。
        raise ArtifactGoneError("artifact delivery requires artifact_id")
    resolved = await resolver.resolve(artifact.artifact_id, tenant_id=body.tenant_id)
    if resolved.storage_key != artifact.storage_key:
        logger.warning(
            "delivery_artifact_storage_key_mismatch delivery_key=%s artifact_id=%s",
            body.delivery_key,
            artifact.artifact_id,
        )
        raise ArtifactGoneError("declared storage_key does not belong to the artifact")
    # 用**解析出来的**引用往下走：存储键、类型、文件名从此只有一个来源（Runtime 的解析端点）
    return body.model_copy(update={"message": message.model_copy(update={"artifact": resolved})})


async def _deliver(
    adapter: ChannelAdapter, body: DeliveryRequest
) -> ArtifactDeliveryOutcome | None:
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


async def _release(held: _Held | None, delivery_key: str) -> None:
    """释放**自己的**占位（CAS）：占位已过期并被别人接管时什么都不做。"""
    if held is None:
        return
    dedupe, reservation = held
    try:
        await dedupe.release(reservation.key, reservation.owner)
    except DedupeStoreError as exc:
        # 释放失败只能等占位 TTL 过期；此时不宣称已送达，Worker 会重试。
        logger.warning("delivery_dedupe_release_failed delivery_key=%s error=%s", delivery_key, exc)
