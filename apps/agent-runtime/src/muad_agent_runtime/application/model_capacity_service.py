"""物理 Key / 共享额度组的原子预占与释放（设计 §3.2/§3.3/§3.7）。

- 预占先取 tenant/model 的池调度 advisory 事务锁，再按 quota_group → key_resource → attempt
  固定锁序加行锁；事务内不发起任何网络请求（凭据解析在提交之后另行进行）；
- 重复 `call_attempt_id` 不重复占位：既有租约原样复用（含已 SETTLED 的尝试），冻结身份不一致
  则明确拒绝（`MODEL_CALL_LEASE_LOST`）；
- 额度窗口固定为保守滚动 60 秒并按 60 秒桶有界分桶：保留与窗口相交的边界桶（保守侧取整），
  只有整个窗口与边界桶都过期才清零，绝不清空仍在窗口内的在飞预占；
- 只有“确定未发送”才能释放占用；取消/异常退出按 UNKNOWN 保留占用，UNKNOWN 不能被当成
  未发送释放（`MODEL_CALL_OUTCOME_UNKNOWN`），本地租约过期也不等于远端已停。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_common import SharedSettings
from muad_contracts.model_pool import (
    MemoryJobCallOwner,
    ModelCallOwner,
    ModelCapacityReservationRequest,
    RunCallOwner,
)
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.db import SessionFactoryProvider
from ..infrastructure.model_capacity_store import (
    RESOURCE_KEY,
    RESOURCE_QUOTA_GROUP,
    CapacityLimits,
    CapacitySnapshot,
    LeaseSnapshot,
    LeaseWrite,
    ModelCapacityStore,
)

#: 额度窗口固定 60 秒（design §3.7：不提供任意算法切换）
QUOTA_WINDOW_SECONDS = 60
#: 保守滚动窗口保留的桶数：当前桶 + 与窗口相交的上一桶（边界桶向保守侧取整）
QUOTA_WINDOW_BUCKETS = 2
#: 首次预占的租约代次（续租/接管由 TASK-022 在同 attempt 上推进）
INITIAL_LEASE_EPOCH = 1

StoreFactory = Callable[[AsyncSession], ModelCapacityStore]


@dataclass(frozen=True, slots=True)
class WindowState:
    """一个容量资源的窗口计数（预占/释放后写回的绝对值）。"""

    window_start: datetime
    reserved_requests: int
    reserved_tokens: int


@dataclass(frozen=True, slots=True)
class CapacityReservation:
    """预占结果；`reused` 表示本次调用复用了既有 attempt 的租约（未再次占位）。"""

    reservation_id: uuid.UUID
    call_attempt_id: uuid.UUID
    epoch: int
    lease_owner: str
    state: str
    issued_at: datetime
    lease_until: datetime
    reserved_tokens: int
    reused: bool


@dataclass(frozen=True, slots=True)
class CapacityRelease:
    """释放/保留结果；`released` 表示本次调用真的释放了占用。"""

    reservation_id: uuid.UUID
    call_attempt_id: uuid.UUID
    epoch: int
    state: str
    released: bool


def _bucket_start(now: datetime) -> datetime:
    """近似 PG `date_trunc('minute', now())`：分桶边界固定，窗口长度恰好 60 秒。"""
    return now.replace(second=0, microsecond=0)


def _window_after_rollover(current: WindowState, bucket: datetime) -> WindowState:
    """整个窗口与相交边界桶都过期才清零；否则保留，不提前清掉在飞预占。"""
    span = timedelta(seconds=QUOTA_WINDOW_SECONDS * QUOTA_WINDOW_BUCKETS)
    if bucket - current.window_start >= span:
        return WindowState(window_start=bucket, reserved_requests=0, reserved_tokens=0)
    return current


def _lease_until(now: datetime, deadline_at: datetime, lease_seconds: int) -> datetime:
    """租约到 `now + 租期`，但不越过本次调用的绝对期限；期限已过即不再预占。"""
    lease_until = min(now + timedelta(seconds=lease_seconds), deadline_at)
    if lease_until <= now:
        raise AppError(ErrorCode.MODEL_WAIT_TIMEOUT)
    return lease_until


def _owner_reference(
    owner: ModelCallOwner,
) -> tuple[uuid.UUID | None, uuid.UUID | None, uuid.UUID | None]:
    if isinstance(owner, RunCallOwner):
        return owner.run_id, None, None
    if isinstance(owner, MemoryJobCallOwner):
        return None, owner.memory_job_id, None
    return None, None, owner.probe_id


def _require_same_attempt(
    lease: LeaseSnapshot, request: ModelCapacityReservationRequest
) -> None:
    """同一 attempt 必须复用同一冻结身份；身份变了说明执行方不能继续提交。"""
    reference = _owner_reference(request.owner)
    if (
        lease.deployment_id != request.deployment_id
        or lease.credential_id != request.credential_id
        or lease.key_resource_id != request.key_resource_id
        or lease.quota_group_id != request.quota_group_id
        or lease.credential_revision != request.credential_revision
        or lease.workload != request.workload
        or (lease.run_id, lease.memory_job_id, lease.probe_id) != reference
    ):
        raise AppError(ErrorCode.MODEL_CALL_LEASE_LOST)


def _require_lease_holder(lease: LeaseSnapshot, *, epoch: int, lease_owner: str) -> None:
    if lease.epoch != epoch or lease.lease_owner != lease_owner:
        raise AppError(ErrorCode.MODEL_CALL_LEASE_LOST)


def _plan_admission(
    row: CapacitySnapshot, limits: CapacityLimits, *, now: datetime, tokens: int
) -> WindowState:
    """行锁内的准入判定：在飞用精确计数，RPM/TPM 用保守滚动窗口；超限抛清晰错误。"""
    if row.inflight + 1 > limits.max_inflight:
        raise AppError(ErrorCode.MODEL_CAPACITY_UNAVAILABLE)
    window = _window_after_rollover(
        WindowState(row.window_start, row.reserved_requests, row.reserved_tokens), _bucket_start(now)
    )
    if limits.rpm is not None and window.reserved_requests + 1 > limits.rpm:
        raise AppError(ErrorCode.MODEL_CAPACITY_UNAVAILABLE)
    if limits.tpm is not None and window.reserved_tokens + tokens > limits.tpm:
        raise AppError(ErrorCode.MODEL_CAPACITY_UNAVAILABLE)
    return WindowState(
        window_start=window.window_start,
        reserved_requests=window.reserved_requests + 1,
        reserved_tokens=window.reserved_tokens + tokens,
    )


async def _require_limits(
    store: ModelCapacityStore, *, tenant_id: str, request: ModelCapacityReservationRequest
) -> tuple[CapacityLimits, CapacityLimits | None]:
    limits = await store.read_limits(
        tenant_id=tenant_id,
        credential_id=request.credential_id,
        quota_group_id=request.quota_group_id,
    )
    if limits is None or (request.quota_group_id is not None and limits.quota_group is None):
        raise AppError(ErrorCode.COMMON_NOT_FOUND)
    if not limits.enabled:
        raise AppError(ErrorCode.MODEL_DISABLED)
    return limits.key, limits.quota_group


async def _lock_capacity_rows(
    store: ModelCapacityStore,
    *,
    tenant_id: str,
    key_resource_id: uuid.UUID,
    quota_group_id: uuid.UUID | None,
    now: datetime,
) -> tuple[CapacitySnapshot, CapacitySnapshot | None]:
    """固定锁序的容量段：先组行、后 Key 行（缺失即建，组缺失则请求本身无效）。"""
    bucket = _bucket_start(now)
    group_row: CapacitySnapshot | None = None
    if quota_group_id is not None:
        group_row = await store.lock_capacity(
            tenant_id=tenant_id,
            resource_type=RESOURCE_QUOTA_GROUP,
            resource_id=quota_group_id,
            window_start=bucket,
        )
    key_row = await store.lock_capacity(
        tenant_id=tenant_id,
        resource_type=RESOURCE_KEY,
        resource_id=key_resource_id,
        window_start=bucket,
    )
    return key_row, group_row


async def _write_admission(
    store: ModelCapacityStore,
    *,
    tenant_id: str,
    row: CapacitySnapshot,
    plan: WindowState,
    now: datetime,
) -> None:
    await store.write_capacity(
        tenant_id=tenant_id,
        snapshot=row,
        inflight=row.inflight + 1,
        window_start=plan.window_start,
        reserved_requests=plan.reserved_requests,
        reserved_tokens=plan.reserved_tokens,
        now=now,
    )


async def _release_capacity(
    store: ModelCapacityStore,
    *,
    tenant_id: str,
    lease: LeaseSnapshot,
    key_row: CapacitySnapshot,
    group_row: CapacitySnapshot | None,
    now: datetime,
) -> None:
    """只“确定未发送”才回收：在飞必减 1，窗口计数仅在该租约仍计入当前窗口时减。"""
    for row in (group_row, key_row):
        if row is None:
            continue
        counted = lease.create_time >= row.window_start
        await store.write_capacity(
            tenant_id=tenant_id,
            snapshot=row,
            inflight=max(row.inflight - 1, 0),
            window_start=row.window_start,
            reserved_requests=max(row.reserved_requests - (1 if counted else 0), 0),
            reserved_tokens=max(row.reserved_tokens - (lease.reserved_tokens if counted else 0), 0),
            now=now,
        )


def _lease_write(
    request: ModelCapacityReservationRequest,
    *,
    lease_owner: str,
    epoch: int,
    lease_until: datetime,
) -> LeaseWrite:
    run_id, memory_job_id, probe_id = _owner_reference(request.owner)
    return LeaseWrite(
        call_attempt_id=request.call_attempt_id,
        deployment_id=request.deployment_id,
        credential_id=request.credential_id,
        key_resource_id=request.key_resource_id,
        quota_group_id=request.quota_group_id,
        credential_revision=request.credential_revision,
        workload=request.workload,
        lease_owner=lease_owner,
        epoch=epoch,
        lease_until=lease_until,
        reserved_tokens=request.reserved_tokens,
        run_id=run_id,
        memory_job_id=memory_job_id,
        probe_id=probe_id,
    )


def _reservation(lease: LeaseSnapshot, *, reused: bool) -> CapacityReservation:
    return CapacityReservation(
        reservation_id=lease.reservation_id,
        call_attempt_id=lease.call_attempt_id,
        epoch=lease.epoch,
        lease_owner=lease.lease_owner,
        state=lease.state,
        issued_at=lease.create_time,
        lease_until=lease.lease_until,
        reserved_tokens=lease.reserved_tokens,
        reused=reused,
    )


async def _apply_transition(
    store: ModelCapacityStore,
    *,
    tenant_id: str,
    call_attempt_id: uuid.UUID,
    epoch: int,
    lease_owner: str,
    target: str,
    release: bool,
) -> CapacityRelease:
    """释放（确定未发送）与保留（UNKNOWN）共用的状态推进：先身份后锁序，不超发。"""
    now = await store.database_clock()
    identity = await store.read_lease(tenant_id=tenant_id, call_attempt_id=call_attempt_id)
    if identity is None:
        raise AppError(ErrorCode.COMMON_NOT_FOUND)
    key_row, group_row = await _lock_capacity_rows(
        store,
        tenant_id=tenant_id,
        key_resource_id=identity.key_resource_id,
        quota_group_id=identity.quota_group_id,
        now=now,
    )
    lease = await store.lock_attempt(tenant_id=tenant_id, call_attempt_id=call_attempt_id)
    if lease is None:  # pragma: no cover - 无锁读与行锁之间不可能删除
        raise AppError(ErrorCode.COMMON_NOT_FOUND)
    _require_lease_holder(lease, epoch=epoch, lease_owner=lease_owner)
    if lease.state != "ACTIVE":
        if release and lease.state == "UNKNOWN":
            raise AppError(ErrorCode.MODEL_CALL_OUTCOME_UNKNOWN)
        return CapacityRelease(
            lease.reservation_id, lease.call_attempt_id, lease.epoch, lease.state, False
        )
    if release:
        await _release_capacity(
            store,
            tenant_id=tenant_id,
            lease=lease,
            key_row=key_row,
            group_row=group_row,
            now=now,
        )
    updated = await store.write_lease_state(
        tenant_id=tenant_id, call_attempt_id=call_attempt_id, state=target, now=now
    )
    return CapacityRelease(
        updated.reservation_id, updated.call_attempt_id, updated.epoch, updated.state, release
    )


class ModelCapacityService:
    def __init__(
        self,
        session_factory_provider: SessionFactoryProvider,
        *,
        lease_duration_sec: int | None = None,
        store_factory: StoreFactory = ModelCapacityStore,
    ) -> None:
        self._session_factory_provider = session_factory_provider
        self._lease_seconds = (
            lease_duration_sec
            if lease_duration_sec is not None
            else SharedSettings().model_call_lease_sec
        )
        self._store_factory = store_factory

    async def reserve(
        self, *, tenant_id: str, lease_owner: str, request: ModelCapacityReservationRequest
    ) -> CapacityReservation:
        """原子预占：池锁→组行→Key 行→attempt，锁内只判定与计数，不发网络请求。"""
        async with self._session_factory_provider()() as session, session.begin():
            store = self._store_factory(session)
            key_limits, group_limits = await _require_limits(store, tenant_id=tenant_id, request=request)
            now = await store.database_clock()
            lease_until = _lease_until(now, request.deadline_at, self._lease_seconds)
            await store.lock_pool(tenant_id=tenant_id, model_id=request.model_id)
            key_row, group_row = await _lock_capacity_rows(
                store,
                tenant_id=tenant_id,
                key_resource_id=request.key_resource_id,
                quota_group_id=request.quota_group_id,
                now=now,
            )
            attempt = await store.lock_attempt(
                tenant_id=tenant_id, call_attempt_id=request.call_attempt_id
            )
            if attempt is not None:
                _require_same_attempt(attempt, request)
                return _reservation(attempt, reused=True)
            key_plan = _plan_admission(key_row, key_limits, now=now, tokens=request.reserved_tokens)
            group_plan: WindowState | None = None
            if group_row is not None and group_limits is not None:
                group_plan = _plan_admission(
                    group_row, group_limits, now=now, tokens=request.reserved_tokens
                )
            lease = await store.insert_lease(
                tenant_id=tenant_id,
                write=_lease_write(
                    request, lease_owner=lease_owner, epoch=INITIAL_LEASE_EPOCH,
                    lease_until=lease_until,
                ),
                now=now,
            )
            if group_row is not None and group_plan is not None:
                await _write_admission(
                    store, tenant_id=tenant_id, row=group_row, plan=group_plan, now=now
                )
            await _write_admission(store, tenant_id=tenant_id, row=key_row, plan=key_plan, now=now)
            return _reservation(lease, reused=False)

    async def release_unsent(
        self, *, tenant_id: str, call_attempt_id: uuid.UUID, epoch: int, lease_owner: str
    ) -> CapacityRelease:
        """“确定未发送”释放：回收在飞与窗口计数；重复释放只回放既有终态。"""
        return await self._transition(
            tenant_id=tenant_id,
            call_attempt_id=call_attempt_id,
            epoch=epoch,
            lease_owner=lease_owner,
            target="SETTLED",
            release=True,
        )

    async def mark_unknown(
        self, *, tenant_id: str, call_attempt_id: uuid.UUID, epoch: int, lease_owner: str
    ) -> CapacityRelease:
        """无法确认远端结束时保留占用（取消/异常退出），不释放任何容量。"""
        return await self._transition(
            tenant_id=tenant_id,
            call_attempt_id=call_attempt_id,
            epoch=epoch,
            lease_owner=lease_owner,
            target="UNKNOWN",
            release=False,
        )

    async def ensure_lease_held(
        self, *, tenant_id: str, call_attempt_id: uuid.UUID, epoch: int, lease_owner: str
    ) -> None:
        """执行前的失约核验：租约被结算/接管/过期即 `MODEL_CALL_LEASE_LOST`（停手）。"""
        async with self._session_factory_provider()() as session:
            store = self._store_factory(session)
            lease = await store.read_lease(tenant_id=tenant_id, call_attempt_id=call_attempt_id)
            now = await store.database_clock()
        if lease is None or lease.epoch != epoch or lease.lease_owner != lease_owner:
            raise AppError(ErrorCode.MODEL_CALL_LEASE_LOST)
        if lease.state != "ACTIVE" or lease.lease_until <= now:
            raise AppError(ErrorCode.MODEL_CALL_LEASE_LOST)

    async def _transition(
        self,
        *,
        tenant_id: str,
        call_attempt_id: uuid.UUID,
        epoch: int,
        lease_owner: str,
        target: str,
        release: bool,
    ) -> CapacityRelease:
        async with self._session_factory_provider()() as session, session.begin():
            store = self._store_factory(session)
            return await _apply_transition(
                store,
                tenant_id=tenant_id,
                call_attempt_id=call_attempt_id,
                epoch=epoch,
                lease_owner=lease_owner,
                target=target,
                release=release,
            )
