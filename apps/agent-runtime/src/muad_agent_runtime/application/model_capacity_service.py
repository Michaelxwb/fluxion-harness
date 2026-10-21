"""物理 Key / 共享额度组的原子预占、续租、usage 结算与 UNKNOWN 保留（设计 §3.2/§3.3/§3.7）。

- 预占只按 quota_group → key_resource → attempt 固定锁序加行锁（不再取池锁：同一
  (tenant, model) 的全局 advisory 锁曾把每次模型调用串成一条队列）；事务内不发起任何
  网络请求（凭据解析在提交之后另行进行）；
- 成员选择没有池级 advisory 锁（由单条原子语句完成排序与计数）；进程内闸门只限制**同时
  在 DB 上排队**的会话数：其余调用在 asyncio 信号量上排队、不占 DB 连接（800 用户实测：
  数百个等待者占满连接池会把准入饿死）；
- 重复 `call_attempt_id` 不重复占位：既有租约原样复用（含已 SETTLED 的尝试），冻结身份不一致
  则明确拒绝（`MODEL_CALL_LEASE_LOST`）；
- 额度窗口固定为保守滚动 60 秒并按 60 秒桶有界分桶：保留与窗口相交的边界桶（保守侧取整），
  只有整个窗口与边界桶都过期才清零，绝不清空仍在窗口内的在飞预占；
- 只有“确定未发送”才能释放占用；取消/异常退出按 UNKNOWN 保留占用，UNKNOWN 不能被当成
  未发送释放（`MODEL_CALL_OUTCOME_UNKNOWN`），本地租约过期也不等于远端已停；
- 续租只前进不倒退且不越过调用冻结的绝对期限；失约（非 ACTIVE、已过期、epoch/owner 不符）
  一律 `MODEL_CALL_LEASE_LOST`，执行方必须停手；
- 结算按证据分档：成功按实际 usage 释放多余预占；有完成证据但缺 usage、失败、UNKNOWN 一律
  保守保留（失败不假定供应商未计费）；SETTLED 幂等，重复结算不再二次扣减；
- 过期 ACTIVE 由有界清扫标记 UNKNOWN：只保留占用（不释放、不超发），等证据再结算释放。
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
import weakref
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_common import SharedSettings
from muad_contracts.model_pool import (
    MemoryJobCallOwner,
    ModelCallOwner,
    ModelCallSettlement,
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
from ..metrics import (
    MODEL_CAPACITY_METRIC,
    MODEL_LEASE_LOST_METRIC,
    MODEL_SETTLEMENT_METRIC,
    MODEL_SWEEP_METRIC,
    record_counter,
    record_outcome,
)

#: 额度窗口固定 60 秒（design §3.7：不提供任意算法切换）
QUOTA_WINDOW_SECONDS = 60
#: 保守滚动窗口保留的桶数：当前桶 + 与窗口相交的上一桶（边界桶向保守侧取整）
QUOTA_WINDOW_BUCKETS = 2
#: 首次预占的租约代次（续租/接管由 TASK-022 在同 attempt 上推进）
INITIAL_LEASE_EPOCH = 1
#: 过期 ACTIVE 租约单次清扫为 UNKNOWN 的默认/最大批量（有界批处理，避免长事务与热点锁）
EXPIRED_SWEEP_LIMIT = 50
EXPIRED_SWEEP_LIMIT_MAX = 200

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


@dataclass(frozen=True, slots=True)
class CapacityRenewal:
    """续租结果：租约身份与只前进不倒退的新期限（不超过冻结的绝对期限）。"""

    reservation_id: uuid.UUID
    call_attempt_id: uuid.UUID
    epoch: int
    lease_owner: str
    lease_until: datetime
    state: str


@dataclass(frozen=True, slots=True)
class _CapacityPlan:
    """一次租约状态推进对容量的影响（释放、结算与保留共用一份计划）。"""

    target: str
    release_inflight: bool
    release_request: bool = False
    #: 结算后该调用留在窗口里的 token 数；`None` = 保守保留原预占
    tokens_after: int | None = None


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


def _settlement_plan(settlement: ModelCallSettlement) -> _CapacityPlan:
    """结算分支：只有完成证据（或确定未发送）才释放在飞；缺 usage 一律保守保留预占。"""
    if not settlement.can_release_capacity:
        return _CapacityPlan(target="UNKNOWN", release_inflight=False)
    if settlement.outcome == "NOT_SENT":
        return _CapacityPlan(
            target="SETTLED", release_inflight=True, release_request=True, tokens_after=0
        )
    tokens_after = None if settlement.usage is None else settlement.usage.total_tokens
    return _CapacityPlan(target="SETTLED", release_inflight=True, tokens_after=tokens_after)


def _finished_release(lease: LeaseSnapshot, released: bool) -> CapacityRelease:
    """把租约当前状态投影为结果（不动任何计数）。"""
    return CapacityRelease(
        lease.reservation_id, lease.call_attempt_id, lease.epoch, lease.state, released
    )


def _settlement_status(state: str) -> str:
    """结算结局的**封闭词表**：只有 `SETTLED` 与 `UNKNOWN`（UNKNOWN 保留占用）两种终态。

    词表外的状态说明租约表被写坏：显式失败（`ValueError`），不把未知状态当标签发出去。
    """
    if state == "SETTLED":
        return "SETTLED"
    if state == "UNKNOWN":
        return "UNKNOWN"
    raise ValueError(f"unexpected model lease state {state!r}")


def _parked_release(lease: LeaseSnapshot, plan: _CapacityPlan) -> CapacityRelease | None:
    """已终态/UNKNOWN 租约的幂等分支：`None` = 本次要真的推进容量与状态。

    - SETTLED 幂等：重复结算/释放只回放既有终态，不再二次扣减；
    - UNKNOWN 上声称“确定未发送”是自相矛盾，明确拒绝（`MODEL_CALL_OUTCOME_UNKNOWN`）；
    - UNKNOWN 上无完成证据的结算保持原样（保留占用），有证据才允许结算释放。
    """
    if lease.state == "SETTLED":
        return _finished_release(lease, False)
    if lease.state != "UNKNOWN":
        return None
    if plan.release_request:
        raise AppError(ErrorCode.MODEL_CALL_OUTCOME_UNKNOWN)
    if plan.release_inflight:
        return None
    return _finished_release(lease, False)


async def _advance_transition(
    store: ModelCapacityStore,
    *,
    tenant_id: str,
    call_attempt_id: uuid.UUID,
    epoch: int,
    lease_owner: str,
    plan: _CapacityPlan,
) -> CapacityRelease:
    """状态推进的共用路径：先租约行、后共享容量行，经 `_parked_release` 处理幂等与拒绝。

    锁序改为 attempt 行 → 容量行（组→Key）：释放/结算与预占一样，**共享容量行是最后一步**，
    行锁只覆盖“加锁+写回+提交”，不再跨租约状态写入与幂等判定。`renew` 只锁 attempt 行、
    预占不锁既有 attempt 行，因此不存在“持容量等 attempt”的反向路径，无死锁环。
    """
    now = await store.database_clock()
    identity = await store.read_lease(tenant_id=tenant_id, call_attempt_id=call_attempt_id)
    if identity is None:
        raise AppError(ErrorCode.COMMON_NOT_FOUND)
    lease = await store.lock_attempt(tenant_id=tenant_id, call_attempt_id=call_attempt_id)
    if lease is None:  # pragma: no cover - 无锁读与行锁之间不可能删除
        raise AppError(ErrorCode.COMMON_NOT_FOUND)
    try:
        _require_lease_holder(lease, epoch=epoch, lease_owner=lease_owner)
    except AppError:
        # 失约停手必须留痕：执行方不能靠一次状态推进把失效的执行权续到未来。
        record_outcome(MODEL_LEASE_LOST_METRIC, "transition")
        raise
    parked = _parked_release(lease, plan)
    if parked is not None:
        return parked
    updated = await store.write_lease_state(
        tenant_id=tenant_id, call_attempt_id=call_attempt_id, state=plan.target, now=now
    )
    # 共享容量行是最后一步：每行一条 UPDATE，行锁只覆盖语句到提交。
    released_tokens = (
        0
        if plan.tokens_after is None
        else lease.reserved_tokens - plan.tokens_after
    )
    for resource_type, resource_id in (
        (RESOURCE_QUOTA_GROUP, identity.quota_group_id),
        (RESOURCE_KEY, identity.key_resource_id),
    ):
        if resource_id is None:
            continue
        await store.ensure_capacity_row(
            tenant_id=tenant_id,
            resource_type=resource_type,
            resource_id=resource_id,
            window_start=_bucket_start(now),
        )
        await store.release_capacity(
            tenant_id=tenant_id,
            resource_type=resource_type,
            resource_id=resource_id,
            release_inflight=plan.release_inflight,
            release_request=plan.release_request,
            released_tokens=released_tokens,
            lease_create_time=lease.create_time,
            now=now,
        )
    return _finished_release(updated, plan.release_inflight)


#: 每个进程允许同时在数据库上等待池锁的会话数（其余调用在 asyncio 信号量上排队）。
DEFAULT_POOL_LOCK_CONCURRENCY = 64


class PoolLockGate:
    """限制同时在 DB 上排队（持行锁/退避重试）的会话数：其余调用不占数据库连接。

    等待中的每个会话都持有一条 DB 连接（事务未提交）。800 用户实测：数百个模型调用同时
    排队吃光 runtime 连接池，把准入拖到 16s。闸门只改变**谁在 DB 上排队**，不改变选择
    语义与吞吐；`max_concurrency` 是可观测的实际上限（供容量测试断言）。
    """

    def __init__(self, limit: int = DEFAULT_POOL_LOCK_CONCURRENCY) -> None:
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("pool lock concurrency limit must be a positive int")
        self._limit = limit
        self._semaphore = asyncio.Semaphore(limit)
        self._active = 0
        self._max_concurrency = 0

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def max_concurrency(self) -> int:
        """观测到的同时在 DB 上等锁的最大会话数（不超过 `limit`）。"""
        return self._max_concurrency

    @asynccontextmanager
    async def hold(self) -> AsyncIterator[None]:
        async with self._semaphore:
            self._active += 1
            self._max_concurrency = max(self._max_concurrency, self._active)
            try:
                yield
            finally:
                self._active -= 1


#: 每个事件循环一份闸门：分配器按 Run/调用装配，闸门必须进程级共享才有效。
_process_gates: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, PoolLockGate] = (
    weakref.WeakKeyDictionary()
)


def process_pool_lock_gate(limit: int = DEFAULT_POOL_LOCK_CONCURRENCY) -> PoolLockGate:
    """取当前事件循环的进程级闸门（无运行循环时退化为独立实例，仅测试装配期发生）。"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return PoolLockGate(limit)
    gate = _process_gates.get(loop)
    if gate is None or gate.limit != limit:
        gate = PoolLockGate(limit)
        _process_gates[loop] = gate
    return gate


