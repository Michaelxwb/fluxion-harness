"""容量端口的两个受信入口：API-17（Console 探针）与 API-29（定时任务 Worker）。

两个入口**只有 owner 身份不同**，预占/续租/结算都走同一个 `ModelCapacityService`：物理 Key、
共享额度组与结算口径不因调用方而分叉。探针端口不接受 Task owner，Task 端口也不接受 Probe
owner —— 线协议各自固定自己的 owner 类型，调用方不能靠请求体换一种身份。

API-17（探针）：

- 只有受信内部身份（`require_internal_service`）可达；探针线协议是 `ProbeCapacityRequest`；
- Runtime 从受信控制面（`control.model_*`，只读同一 PG 的当前配置）解析本次探测的冻结身份：
  成员关系、凭据当前版本、由 provider_key+摘要派生的**稳定计量身份**与共享额度组——探针因此
  与主调用/摘要/提炼在同一物理 Key 与账号额度上合并计量，不绕过资源池；
- 探针 HTTP 仍由 Console 执行（TASK-028）：本端口只做预占/续租/结算，不调用模型、不写模型
  计费审计，响应只给栅栏事实（reservation_id/epoch/lease_until），不含密钥或内部计量身份；
- 释放语义与主链一致：只有"确定未发送"（NOT_SENT）或有远端完成证据才释放占用，无法确认远端
  结束时保留 UNKNOWN 占用（不假定供方未计费）；本端口不触碰 Run/job 的调用租约。

API-29（定时任务）：
- 只允许内部服务身份；`workload` 固定 BACKGROUND（后台份额，不与交互式争抢）；
- owner 是 `task.task_execution.id`，进入 `model_call_lease` 的四选一约束；租约的执行方身份由
  Execution 派生（不接受线协议声明），失约即 `MODEL_CALL_LEASE_LOST`，调用方必须停止本地模型
  请求；
- 归属**按 Execution 行复核**（Runtime 只读该组事实）：租户不符/Execution 不存在即 404，操作者
  与 `X-Actor-User-Id` 不符即 `TASK_ACCESS_DENIED`；请求体的 owner/tenant 不是信任来源；
- 本端口**不创建 Run**，也不调用模型（模型调用仍在 Worker 侧，凭据按 Execution 解析）。
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, Header, Request
from muad_api import ApiResponse, ok
from muad_api.error_codes import ErrorCode
from muad_api.errors import AppError
from muad_api.security import require_internal_service
from muad_contracts import (
    TaskCallSettlement,
    TaskCapacityGrant,
    TaskCapacityRelease,
    TaskCapacityReservationRequest,
    TaskLeaseRenewal,
)
from muad_contracts.model_pool import (
    ModelCallSettlement,
    ModelCapacityReservationRequest,
    ProbeCallOwner,
    ProbeCallSettlement,
    ProbeCapacityGrant,
    ProbeCapacityRelease,
    ProbeCapacityRequest,
    ProbeLeaseRenewal,
    TaskCallOwner,
)

from ..application.model_capacity_service import ModelCapacityService
from ..infrastructure.db import SessionFactoryProvider, get_session_factory
from ..infrastructure.model_capacity_store import (
    CapacityTarget,
    LeaseSnapshot,
    ModelCapacityStore,
)
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
    ) -> CapacityTarget:
        async with self._sessions()() as session:
            target = await ModelCapacityStore(session).read_capacity_target(
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


# --------------------------------------------------------------------------------------
# API-29：定时任务的 BACKGROUND 容量（同一容量服务，owner=TASK + workload=BACKGROUND）
# --------------------------------------------------------------------------------------

TASK_LEASE_OWNER_PREFIX = "task:"


def task_lease_owner(task_execution_id: uuid.UUID) -> str:
    """Task 租约的固定执行方身份：由 Execution 派生，线协议不能声明或覆盖。"""
    return f"{TASK_LEASE_OWNER_PREFIX}{task_execution_id}"


def require_actor_user_id(
    x_actor_user_id: Annotated[str | None, Header()] = None,
) -> uuid.UUID:
    """变更类内部调用必须声明操作者：缺失即无权，不按"未限定"放行。"""
    if x_actor_user_id is None:
        raise AppError(ErrorCode.TASK_ACCESS_DENIED)
    try:
        return uuid.UUID(x_actor_user_id)
    except ValueError as exc:
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR) from exc


ActorDep = Annotated[uuid.UUID, Depends(require_actor_user_id)]


class TaskCapacityPort:
    """API-29 的受信适配层：Task owner 的预占/续租/结算（不创建 Run，不调用模型）。"""

    def __init__(
        self, session_factory_provider: SessionFactoryProvider, service: ModelCapacityService
    ) -> None:
        self._sessions = session_factory_provider
        self._service = service

    async def reserve(
        self,
        *,
        tenant_id: str,
        actor_user_id: uuid.UUID,
        request: TaskCapacityReservationRequest,
    ) -> TaskCapacityGrant:
        """预占：先按 Execution 复核归属，再核对冻结成员身份，最后走同一容量服务。"""
        task_execution_id = request.owner.task_execution_id
        await self._require_owner(
            tenant_id=tenant_id,
            task_execution_id=task_execution_id,
            actor_user_id=actor_user_id,
        )
        await self._require_frozen_target(tenant_id=tenant_id, request=request)
        reservation = await self._service.reserve(
            tenant_id=tenant_id,
            lease_owner=task_lease_owner(task_execution_id),
            request=ModelCapacityReservationRequest(
                call_attempt_id=request.call_attempt_id,
                model_id=request.model_id,
                deployment_id=request.deployment_id,
                credential_id=request.credential_id,
                credential_revision=request.credential_revision,
                key_resource_id=request.key_resource_id,
                quota_group_id=request.quota_group_id,
                owner=TaskCallOwner(task_execution_id=task_execution_id),
                # 定时任务永远是后台份额：不与交互式争抢，高峰让路、老化后放行
                workload="BACKGROUND",
                deadline_at=request.deadline_at,
                reserved_tokens=request.reserved_tokens,
            ),
        )
        return TaskCapacityGrant(
            reservation_id=reservation.reservation_id,
            epoch=reservation.epoch,
            lease_until=reservation.lease_until,
        )

    async def renew(
        self,
        *,
        tenant_id: str,
        actor_user_id: uuid.UUID,
        reservation_id: uuid.UUID,
        body: TaskLeaseRenewal,
    ) -> TaskCapacityGrant:
        """续租：核 owner 与 epoch；失约（终态/过期/epoch 不符）即停手。"""
        lease = await self._require_task_lease(
            tenant_id=tenant_id, actor_user_id=actor_user_id, reservation_id=reservation_id
        )
        renewal = await self._service.renew(
            tenant_id=tenant_id,
            call_attempt_id=lease.call_attempt_id,
            epoch=body.epoch,
            lease_owner=task_lease_owner(cast(uuid.UUID, lease.task_execution_id)),
            deadline_at=body.deadline_at,
        )
        return TaskCapacityGrant(
            reservation_id=renewal.reservation_id,
            epoch=renewal.epoch,
            lease_until=renewal.lease_until,
        )

    async def settle(
        self,
        *,
        tenant_id: str,
        actor_user_id: uuid.UUID,
        reservation_id: uuid.UUID,
        body: TaskCallSettlement,
    ) -> TaskCapacityRelease:
        """结算：按证据分档释放/保留；SETTLED 幂等回放不二次扣减。"""
        lease = await self._require_task_lease(
            tenant_id=tenant_id, actor_user_id=actor_user_id, reservation_id=reservation_id
        )
        release = await self._service.settle(
            tenant_id=tenant_id,
            call_attempt_id=lease.call_attempt_id,
            settlement=ModelCallSettlement(
                epoch=body.epoch,
                lease_owner=task_lease_owner(cast(uuid.UUID, lease.task_execution_id)),
                outcome=body.outcome,
                usage=body.usage,
                remote_completion=body.remote_completion,
            ),
        )
        return TaskCapacityRelease(
            reservation_id=release.reservation_id,
            state=_release_state(release.state),
            released=release.released,
        )

    async def _require_owner(
        self, *, tenant_id: str, task_execution_id: uuid.UUID, actor_user_id: uuid.UUID
    ) -> None:
        """归属只认 Execution 行：租户不符即 404，操作者不符即无权（body 不是信任来源）。

        agent 不是线协议字段（由 Execution 行唯一确定），因此这里没有可伪造的 agent 声明；
        凭据解析按该 Execution 冻结的模型复核 agent 维度。
        """
        async with self._sessions()() as session:
            execution_actor = await ModelCapacityStore(session).read_task_execution_actor(
                tenant_id=tenant_id, task_execution_id=task_execution_id
            )
        if execution_actor is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        if execution_actor != actor_user_id:
            raise AppError(ErrorCode.TASK_ACCESS_DENIED)

    async def _require_task_lease(
        self, *, tenant_id: str, actor_user_id: uuid.UUID, reservation_id: uuid.UUID
    ) -> LeaseSnapshot:
        """路径 id 必须指向本租户的 TASK 后台租约，且操作者仍是该 Execution 的 actor。"""
        async with self._sessions()() as session:
            lease = await ModelCapacityStore(session).read_lease_by_reservation(
                tenant_id=tenant_id, reservation_id=reservation_id
            )
        if (
            lease is None
            or lease.task_execution_id is None
            or lease.owner_type != "TASK"
            or lease.workload != "BACKGROUND"
        ):
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        await self._require_owner(
            tenant_id=tenant_id,
            task_execution_id=lease.task_execution_id,
            actor_user_id=actor_user_id,
        )
        return lease

    async def _require_frozen_target(
        self, *, tenant_id: str, request: TaskCapacityReservationRequest
    ) -> CapacityTarget:
        """冻结引用必须仍与当前控制面一致：轮换/换成员后重新冻结，不拿新 Key 消费旧槽位。"""
        async with self._sessions()() as session:
            target = await ModelCapacityStore(session).read_capacity_target(
                tenant_id=tenant_id,
                model_id=request.model_id,
                credential_id=request.credential_id,
            )
        if target is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        if (
            target.deployment_id != request.deployment_id
            or target.credential_revision != request.credential_revision
            or target.key_resource_id != request.key_resource_id
            or target.quota_group_id != request.quota_group_id
        ):
            raise AppError(ErrorCode.MODEL_CREDENTIAL_CHANGED)
        return target


def get_task_capacity_port() -> TaskCapacityPort:
    """与 Probe/主链同一装配：同一会话工厂、同一 PG 权威状态。"""
    factory = get_session_factory
    return TaskCapacityPort(factory, ModelCapacityService(factory))


TaskCapacityPortDep = Annotated[TaskCapacityPort, Depends(get_task_capacity_port)]


@router.post("/task-reservations")
async def reserve_task(
    body: TaskCapacityReservationRequest,
    request: Request,
    tenant: TenantDep,
    actor: ActorDep,
    port: TaskCapacityPortDep,
) -> ApiResponse[TaskCapacityGrant]:
    grant = await port.reserve(tenant_id=tenant, actor_user_id=actor, request=body)
    return ok(request.app.state.message_catalog, grant)


@router.post("/task-reservations/{reservation_id}/renew")
async def renew_task(
    reservation_id: uuid.UUID,
    body: TaskLeaseRenewal,
    request: Request,
    tenant: TenantDep,
    actor: ActorDep,
    port: TaskCapacityPortDep,
) -> ApiResponse[TaskCapacityGrant]:
    grant = await port.renew(
        tenant_id=tenant, actor_user_id=actor, reservation_id=reservation_id, body=body
    )
    return ok(request.app.state.message_catalog, grant)


@router.post("/task-reservations/{reservation_id}/settle")
async def settle_task(
    reservation_id: uuid.UUID,
    body: TaskCallSettlement,
    request: Request,
    tenant: TenantDep,
    actor: ActorDep,
    port: TaskCapacityPortDep,
) -> ApiResponse[TaskCapacityRelease]:
    release = await port.settle(
        tenant_id=tenant, actor_user_id=actor, reservation_id=reservation_id, body=body
    )
    return ok(request.app.state.message_catalog, release)
