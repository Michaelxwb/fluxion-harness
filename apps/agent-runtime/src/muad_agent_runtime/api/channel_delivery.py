"""API-18：Runtime 内部出站投递端口（design §3.4）。

- 只有受信服务身份（`require_internal_service`）可达：Runtime 投递泵、Gateway 活跃回复的
  收尾/兜底交付、Worker 结果投递都走**同一套 PG 状态转换**（本模块不重实现状态机）；
- **reserve 按稳定 `delivery_key`**（发送方唯一稳定持有的投递身份，如
  `run:{run_id}:terminal`）；响应回 `notification_id`，renew/ack 用 `{id}` 路径并在请求体里
  用 `delivery_key` 复核身份，`attempt_id`/`epoch` 是租约栅栏；
- 设置快照（`NOT_SENT` 退避基数、回执重放保留期）在**事务外**取一次；源不可读即显式失败，
  不回落默认值；
- 响应只给栅栏事实与冻结的首次结果：不含通知正文、接收者、入口或渠道细节。
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from muad_api import ApiResponse, ok
from muad_api.security import require_internal_service
from muad_contracts.channel_delivery import (
    DeliveryAck,
    DeliveryAckReceipt,
    DeliveryLeaseGrant,
    DeliveryLeaseRenewal,
    DeliveryReceiptPayload,
    DeliveryReservationRequest,
)

from ..application.channel_delivery import (
    ChannelDeliveryService,
    DeliveryLease,
    DeliveryReceipt,
    delivery_policy_from_settings,
)
from ..application.ports import PlatformSettingsClient
from ..infrastructure.db import SessionFactoryProvider, get_session_factory
from .deps import PlatformSettingsClientDep, TenantDep

router = APIRouter(
    prefix="/internal/channel-notifications",
    tags=["channel-notifications"],
    dependencies=[Depends(require_internal_service)],
)


class ChannelDeliveryPort:
    """API-18 的受信适配层：线协议 → 投递服务；设置快照在事务外取一次。"""

    def __init__(
        self, session_factory_provider: SessionFactoryProvider, settings_client: PlatformSettingsClient
    ) -> None:
        self._sessions = session_factory_provider
        self._settings = settings_client

    async def reserve(
        self, *, tenant_id: str, body: DeliveryReservationRequest
    ) -> DeliveryLeaseGrant:
        """预占：按稳定 delivery_key 取行；已送达重放首次结果，UNKNOWN 保留核实。"""
        service = await self._service(tenant_id)
        lease = await service.reserve(
            tenant_id=tenant_id,
            delivery_key=body.delivery_key,
            attempt_id=body.attempt_id,
            lease_sec=body.lease_sec,
        )
        return _grant(lease)

    async def renew(
        self, *, tenant_id: str, notification_id: uuid.UUID, body: DeliveryLeaseRenewal
    ) -> DeliveryLeaseGrant:
        """续租：核 attempt+epoch 且租约仍有效；失约即停手（`DELIVERY_LEASE_LOST`）。"""
        service = await self._service(tenant_id)
        lease = await service.renew(
            tenant_id=tenant_id,
            notification_id=notification_id,
            delivery_key=body.delivery_key,
            attempt_id=body.attempt_id,
            epoch=body.epoch,
            lease_sec=body.lease_sec,
        )
        return _grant(lease)

    async def ack(
        self, *, tenant_id: str, notification_id: uuid.UUID, body: DeliveryAck
    ) -> DeliveryAckReceipt:
        """确认：只有实际发送成功才写 DELIVERED；重复 ack 回放首次结果。"""
        service = await self._service(tenant_id)
        confirmation = await service.ack(
            tenant_id=tenant_id,
            notification_id=notification_id,
            delivery_key=body.delivery_key,
            attempt_id=body.attempt_id,
            epoch=body.epoch,
            outcome=body.outcome,
            result=body.result,
        )
        return DeliveryAckReceipt(
            notification_id=confirmation.notification_id,
            delivery_key=confirmation.delivery_key,
            state=confirmation.state,
            attempt=confirmation.attempt,
            reused=confirmation.reused,
            receipt=_receipt(confirmation.receipt),
        )

    async def _service(self, tenant_id: str) -> ChannelDeliveryService:
        """每次操作取一次设置快照（HTTP 在事务外）再构造服务：坏源显式失败，不回落默认。"""
        snapshot = await self._settings.fetch_snapshot(tenant_id=tenant_id)
        return ChannelDeliveryService(
            self._sessions, delivery_policy_from_settings(snapshot.settings)
        )


def _receipt(receipt: DeliveryReceipt | None) -> DeliveryReceiptPayload | None:
    if receipt is None:
        return None
    return DeliveryReceiptPayload(
        outcome=receipt.outcome,
        attempt=receipt.attempt,
        attempt_id=receipt.attempt_id,
        lease_epoch=receipt.lease_epoch,
        acked_at=receipt.acked_at,
        result=receipt.result,
    )


def _grant(lease: DeliveryLease) -> DeliveryLeaseGrant:
    return DeliveryLeaseGrant(
        notification_id=lease.notification_id,
        delivery_key=lease.delivery_key,
        state=lease.state,
        lease_epoch=lease.lease_epoch,
        lease_until=lease.lease_until,
        attempt=lease.attempt,
        reused=lease.reused,
        receipt=_receipt(lease.receipt),
    )


def get_channel_delivery_port(
    settings_client: PlatformSettingsClientDep,
) -> ChannelDeliveryPort:
    """默认装配：与 Runtime 其他投递路径共用同一会话工厂与设置客户端（同一 PG 权威状态）。"""
    return ChannelDeliveryPort(get_session_factory, settings_client)


ChannelDeliveryPortDep = Annotated[ChannelDeliveryPort, Depends(get_channel_delivery_port)]


@router.post("/reserve")
async def reserve_delivery(
    body: DeliveryReservationRequest,
    request: Request,
    tenant: TenantDep,
    port: ChannelDeliveryPortDep,
) -> ApiResponse[DeliveryLeaseGrant]:
    grant = await port.reserve(tenant_id=tenant, body=body)
    return ok(request.app.state.message_catalog, grant)


@router.post("/{notification_id}/renew")
async def renew_delivery(
    notification_id: uuid.UUID,
    body: DeliveryLeaseRenewal,
    request: Request,
    tenant: TenantDep,
    port: ChannelDeliveryPortDep,
) -> ApiResponse[DeliveryLeaseGrant]:
    grant = await port.renew(tenant_id=tenant, notification_id=notification_id, body=body)
    return ok(request.app.state.message_catalog, grant)


@router.post("/{notification_id}/ack")
async def ack_delivery(
    notification_id: uuid.UUID,
    body: DeliveryAck,
    request: Request,
    tenant: TenantDep,
    port: ChannelDeliveryPortDep,
) -> ApiResponse[DeliveryAckReceipt]:
    receipt = await port.ack(tenant_id=tenant, notification_id=notification_id, body=body)
    return ok(request.app.state.message_catalog, receipt)
