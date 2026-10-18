"""API-17：Console 探针的受信容量端口（design §3.4/§3.7）。

- 只有受信内部身份（`require_internal_service`）可达；探针线协议是 `ProbeCapacityRequest`；
- Runtime 从受信控制面（`control.model_*`，只读同一 PG 的当前配置）解析本次探测的冻结身份：
  成员关系、凭据当前版本、由 provider_key+摘要派生的**稳定计量身份**与共享额度组——探针因此
  与主调用/摘要/提炼在同一物理 Key 与账号额度上合并计量，不绕过资源池；
- 探针 HTTP 仍由 Console 执行（TASK-028）：本端口只做预占/续租/结算，不调用模型、不写模型
  计费审计，响应只给栅栏事实（reservation_id/epoch/lease_until），不含密钥或内部计量身份；
- 释放语义与主链一致：只有"确定未发送"（NOT_SENT）或有远端完成证据才释放占用，无法确认远端
  结束时保留 UNKNOWN 占用（不假定供方未计费）；本端口不触碰 Run/job 的调用租约。
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, Request
from muad_api import ApiResponse, ok
from muad_api.error_codes import ErrorCode
from muad_api.errors import AppError
from muad_api.security import require_internal_service
from muad_contracts.model_pool import (
    ModelCallSettlement,
    ModelCapacityReservationRequest,
    ProbeCallOwner,
    ProbeCallSettlement,
    ProbeCapacityGrant,
    ProbeCapacityRelease,
    ProbeCapacityRequest,
    ProbeLeaseRenewal,
)

from ..application.model_capacity_service import ModelCapacityService
from ..infrastructure.db import SessionFactoryProvider, get_session_factory
from ..infrastructure.model_capacity_store import LeaseSnapshot, ModelCapacityStore, ProbeTarget
from .deps import TenantDep

router = APIRouter(
    prefix="/internal/model-capacity",
    tags=["model-capacity"],
    dependencies=[Depends(require_internal_service)],
)

#: 探针租约的固定执行方身份：API-17 只有一个受信调用方（Console 探针），续租/结算只核 epoch
PROBE_LEASE_OWNER = "console-probe"

ReleaseState = Literal["ACTIVE", "UNKNOWN", "SETTLED"]


def _release_state(state: str) -> ReleaseState:
    """把服务层状态投影为线协议枚举；越界即内部错误，不静默透传。"""
    if state not in ("ACTIVE", "UNKNOWN", "SETTLED"):
        raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
    return cast(ReleaseState, state)


class ProbeCapacityPort:
    """API-17 的受信适配层：线协议 → 容量服务，并守卫 PROBE 归属与目标身份。"""

    def __init__(
        self, session_factory_provider: SessionFactoryProvider, service: ModelCapacityService
    ) -> None:
        self._sessions = session_factory_provider
        self._service = service

    async def reserve(self, *, tenant_id: str, request: ProbeCapacityRequest) -> ProbeCapacityGrant:
        """预占：先解析成员/凭据/计量身份，再走与主调用同一 `ModelCapacityService.reserve`。"""
        target = await self._require_target(
            tenant_id=tenant_id, model_id=request.model_id, credential_id=request.credential_id
        )
        reservation = await self._service.reserve(
            tenant_id=tenant_id,
            lease_owner=PROBE_LEASE_OWNER,
            request=ModelCapacityReservationRequest(
                call_attempt_id=request.attempt_id,
                model_id=request.model_id,
                deployment_id=target.deployment_id,
                credential_id=target.credential_id,
                credential_revision=target.credential_revision,
                key_resource_id=target.key_resource_id,
                quota_group_id=target.quota_group_id,
                owner=ProbeCallOwner(probe_id=request.probe_id),
                workload="PROBE",
                deadline_at=request.deadline_at,
                reserved_tokens=request.reserved_tokens,
            ),
        )
        return ProbeCapacityGrant(
            reservation_id=reservation.reservation_id,
            epoch=reservation.epoch,
            lease_until=reservation.lease_until,
        )

    async def renew(
        self, *, tenant_id: str, reservation_id: uuid.UUID, body: ProbeLeaseRenewal
    ) -> ProbeCapacityGrant:
        """续租：核 epoch 与冻结的绝对期限；失约（终态/过期/epoch 不符）即停手。"""
        lease = await self._require_probe_lease(tenant_id=tenant_id, reservation_id=reservation_id)
        renewal = await self._service.renew(
            tenant_id=tenant_id,
            call_attempt_id=lease.call_attempt_id,
            epoch=body.epoch,
            lease_owner=PROBE_LEASE_OWNER,
            deadline_at=body.deadline_at,
        )
        return ProbeCapacityGrant(
            reservation_id=renewal.reservation_id,
            epoch=renewal.epoch,
            lease_until=renewal.lease_until,
        )

    async def settle(
        self, *, tenant_id: str, reservation_id: uuid.UUID, body: ProbeCallSettlement
    ) -> ProbeCapacityRelease:
        """结算：核 epoch，按证据分档释放/保留；SETTLED 幂等回放不二次扣减。"""
        lease = await self._require_probe_lease(tenant_id=tenant_id, reservation_id=reservation_id)
        release = await self._service.settle(
            tenant_id=tenant_id,
            call_attempt_id=lease.call_attempt_id,
            settlement=ModelCallSettlement(
                epoch=body.epoch,
                lease_owner=PROBE_LEASE_OWNER,
                outcome=body.outcome,
                usage=body.usage,
                remote_completion=body.remote_completion,
            ),
        )
        return ProbeCapacityRelease(
            reservation_id=release.reservation_id,
            state=_release_state(release.state),
            released=release.released,
        )

    async def _require_target(
        self, *, tenant_id: str, model_id: uuid.UUID, credential_id: uuid.UUID
    ) -> ProbeTarget:
        async with self._sessions()() as session:
            target = await ModelCapacityStore(session).read_probe_target(
                tenant_id=tenant_id, model_id=model_id, credential_id=credential_id
            )
        if target is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        return target

    async def _require_probe_lease(
        self, *, tenant_id: str, reservation_id: uuid.UUID
    ) -> LeaseSnapshot:
        """路径 id 必须指向本租户的 PROBE 租约：本端口不触碰 Run/job 的调用。"""
        async with self._sessions()() as session:
            lease = await ModelCapacityStore(session).read_lease_by_reservation(
                tenant_id=tenant_id, reservation_id=reservation_id
            )
        if lease is None or lease.probe_id is None or lease.workload != "PROBE":
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        return lease


def get_probe_capacity_port() -> ProbeCapacityPort:
    """默认装配：与 Runtime 其他容量路径共用同一会话工厂（同一 PG 权威状态）。"""
    factory = get_session_factory
    return ProbeCapacityPort(factory, ModelCapacityService(factory))


ProbeCapacityPortDep = Annotated[ProbeCapacityPort, Depends(get_probe_capacity_port)]


@router.post("/reservations")
async def reserve_probe(
    body: ProbeCapacityRequest,
    request: Request,
    tenant: TenantDep,
    port: ProbeCapacityPortDep,
) -> ApiResponse[ProbeCapacityGrant]:
    grant = await port.reserve(tenant_id=tenant, request=body)
    return ok(request.app.state.message_catalog, grant)


@router.post("/reservations/{reservation_id}/renew")
async def renew_probe(
    reservation_id: uuid.UUID,
    body: ProbeLeaseRenewal,
    request: Request,
    tenant: TenantDep,
    port: ProbeCapacityPortDep,
) -> ApiResponse[ProbeCapacityGrant]:
    grant = await port.renew(tenant_id=tenant, reservation_id=reservation_id, body=body)
    return ok(request.app.state.message_catalog, grant)


@router.post("/reservations/{reservation_id}/settle")
async def settle_probe(
    reservation_id: uuid.UUID,
    body: ProbeCallSettlement,
    request: Request,
    tenant: TenantDep,
    port: ProbeCapacityPortDep,
) -> ApiResponse[ProbeCapacityRelease]:
    release = await port.settle(tenant_id=tenant, reservation_id=reservation_id, body=body)
    return ok(request.app.state.message_catalog, release)
