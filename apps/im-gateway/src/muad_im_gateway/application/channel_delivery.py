"""Run 终态交付的同键协调（design §3.6「机器人终态交付」；TASK-067）。

**同一稳定投递键**：`run:{run_id}:terminal` —— 与 Runtime 持久通知（TASK-063）和投递泵
（TASK-066）**同一个键**。活跃回复会话的收尾与兜底主动交付都按它 reserve/ack，实际发送成功
才确认；两条路径的真实发送因此至多发生一次（渠道确认丢失/外部无幂等能力时按至少一次设计）。

**协调走 API-18**（Runtime 是投递事实的 Owner；网关不持库，只做受信 HTTP 调用）：

- `reserve` 授出租约 ⇒ 本进程是这条投递的发送者，发送后按真实结局 ack；
- 已送达回执重放 ⇒ **不重发**（用户已经收到过），也不重写冻结的首次结果；
- 别人持有有效租约（Runtime 泵正在发）⇒ 不抢发，等它的结论；
- `UNKNOWN` 保留核实 ⇒ 不自动重发、不标送达；
- 没有持久出站意图（无通知行 / `NOT_REQUIRED`）⇒ 没有可协调的对象，按原语义直接发送
  （**不因此丢消息**）；
- 协调端口不可用 ⇒ 降级为直接发送（at-least-once；与 `RULE-13` 同一条口径：不能因为
  去重存储抖动把用户的答案吞掉）。

**ACK 丢失**：文本确实发出去了，但确认没落库 ⇒ 行留在 `DELIVERING`（attempt/租约可追溯，
不把占位当送达）；租约到期后同键重来被接管（epoch 前进）再次投递 —— 至少一次、可追溯。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from muad_api import AppError
from muad_contracts import (
    ChannelResolveRequest,
    ChannelResolveResponse,
    DeliveryMessage,
    DeliveryRouteInput,
)
from muad_contracts.channel_delivery import (
    DELIVERY_LEASE_DEFAULT_SEC,
    DeliveryAck,
    DeliveryAckOutcome,
    DeliveryAckReceipt,
    DeliveryLeaseGrant,
    DeliveryReservationRequest,
)
from pydantic import JsonValue

from ..channels.base import ActiveTextDelivery, ChannelAdapter, ChannelAdapterUnavailable
from .console_client import ConsoleClientPort

logger = logging.getLogger(__name__)

#: 终态交付结局词表（同时是 `im_final_delivery_total` 的状态标签）。
TerminalDeliveryStatus = Literal["delivered", "deduplicated", "failed"]
DELIVERED: TerminalDeliveryStatus = "delivered"
#: 已经送达过、或另一个尝试正持有投递资格：本次不发送。
DEDUPLICATED: TerminalDeliveryStatus = "deduplicated"
FAILED: TerminalDeliveryStatus = "failed"

#: 没有持久出站意图时的错误码（无入口归属的会话 / 网页持久入箱）：没有可协调的对象。
WITHOUT_INTENT_CODES = ("COMMON_NOT_FOUND", "COMMON_CONFLICT")


def terminal_delivery_key(run_id: str) -> str:
    """Run 终态的稳定交付键：与 Runtime 持久通知/投递泵**同一个键**（`run:{run_id}:terminal`）。"""
    return f"run:{run_id}:terminal"


class ChannelDeliveryProtocol(Protocol):
    """API-18 协调端口（受信发送方的 reserve/ack；`RuntimeClient` 满足）。"""

    async def reserve_delivery(
        self, request: DeliveryReservationRequest, *, tenant_id: str
    ) -> DeliveryLeaseGrant: ...

    async def ack_delivery(
        self, notification_id: UUID, body: DeliveryAck, *, tenant_id: str
    ) -> DeliveryAckReceipt: ...


class DeliveryPermissionPort(Protocol):
    """交付时的接收权限复核（设计 §3.6「接收者、原入口与 Agent 权限在交付时复核」）。"""

    async def may_deliver(
        self,
        route: DeliveryRouteInput,
        *,
        tenant_id: str,
        agent_id: UUID | None,
        platform_user_id: UUID | None,
    ) -> bool: ...


class TerminalDeliveryPort(Protocol):
    """Run 终态交付端口：活跃会话收尾（`confirm`）与兜底主动交付（`deliver`）。"""

    async def deliver(
        self,
        adapter: ChannelAdapter,
        route: DeliveryRouteInput,
        *,
        tenant_id: str,
        run_id: str,
        text: str,
        agent_id: UUID | None = None,
        platform_user_id: UUID | None = None,
    ) -> TerminalDeliveryStatus: ...

    async def confirm(self, *, tenant_id: str, run_id: str) -> TerminalDeliveryStatus: ...


@dataclass(frozen=True, slots=True)
class _Claim:
    """本次尝试的协调事实：租户 + 授出的租约 + 自己这次尝试的身份（确认的栅栏）。"""

    tenant_id: str
    grant: DeliveryLeaseGrant
    attempt_id: UUID


class ConsoleDeliveryPermission:
    """按**原入口**复核接收者与 Agent：解析不到、未授权或身份已变一律不放行。"""

    def __init__(self, console: ConsoleClientPort) -> None:
        self._console = console

    async def may_deliver(
        self,
        route: DeliveryRouteInput,
        *,
        tenant_id: str,
        agent_id: UUID | None,
        platform_user_id: UUID | None,
    ) -> bool:
        request = ChannelResolveRequest(
            channel=route.channel,
            bot_id=route.bot_id,
            external_user_id=route.external_user_id,
            external_conversation_id=route.external_conversation_id,
        )
        try:
            resolved = await self._console.resolve(request, tenant_id)
        except AppError as exc:
            logger.warning(
                "channel_delivery_resolve_failed channel=%s code=%s", route.channel, exc.code
            )
            return False
        return _same_receiver(
            route, resolved, agent_id=agent_id, platform_user_id=platform_user_id
        )


def _same_receiver(
    route: DeliveryRouteInput,
    resolved: ChannelResolveResponse,
    *,
    agent_id: UUID | None,
    platform_user_id: UUID | None,
) -> bool:
    """复核裁决：授权 + 接收者/Agent 与受理时**同一个**（变了就不发给别人）。"""
    if not resolved.authorized:
        logger.warning("channel_delivery_permission_denied channel=%s", route.channel)
        return False
    if agent_id is not None and resolved.agent_id != agent_id:
        logger.warning("channel_delivery_agent_changed channel=%s", route.channel)
        return False
    if platform_user_id is not None and resolved.platform_user_id != platform_user_id:
        logger.warning("channel_delivery_receiver_changed channel=%s", route.channel)
        return False
    return True


class ActiveChannelDelivery:
    """按稳定投递键协调的终态交付（协调端口缺失时降级为直接发送，绝不静默丢）。"""

    def __init__(
        self,
        protocol: ChannelDeliveryProtocol | None,
        *,
        permission: DeliveryPermissionPort | None = None,
        lease_sec: int = DELIVERY_LEASE_DEFAULT_SEC,
    ) -> None:
        if lease_sec < 1:
            raise ValueError("channel delivery lease must be positive")
        self._protocol = protocol
        self._permission = permission
        self._lease_sec = lease_sec

    async def deliver(
        self,
        adapter: ChannelAdapter,
        route: DeliveryRouteInput,
        *,
        tenant_id: str,
        run_id: str,
        text: str,
        agent_id: UUID | None = None,
        platform_user_id: UUID | None = None,
    ) -> TerminalDeliveryStatus:
        """兜底主动交付：reserve → 主动发送 → 实际发送成功才 ack。"""
        content = text.strip()
        if not content:
            logger.warning(
                "channel_delivery_empty_text run_id=%s channel=%s", run_id, route.channel
            )
            return FAILED
        if not isinstance(adapter, ActiveTextDelivery):
            # 渠道没有主动投递能力：**显式失败**。退化成"最新回调"会把答案挂到别人头上。
            logger.error(
                "channel_delivery_capability_missing run_id=%s channel=%s", run_id, route.channel
            )
            return FAILED
        permitted = await self._permitted(
            route, tenant_id=tenant_id, agent_id=agent_id, platform_user_id=platform_user_id
        )
        if not permitted:
            return FAILED
        claim = await self._reserve(tenant_id=tenant_id, run_id=run_id)
        if claim is None:
            return await self._send_uncoordinated(adapter, route, run_id=run_id, text=content)
        if _has_lease(claim.grant):
            return await self._send_and_ack(adapter, route, claim, run_id=run_id, text=content)
        return self._settle_grant(claim.grant, run_id=run_id, channel=route.channel)

    async def confirm(self, *, tenant_id: str, run_id: str) -> TerminalDeliveryStatus:
        """活跃回复会话的收尾：终态内容已写进会话流 ⇒ 同键认领并确认，阻止兜底重发。"""
        claim = await self._reserve(tenant_id=tenant_id, run_id=run_id)
        if claim is None:
            return DELIVERED
        if not _has_lease(claim.grant):
            return self._settle_grant(claim.grant, run_id=run_id, channel="")
        acknowledged = await self._ack(
            claim, outcome="DELIVERED", result=None, run_id=run_id, stage="confirm"
        )
        return DELIVERED if acknowledged else FAILED

    async def _permitted(
        self,
        route: DeliveryRouteInput,
        *,
        tenant_id: str,
        agent_id: UUID | None,
        platform_user_id: UUID | None,
    ) -> bool:
        if self._permission is None:
            return True
        return await self._permission.may_deliver(
            route,
            tenant_id=tenant_id,
            agent_id=agent_id,
            platform_user_id=platform_user_id,
        )

    async def _reserve(self, *, tenant_id: str, run_id: str) -> _Claim | None:
        """按稳定键预占；`None` = 没有可协调的对象（无意图 / 非出站意图 / 端口不可用）。"""
        protocol = self._protocol
        if protocol is None:
            logger.warning("channel_delivery_uncoordinated run_id=%s", run_id)
            return None
        attempt_id = uuid.uuid4()
        request = DeliveryReservationRequest(
            delivery_key=terminal_delivery_key(run_id),
            attempt_id=attempt_id,
            lease_sec=self._lease_sec,
        )
        try:
            grant = await protocol.reserve_delivery(request, tenant_id=tenant_id)
        except AppError as exc:
            if exc.code in WITHOUT_INTENT_CODES:
                # 没有持久出站意图（无入口归属的会话 / 网页持久入箱）：没有可协调的对象。
                logger.info("channel_delivery_without_intent run_id=%s code=%s", run_id, exc.code)
            else:
                logger.warning(
                    "channel_delivery_coordination_unavailable run_id=%s code=%s",
                    run_id,
                    exc.code,
                )
            return None
        return _Claim(tenant_id=tenant_id, grant=grant, attempt_id=attempt_id)

    def _settle_grant(
        self, grant: DeliveryLeaseGrant, *, run_id: str, channel: str
    ) -> TerminalDeliveryStatus:
        """未授租约：已送达回执 ⇒ 去重；别人在发 ⇒ 不抢发；核实保留 ⇒ 不自动重发。"""
        receipt = grant.receipt
        if receipt is not None and receipt.outcome == "DELIVERED":
            logger.info("channel_delivery_deduplicated run_id=%s", run_id)
            return DEDUPLICATED
        if grant.state == "DELIVERING":
            logger.info("channel_delivery_lease_held run_id=%s", run_id)
            return DEDUPLICATED
        logger.warning(
            "channel_delivery_not_granted run_id=%s state=%s channel=%s",
            run_id,
            grant.state,
            channel,
        )
        return FAILED

    async def _send_uncoordinated(
        self,
        adapter: ChannelAdapter,
        route: DeliveryRouteInput,
        *,
        run_id: str,
        text: str,
    ) -> TerminalDeliveryStatus:
        """没有可协调的对象：直接发送（at-least-once）。没送达就是失败，不宣称成功。"""
        if not await self._send(adapter, route, run_id=run_id, text=text):
            return FAILED
        return DELIVERED

    async def _send_and_ack(
        self,
        adapter: ChannelAdapter,
        route: DeliveryRouteInput,
        claim: _Claim,
        *,
        run_id: str,
        text: str,
    ) -> TerminalDeliveryStatus:
        """持有租约的发送：失败 ack `NOT_SENT`（回 PENDING 重投），成功 ack `DELIVERED`。"""
        if not await self._send(adapter, route, run_id=run_id, text=text):
            await self._ack(claim, outcome="NOT_SENT", result=None, run_id=run_id, stage="send")
            return FAILED
        await self._ack(
            claim,
            outcome="DELIVERED",
            result={"channel": route.channel},
            run_id=run_id,
            stage="send",
        )
        return DELIVERED

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
                "channel_delivery_failed run_id=%s channel=%s error=%s",
                run_id,
                route.channel,
                exc,
            )
            return False
        logger.info("channel_delivery_sent run_id=%s channel=%s", run_id, route.channel)
        return True

    async def _ack(
        self,
        claim: _Claim,
        *,
        outcome: DeliveryAckOutcome,
        result: dict[str, JsonValue] | None,
        run_id: str,
        stage: str,
    ) -> bool:
        """写确认；**确认丢失不改变"东西已发出去"这个事实**，但必须留痕（至少一次）。"""
        protocol = self._protocol
        if protocol is None:
            return False
        body = DeliveryAck(
            delivery_key=claim.grant.delivery_key,
            attempt_id=claim.attempt_id,
            epoch=claim.grant.lease_epoch,
            outcome=outcome,
            result=result,
        )
        try:
            await protocol.ack_delivery(
                claim.grant.notification_id, body, tenant_id=claim.tenant_id
            )
        except AppError as exc:
            logger.warning(
                "channel_delivery_ack_failed run_id=%s stage=%s outcome=%s code=%s",
                run_id,
                stage,
                outcome,
                exc.code,
            )
            return False
        return True


def _has_lease(grant: DeliveryLeaseGrant) -> bool:
    """`lease_epoch=0`/`lease_until=null` 表示本次没有授出租约（已送达重放/UNKNOWN 等）。"""
    return grant.lease_epoch > 0 and grant.lease_until is not None