logger = logging.getLogger(__name__)


class ModelCapacityService:
    def __init__(
        self,
        session_factory_provider: SessionFactoryProvider,
        *,
        lease_duration_sec: int | None = None,
        store_factory: StoreFactory = ModelCapacityStore,
        gate: PoolLockGate | None = None,
    ) -> None:
        self._session_factory_provider = session_factory_provider
        self._gate = gate or process_pool_lock_gate()
        self._lease_seconds = (
            lease_duration_sec
            if lease_duration_sec is not None
            else SharedSettings().model_call_lease_sec
        )
        self._store_factory = store_factory

    @property
    def gate(self) -> PoolLockGate:
        """进程内闸门：调用方（分配器）必须与预占共享同一实例。"""
        return self._gate

    async def reserve(
        self, *, tenant_id: str, lease_owner: str, request: ModelCapacityReservationRequest
    ) -> CapacityReservation:
        """原子预占：按 workload 记裁决结局（拒绝按目录错误码，不静默降级）。"""
        try:
            reservation = await self._reserve_in(
                tenant_id=tenant_id, lease_owner=lease_owner, request=request
            )
        except AppError as exc:
            record_outcome(MODEL_CAPACITY_METRIC, exc.code, {"workload": request.workload})
            raise
        record_outcome(
            MODEL_CAPACITY_METRIC,
            "REUSED" if reservation.reused else "RESERVED",
            {"workload": request.workload},
        )
        return reservation

    async def _reserve_in(
        self, *, tenant_id: str, lease_owner: str, request: ModelCapacityReservationRequest
    ) -> CapacityReservation:
        """预占本体：判定与计数由每条条件 UPDATE 完成，不跨语句持共享行锁。

        顺序：读上限/时钟（无锁）→ 按需建行（不锁）→ 同 attempt 身份锁 → 读 attempt
        （重放即复用）→ 写 lease → 组行/Key 行各一条条件 UPDATE。容量更新失败时整个事务
        回滚（lease 与组行计数一起消失）。旧实现在行锁内读-判-写 6 条语句，单 Key 的并发
        因此串行（800 用户实测：预占 P50 等待 3.3s）；现在行锁只覆盖一条 UPDATE 到提交。
        """
        waited = time.monotonic()
        async with self._gate.hold(), self._session_factory_provider()() as session, session.begin():
            gate_ms = int((time.monotonic() - waited) * 1000)
            started = time.monotonic()
            store = self._store_factory(session)
            key_limits, group_limits = await _require_limits(store, tenant_id=tenant_id, request=request)
            now = await store.database_clock()
            lease_until = _lease_until(now, request.deadline_at, self._lease_seconds)
            bucket = _bucket_start(now)
            if request.quota_group_id is not None:
                await store.ensure_capacity_row(
                    tenant_id=tenant_id,
                    resource_type=RESOURCE_QUOTA_GROUP,
                    resource_id=request.quota_group_id,
                    window_start=bucket,
                )
            await store.ensure_capacity_row(
                tenant_id=tenant_id,
                resource_type=RESOURCE_KEY,
                resource_id=request.key_resource_id,
                window_start=bucket,
            )
            await store.lock_attempt_selection(
                tenant_id=tenant_id, call_attempt_id=request.call_attempt_id
            )
            attempt = await store.read_attempt(
                tenant_id=tenant_id, call_attempt_id=request.call_attempt_id
            )
            if attempt is not None:
                _require_same_attempt(attempt, request)
                return _reservation(attempt, reused=True)
            lease = await store.insert_lease(
                tenant_id=tenant_id,
                write=_lease_write(
                    request, lease_owner=lease_owner, epoch=INITIAL_LEASE_EPOCH,
                    lease_until=lease_until,
                ),
                now=now,
            )
            span = QUOTA_WINDOW_SECONDS * QUOTA_WINDOW_BUCKETS
            if request.quota_group_id is not None and group_limits is not None:
                admitted = await store.try_admit_capacity(
                    tenant_id=tenant_id,
                    resource_type=RESOURCE_QUOTA_GROUP,
                    resource_id=request.quota_group_id,
                    bucket=bucket,
                    span_seconds=span,
                    tokens=request.reserved_tokens,
                    limits=group_limits,
                    now=now,
                )
                if admitted is None:
                    raise AppError(ErrorCode.MODEL_CAPACITY_UNAVAILABLE)
            admitted = await store.try_admit_capacity(
                tenant_id=tenant_id,
                resource_type=RESOURCE_KEY,
                resource_id=request.key_resource_id,
                bucket=bucket,
                span_seconds=span,
                tokens=request.reserved_tokens,
                limits=key_limits,
                now=now,
            )
            if admitted is None:
                raise AppError(ErrorCode.MODEL_CAPACITY_UNAVAILABLE)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            if gate_ms + elapsed_ms > 1000:
                logger.warning(
                    "model_reserve_slow",
                    extra={
                        "tenant_id": tenant_id,
                        "gate_ms": gate_ms,
                        "elapsed_ms": elapsed_ms,
                        "model_id": str(request.model_id),
                    },
                )
            return _reservation(lease, reused=False)

    async def release_unsent(
        self, *, tenant_id: str, call_attempt_id: uuid.UUID, epoch: int, lease_owner: str
    ) -> CapacityRelease:
        """“确定未发送”释放：回收在飞与窗口计数；重复释放只回放既有终态。"""
        return await self._advance(
            tenant_id=tenant_id,
            call_attempt_id=call_attempt_id,
            epoch=epoch,
            lease_owner=lease_owner,
            plan=_CapacityPlan(
                target="SETTLED", release_inflight=True, release_request=True, tokens_after=0
            ),
        )

    async def mark_unknown(
        self, *, tenant_id: str, call_attempt_id: uuid.UUID, epoch: int, lease_owner: str
    ) -> CapacityRelease:
        """无法确认远端结束时保留占用（取消/异常退出），不释放任何容量。"""
        return await self._advance(
            tenant_id=tenant_id,
            call_attempt_id=call_attempt_id,
            epoch=epoch,
            lease_owner=lease_owner,
            plan=_CapacityPlan(target="UNKNOWN", release_inflight=False),
        )

    async def renew(
        self,
        *,
        tenant_id: str,
        call_attempt_id: uuid.UUID,
        epoch: int,
        lease_owner: str,
        deadline_at: datetime,
    ) -> CapacityRenewal:
        """续租：核 epoch/owner 且当前租约仍有效；期限只前进不倒退且不越过绝对期限。

        失约（非 ACTIVE、已过期、epoch/owner 不符）一律 `MODEL_CALL_LEASE_LOST`：执行方必须
        停手，不能靠一次心跳把失效的执行权续到未来（同 Worker 租约续租口径）。续租不触碰
        容量行，只取 attempt 行锁（固定锁序的最后一步），不引入新的锁序。
        """
        async with self._session_factory_provider()() as session, session.begin():
            store = self._store_factory(session)
            now = await store.database_clock()
            lease = await store.lock_attempt(tenant_id=tenant_id, call_attempt_id=call_attempt_id)
            if lease is None:
                raise AppError(ErrorCode.COMMON_NOT_FOUND)
            try:
                _require_lease_holder(lease, epoch=epoch, lease_owner=lease_owner)
                if lease.state != "ACTIVE" or lease.lease_until <= now:
                    raise AppError(ErrorCode.MODEL_CALL_LEASE_LOST)
            except AppError:
                record_outcome(MODEL_LEASE_LOST_METRIC, "renew")
                raise
            extended = max(lease.lease_until, _lease_until(now, deadline_at, self._lease_seconds))
            updated = await store.write_lease_until(
                tenant_id=tenant_id,
                call_attempt_id=call_attempt_id,
                lease_until=extended,
                now=now,
            )
            return CapacityRenewal(
                updated.reservation_id,
                updated.call_attempt_id,
                updated.epoch,
                updated.lease_owner,
                updated.lease_until,
                updated.state,
            )

    async def settle(
        self,
        *,
        tenant_id: str,
        call_attempt_id: uuid.UUID,
        settlement: ModelCallSettlement,
    ) -> CapacityRelease:
        """按完成证据结算：成功按实际 usage 释放多余预占，缺证据一律保留占用。

        - `NOT_SENT`（确定未发送）→ SETTLED，回收在飞与窗口计数；
        - `SUCCEEDED`/`FAILED` + 远端完成证据 → SETTLED 并释放在飞；有 usage 按实际用量替换
          预占，无 usage 保留保守预占（失败不假定供应商未计费）；
        - `UNKNOWN` 或缺完成证据 → UNKNOWN 保留占用（不释放、不超发），等证据再结算；
        - SETTLED 幂等：重复结算不再二次扣减；UNKNOWN 上声称未发送即
          `MODEL_CALL_OUTCOME_UNKNOWN`。
        """
        return await self._advance(
            tenant_id=tenant_id,
            call_attempt_id=call_attempt_id,
            epoch=settlement.epoch,
            lease_owner=settlement.lease_owner,
            plan=_settlement_plan(settlement),
        )

    async def sweep_expired(
        self, *, tenant_id: str, limit: int = EXPIRED_SWEEP_LIMIT
    ) -> list[CapacityRelease]:
        """把已过期仍 ACTIVE 的租约标记 UNKNOWN（有界批处理，只保留占用不释放）。

        本地租约过期只说明本进程失去执行权，不证明远端已停：清扫不写容量行，`inflight` 与
        窗口计数原样保留，后续只能凭完成证据经 `settle` 释放。扫描取行锁且 `SKIP LOCKED`，
        多实例重复清扫幂等。
        """
        if not 1 <= limit <= EXPIRED_SWEEP_LIMIT_MAX:
            raise ValueError(f"limit must be within 1..{EXPIRED_SWEEP_LIMIT_MAX}")
        async with self._session_factory_provider()() as session, session.begin():
            store = self._store_factory(session)
            now = await store.database_clock()
            expired = await store.lock_expired_active(tenant_id=tenant_id, now=now, limit=limit)
            swept: list[CapacityRelease] = []
            for lease in expired:
                updated = await store.write_lease_state(
                    tenant_id=tenant_id,
                    call_attempt_id=lease.call_attempt_id,
                    state="UNKNOWN",
                    now=now,
                )
                swept.append(_finished_release(updated, False))
            if swept:
                # 批量记在 amount：条数进 label 会裂出无界时间序列（同 artifact 字节口径）。
                record_counter(MODEL_SWEEP_METRIC, len(swept))
            return swept

    async def ensure_lease_held(
        self, *, tenant_id: str, call_attempt_id: uuid.UUID, epoch: int, lease_owner: str
    ) -> None:
        """执行前的失约核验：租约被结算/接管/过期即 `MODEL_CALL_LEASE_LOST`（停手）。"""
        async with self._session_factory_provider()() as session:
            store = self._store_factory(session)
            lease = await store.read_lease(tenant_id=tenant_id, call_attempt_id=call_attempt_id)
            now = await store.database_clock()
        if lease is None or lease.epoch != epoch or lease.lease_owner != lease_owner:
            record_outcome(MODEL_LEASE_LOST_METRIC, "execute")
            raise AppError(ErrorCode.MODEL_CALL_LEASE_LOST)
        if lease.state != "ACTIVE" or lease.lease_until <= now:
            record_outcome(MODEL_LEASE_LOST_METRIC, "execute")
            raise AppError(ErrorCode.MODEL_CALL_LEASE_LOST)

    async def _advance(
        self,
        *,
        tenant_id: str,
        call_attempt_id: uuid.UUID,
        epoch: int,
        lease_owner: str,
        plan: _CapacityPlan,
    ) -> CapacityRelease:
        async with self._session_factory_provider()() as session, session.begin():
            release = await _advance_transition(
                self._store_factory(session),
                tenant_id=tenant_id,
                call_attempt_id=call_attempt_id,
                epoch=epoch,
                lease_owner=lease_owner,
                plan=plan,
            )
        # 结算结局按**结果状态**计数：UNKNOWN（保留占用）与 SETTLED 是不同样本。
        record_outcome(MODEL_SETTLEMENT_METRIC, _settlement_status(release.state))
        return release
