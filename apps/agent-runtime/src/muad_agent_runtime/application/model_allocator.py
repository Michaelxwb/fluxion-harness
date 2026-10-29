"""加权均衡、共享 epoch 归一与工作负载公平调度（设计 §3.1 ADR-04、§3.3、§3.7）。

一段调用尝试分两个短事务，中间不持有任何锁：

1. **选择事务（池 advisory 锁内）**：扫描冻结候选与实时事实（成员/凭据启用、Key 与共享
   额度组的在飞额度、`runtime.model_dispatch_state` 计数），按 `dispatch_count/weight` 最小者
   排序，平局按归一化在飞量与稳定轮转顺序决定；被选中成员在同一条 UPDATE 里按共享 epoch
   归一后计一次尝试。工作负载份额与老化门也在此事务内判定。
2. **预占事务**：把选中成员的冻结身份交给既有 `ModelCapacityService.reserve`，由它按
   池→quota_group→key→attempt 固定锁序完成额度预占与租约写入。

设计取舍（诚实记录）：选择与计数在池锁内一次提交，因此"预占成功同时递增计数"在正常路径
成立；预占随后被别的池抢走额度时按 `MODEL_CAPACITY_UNAVAILABLE` 明确回压，本次尝试已经
计入（契约的"重试也计一次尝试"），且绝不超发。额度判定与窗口算法**只有一处实现**
（`ModelCapacityService`），本模块不复制，避免与调用续租/结算事实漂移。

- 共享 epoch：`dispatch_epoch = floor(PG 时钟秒 / dispatch_epoch_sec)`，跨实例同值；计数按
  epoch 归一而不逐请求全量清零；`policy_hash` 标识冻结策略版本，不同版本分布分别解释。
- 工作负载公平：交互与探针不受份额门限制（探针并发由 Console 探针预算与 API-17 占额约束）；
  后台按冻结的 `background_weight/interactive_weight` 让路给交互，真实等待超过
  `aging_interval_sec` 后必须可进展（老化），不会永久饿死。
- 锁内不联网：两个事务都只读写 PG；凭据解析在网络侧另行进行。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from fractions import Fraction
from typing import Literal

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_common.db_counters import db_stage_scope
from muad_contracts.model_pool import ModelCallOwner, ModelCapacityReservationRequest
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.db import SessionFactoryProvider
from ..infrastructure.model_capacity_store import (
    RESOURCE_KEY,
    RESOURCE_QUOTA_GROUP,
    CredentialLimits,
    LeaseSnapshot,
)
from ..infrastructure.model_dispatch_store import (
    DispatchState,
    FrozenCandidate,
    LiveMember,
    ModelDispatchStore,
)
from ..metrics import MODEL_CALL_SECONDS, record_latency
from .model_capacity_service import ModelCapacityService, PoolLockGate

logger = logging.getLogger(__name__)

#: 被延迟的工作负载原因：物理额度都满了（或绑定额度不足）
DEFER_REASON_CAPACITY = "NO_CAPACITY"
#: 被延迟的工作负载原因：后台份额让路给交互，尚未老化
DEFER_REASON_WORKLOAD = "WORKLOAD_SHARE"

WorkloadName = Literal["INTERACTIVE", "BACKGROUND", "PROBE"]
StoreFactory = Callable[[AsyncSession], ModelDispatchStore]


@dataclass(frozen=True, slots=True)
class ModelDispatchPolicy:
    """一次 Run/job 冻结的调度策略版本（TASK-024 写入快照；本模块校验其自洽）。"""

    dispatch_epoch_sec: int
    interactive_weight: int
    background_weight: int
    aging_interval_sec: int
    policy_hash: str

    @classmethod
    def frozen(
        cls,
        *,
        dispatch_epoch_sec: int,
        interactive_weight: int,
        background_weight: int,
        aging_interval_sec: int,
    ) -> ModelDispatchPolicy:
        return cls(
            dispatch_epoch_sec=dispatch_epoch_sec,
            interactive_weight=interactive_weight,
            background_weight=background_weight,
            aging_interval_sec=aging_interval_sec,
            policy_hash=dispatch_policy_hash(
                dispatch_epoch_sec=dispatch_epoch_sec,
                interactive_weight=interactive_weight,
                background_weight=background_weight,
                aging_interval_sec=aging_interval_sec,
            ),
        )


@dataclass(frozen=True, slots=True)
class ModelMemberCandidate:
    """一名冻结成员的非密钥元信息：权重随 Run 冻结，实时事实另行读取。"""

    deployment_id: uuid.UUID
    credential_id: uuid.UUID
    credential_revision: int
    key_resource_id: uuid.UUID
    quota_group_id: uuid.UUID | None
    weight: int

    @classmethod
    def of(cls, frozen: FrozenCandidate) -> ModelMemberCandidate:
        """把短事务外读到的候选元信息映射成调度输入（身份逐字段搬运，不做补默认）。"""
        return cls(
            deployment_id=frozen.deployment_id,
            credential_id=frozen.credential_id,
            credential_revision=frozen.credential_revision,
            key_resource_id=frozen.key_resource_id,
            quota_group_id=frozen.quota_group_id,
            weight=frozen.weight,
        )


@dataclass(frozen=True, slots=True)
class ModelAllocationRequest:
    """一次模型调用尝试的调度输入；`wait_since` 是模型资源等待起点，重试不刷新。"""

    call_attempt_id: uuid.UUID
    owner: ModelCallOwner
    workload: WorkloadName
    deadline_at: datetime
    reserved_tokens: int
    wait_since: datetime


@dataclass(frozen=True, slots=True)
class ModelAllocation:
    """调度结果：选中成员的冻结身份 + 真实租约事实 + 本次计入的调度计数。"""

    reservation_id: uuid.UUID
    call_attempt_id: uuid.UUID
    lease_epoch: int
    lease_owner: str
    state: str
    issued_at: datetime
    lease_until: datetime
    reserved_tokens: int
    reused: bool
    member: ModelMemberCandidate
    policy_hash: str
    #: 复用既有 attempt 时本次未做选择与计数，两个字段为空
    dispatch_epoch: int | None
    dispatch_count: int | None


def dispatch_policy_hash(
    *,
    dispatch_epoch_sec: int,
    interactive_weight: int,
    background_weight: int,
    aging_interval_sec: int,
) -> str:
    """冻结策略版本的规范身份（≤128 字符列；同一语义只有一个哈希）。"""
    canonical = json.dumps(
        {
            "dispatch_epoch_sec": dispatch_epoch_sec,
            "interactive_weight": interactive_weight,
            "background_weight": background_weight,
            "aging_interval_sec": aging_interval_sec,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(f"muad:model-dispatch-policy:v1:{canonical}".encode()).hexdigest()


def dispatch_epoch_for(now: datetime, epoch_sec: int) -> int:
    """共享 epoch 序号：由 PG 服务端时间派生，跨实例同值（不使用本机墙钟）。"""
    return int(now.timestamp()) // epoch_sec


#: 多成员并发选择拿不到行（其它调用正持行）时的有界等待：1ms 起、50ms 封顶、5s 上限。
#: 等待而不是阻塞兜底：阻塞会让所有等待者堆在排序第一的行上，破坏 B-223 的均衡。
_SELECT_RETRY_BASE_SEC = 0.001
_SELECT_RETRY_MAX_SEC = 0.05
_SELECT_ACQUIRE_TIMEOUT_SEC = 5.0


@dataclass(frozen=True, slots=True)
class _CandidateLoad:
    """一名候选的调度事实（`executable` 为假表示当前不可执行）。"""

    candidate: ModelMemberCandidate
    executable: bool
    inflight: int
    max_inflight: int
    effective_count: int
    last_picked_at: datetime | None
    #: 该策略版本下是否已有计数行（缺失先建 0 行，否则原子选择看不到新成员）
    has_state: bool


@dataclass(frozen=True, slots=True)
class _Picked:
    member: ModelMemberCandidate
    epoch: int
    count: int
    last_picked_at: datetime | None


@dataclass(frozen=True, slots=True)
class _Replay:
    member: ModelMemberCandidate
    lease: LeaseSnapshot


Selection = _Picked | _Replay


def _require_policy(policy: ModelDispatchPolicy) -> None:
    """冻结策略必须自洽：范围为正、哈希与其字段一致（不接受伪造版本身份）。"""
    if min(
        policy.dispatch_epoch_sec,
        policy.interactive_weight,
        policy.background_weight,
        policy.aging_interval_sec,
    ) <= 0:
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
    expected = dispatch_policy_hash(
        dispatch_epoch_sec=policy.dispatch_epoch_sec,
        interactive_weight=policy.interactive_weight,
        background_weight=policy.background_weight,
        aging_interval_sec=policy.aging_interval_sec,
    )
    if policy.policy_hash != expected:
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)


def _require_within_deadline(now: datetime, deadline_at: datetime) -> None:
    """绝对期限已过就不再尝试：不占额度、也不计一次尝试。"""
    if deadline_at <= now:
        raise AppError(ErrorCode.MODEL_WAIT_TIMEOUT)


def _deferred(reason: str) -> AppError:
    return AppError(ErrorCode.MODEL_CAPACITY_UNAVAILABLE, data={"reason": reason})


def _unavailable(candidate: ModelMemberCandidate) -> _CandidateLoad:
    return _CandidateLoad(
        candidate=candidate,
        executable=False,
        inflight=0,
        max_inflight=1,
        effective_count=0,
        last_picked_at=None,
        has_state=False,
    )


def _binding_limits(
    limits: CredentialLimits,
    candidate: ModelMemberCandidate,
    key_inflight: int,
    group_inflight: int,
) -> tuple[int, int]:
    """最紧的绑定额度：共享额度组比 Key 更紧时以组为准（同一实际 Key 的容量是取交集）。"""
    key_max = limits.key.max_inflight
    if candidate.quota_group_id is None or limits.quota_group is None:
        return key_inflight, key_max
    if limits.quota_group.max_inflight < key_max:
        return group_inflight, limits.quota_group.max_inflight
    return key_inflight, key_max


async def _load_candidate(
    store: ModelDispatchStore,
    *,
    tenant_id: str,
    candidate: ModelMemberCandidate,
    live: dict[uuid.UUID, LiveMember],
    state: DispatchState | None,
    key_inflight: int,
    group_inflight: int,
    epoch: int,
) -> _CandidateLoad:
    """一名候选的实时可执行性与额度事实（成员/凭据停用或缺失即不可执行）。"""
    member = live.get(candidate.deployment_id)
    if member is None or not member.enabled or member.credential_id != candidate.credential_id:
        return _unavailable(candidate)
    limits = await store.read_limits(
        tenant_id=tenant_id,
        credential_id=candidate.credential_id,
        quota_group_id=candidate.quota_group_id,
    )
    if limits is None or not limits.enabled:
        return _unavailable(candidate)
    inflight, max_inflight = _binding_limits(limits, candidate, key_inflight, group_inflight)
    return _CandidateLoad(
        candidate=candidate,
        executable=True,
        inflight=inflight,
        max_inflight=max_inflight,
        effective_count=0 if state is None else state.count_in_epoch(epoch),
        last_picked_at=None if state is None else state.last_picked_at,
        has_state=state is not None,
    )


@dataclass(frozen=True, slots=True)
class _CandidateFacts:
    """池锁内一次有界扫描读到的实时事实（成员、计数、在飞额度）。"""

    live: dict[uuid.UUID, LiveMember]
    states: dict[uuid.UUID, DispatchState]
    key_inflight: dict[uuid.UUID, int]
    group_inflight: dict[uuid.UUID, int]


async def _read_candidate_facts(
    store: ModelDispatchStore,
    *,
    tenant_id: str,
    model_id: uuid.UUID,
    candidates: Sequence[ModelMemberCandidate],
    policy_hash: str,
) -> _CandidateFacts:
    group_ids = [
        candidate.quota_group_id
        for candidate in candidates
        if candidate.quota_group_id is not None
    ]
    return _CandidateFacts(
        live=await store.read_live_members(tenant_id=tenant_id, model_id=model_id),
        states=await store.read_dispatch_states(
            tenant_id=tenant_id,
            model_id=model_id,
            policy_hash=policy_hash,
            deployment_ids=[candidate.deployment_id for candidate in candidates],
        ),
        key_inflight=await store.read_capacity_inflight(
            tenant_id=tenant_id,
            resource_type=RESOURCE_KEY,
            resource_ids=[candidate.key_resource_id for candidate in candidates],
        ),
        group_inflight=await store.read_capacity_inflight(
            tenant_id=tenant_id,
            resource_type=RESOURCE_QUOTA_GROUP,
            resource_ids=group_ids,
        ),
    )


async def _scan_candidates(
    store: ModelDispatchStore,
    *,
    tenant_id: str,
    model_id: uuid.UUID,
    candidates: Sequence[ModelMemberCandidate],
    epoch: int,
    policy_hash: str,
    now: datetime,
) -> tuple[list[_CandidateLoad], dict[str, int]]:
    """一次有界候选扫描：成员事实、调度计数、在飞与工作负载分布（选择前读快照）。"""
    facts = await _read_candidate_facts(
        store,
        tenant_id=tenant_id,
        model_id=model_id,
        candidates=candidates,
        policy_hash=policy_hash,
    )
    loads = [
        await _load_candidate(
            store,
            tenant_id=tenant_id,
            candidate=candidate,
            live=facts.live,
            state=facts.states.get(candidate.deployment_id),
            key_inflight=facts.key_inflight.get(candidate.key_resource_id, 0),
            group_inflight=0
            if candidate.quota_group_id is None
            else facts.group_inflight.get(candidate.quota_group_id, 0),
            epoch=epoch,
        )
        for candidate in candidates
    ]
    workload_inflight = await store.read_workload_inflight(
        tenant_id=tenant_id,
        deployment_ids=[candidate.deployment_id for candidate in candidates],
        now=now,
    )
    return loads, workload_inflight


def _workload_share_allows(
    *,
    workload: WorkloadName,
    policy: ModelDispatchPolicy,
    workload_inflight: dict[str, int],
    wait_since: datetime,
    now: datetime,
) -> bool:
    """交互/探针不受份额门限制；后台让路给交互直到真实等待超过老化间隔。"""
    if workload != "BACKGROUND":
        return True
    if now - wait_since >= timedelta(seconds=policy.aging_interval_sec):
        return True
    interactive = workload_inflight.get("INTERACTIVE", 0)
    background = workload_inflight.get("BACKGROUND", 0)
    if interactive + background == 0:
        return True
    return (background + 1) * policy.interactive_weight <= (interactive + 1) * policy.background_weight


def _rank_key(load: _CandidateLoad) -> tuple[Fraction, Fraction, float, uuid.UUID]:
    """排序：dispatch_count/weight → 归一化在飞量 → 稳定轮转（最久未选，再按成员 ID）。"""
    return (
        Fraction(load.effective_count, load.candidate.weight),
        Fraction(load.inflight, load.max_inflight),
        load.last_picked_at.timestamp() if load.last_picked_at is not None else 0.0,
        load.candidate.deployment_id,
    )


def _select_load(
    *,
    loads: Sequence[_CandidateLoad],
    workload_inflight: dict[str, int],
    request: ModelAllocationRequest,
    policy: ModelDispatchPolicy,
    now: datetime,
) -> _CandidateLoad:
    """不可执行 → 没有额度 → 份额/老化门的顺序判定，最后给出排序最小的候选。"""
    executable = [load for load in loads if load.executable]
    if not executable:
        raise AppError(ErrorCode.CREDENTIAL_MISSING)
    available = [load for load in executable if load.inflight + 1 <= load.max_inflight]
    if not available:
        raise _deferred(DEFER_REASON_CAPACITY)
    if not _workload_share_allows(
        workload=request.workload,
        policy=policy,
        workload_inflight=workload_inflight,
        wait_since=request.wait_since,
        now=now,
    ):
        raise _deferred(DEFER_REASON_WORKLOAD)
    return min(available, key=_rank_key)


def _member_by_id(
    candidates: Sequence[ModelMemberCandidate], deployment_id: uuid.UUID
) -> ModelMemberCandidate:
    """把原子选择返回的成员 ID 映射回调用方冻结的候选（未知成员即身份已失效）。"""
    for candidate in candidates:
        if candidate.deployment_id == deployment_id:
            return candidate
    raise AppError(ErrorCode.MODEL_CALL_LEASE_LOST)


def _require_frozen_member(
    candidates: Sequence[ModelMemberCandidate], lease: LeaseSnapshot
) -> ModelMemberCandidate:
    """既有 attempt 的冻结身份必须仍在调用方的冻结候选集内，否则不能继续提交。"""
    for candidate in candidates:
        if (
            candidate.deployment_id == lease.deployment_id
            and candidate.credential_id == lease.credential_id
            and candidate.key_resource_id == lease.key_resource_id
            and candidate.quota_group_id == lease.quota_group_id
        ):
            return candidate
    raise AppError(ErrorCode.MODEL_CALL_LEASE_LOST)


def _reservation_request(
    *, model_id: uuid.UUID, request: ModelAllocationRequest, member: ModelMemberCandidate
) -> ModelCapacityReservationRequest:
    return ModelCapacityReservationRequest(
        call_attempt_id=request.call_attempt_id,
        model_id=model_id,
        deployment_id=member.deployment_id,
        credential_id=member.credential_id,
        credential_revision=member.credential_revision,
        key_resource_id=member.key_resource_id,
        quota_group_id=member.quota_group_id,
        owner=request.owner,
        workload=request.workload,
        deadline_at=request.deadline_at,
        reserved_tokens=request.reserved_tokens,
    )


def _replayed(lease: LeaseSnapshot, member: ModelMemberCandidate, policy_hash: str) -> ModelAllocation:
    return ModelAllocation(
        reservation_id=lease.reservation_id,
        call_attempt_id=lease.call_attempt_id,
        lease_epoch=lease.epoch,
        lease_owner=lease.lease_owner,
        state=lease.state,
        issued_at=lease.create_time,
        lease_until=lease.lease_until,
        reserved_tokens=lease.reserved_tokens,
        reused=True,
        member=member,
        policy_hash=policy_hash,
        dispatch_epoch=None,
        dispatch_count=None,
    )


class ModelAllocator:
    """加权均衡的成员分配端口：池锁内选择与计数，随后由容量服务预占（预占只用行锁）。"""

    def __init__(
        self,
        session_factory_provider: SessionFactoryProvider,
        *,
        capacity: ModelCapacityService | None = None,
        store_factory: StoreFactory = ModelDispatchStore,
        gate: PoolLockGate | None = None,
    ) -> None:
        self._session_factory_provider = session_factory_provider
        self._capacity = capacity or ModelCapacityService(session_factory_provider, gate=gate)
        # 选择与预占共享同一闸门：两者都会在 DB 上等锁，超出的调用必须在进程内排队。
        self._gate = gate or self._capacity.gate
        self._store_factory = store_factory

    @property
    def gate(self) -> PoolLockGate:
        """与容量预占共享的进程内闸门（可观测：见 `PoolLockGate.max_concurrency`）。"""
        return self._gate

    @db_stage_scope("model")
    async def allocate(
        self,
        *,
        tenant_id: str,
        lease_owner: str,
        model_id: uuid.UUID,
        request: ModelAllocationRequest,
        candidates: Sequence[ModelMemberCandidate],
        policy: ModelDispatchPolicy,
    ) -> ModelAllocation:
        """选择成员并预占额度；同一 attempt 重放复用既有租约且不重复计数。"""
        _require_policy(policy)
        select_started = time.monotonic()
        selection = await self._select(
            tenant_id=tenant_id,
            model_id=model_id,
            request=request,
            candidates=candidates,
            policy=policy,
        )
        record_latency(
            MODEL_CALL_SECONDS, time.monotonic() - select_started, {"stage": "select"}
        )
        if isinstance(selection, _Replay):
            return _replayed(selection.lease, selection.member, policy.policy_hash)
        reserve_started = time.monotonic()
        reservation = await self._capacity.reserve(
            tenant_id=tenant_id,
            lease_owner=lease_owner,
            request=_reservation_request(
                model_id=model_id, request=request, member=selection.member
            ),
        )
        record_latency(
            MODEL_CALL_SECONDS,
            time.monotonic() - reserve_started,
            {"stage": "capacity_reserve"},
        )
        return ModelAllocation(
            reservation_id=reservation.reservation_id,
            call_attempt_id=reservation.call_attempt_id,
            lease_epoch=reservation.epoch,
            lease_owner=reservation.lease_owner,
            state=reservation.state,
            issued_at=reservation.issued_at,
            lease_until=reservation.lease_until,
            reserved_tokens=reservation.reserved_tokens,
            reused=reservation.reused,
            member=selection.member,
            policy_hash=policy.policy_hash,
            dispatch_epoch=selection.epoch,
            dispatch_count=selection.count,
        )

    async def _select(
        self,
        *,
        tenant_id: str,
        model_id: uuid.UUID,
        request: ModelAllocationRequest,
        candidates: Sequence[ModelMemberCandidate],
        policy: ModelDispatchPolicy,
    ) -> Selection:
        """选择事务：无池级全局锁；排序/取行/epoch 归一/计数由一条原子语句完成。

        行为契约（B-223）：顺序选择差 ≤1、权重按 count/weight、在飞归一后轮转。
        多成员用 `FOR UPDATE SKIP LOCKED` 让并发调用各取一行；最小计数行被持锁时会被跳过，
        因此真实并发下只保证有界偏差（每个成员相对均值 ±1），不保证严格均分。拿不到行时短
        退避重试；单成员直接阻塞（只有一个成员时本来就必须串行）。同 attempt 的并发重试由
        一把独立的 attempt 身份锁串行，保证「重试只计一次尝试」。闸门限制同时在 DB 上排队
        的会话数。
        """
        waited = time.monotonic()
        async with self._gate.hold(), self._session_factory_provider()() as session, session.begin():
            gate_ms = int((time.monotonic() - waited) * 1000)
            # 把选择阶段的闸门等待单独成 stage：reserve 包含 gate，必须能拆开归因。
            record_latency(
                MODEL_CALL_SECONDS, gate_ms / 1000.0, {"stage": "gate_select"}
            )
            started = time.monotonic()
            store = self._store_factory(session)
            now = await store.database_clock()
            _require_within_deadline(now, request.deadline_at)
            await store.lock_attempt_selection(
                tenant_id=tenant_id, call_attempt_id=request.call_attempt_id
            )
            attempt = await store.read_attempt(
                tenant_id=tenant_id, call_attempt_id=request.call_attempt_id
            )
            if attempt is not None:
                return _Replay(member=_require_frozen_member(candidates, attempt), lease=attempt)
            epoch = dispatch_epoch_for(now, policy.dispatch_epoch_sec)
            loads, workload_inflight = await _scan_candidates(
                store,
                tenant_id=tenant_id,
                model_id=model_id,
                candidates=candidates,
                epoch=epoch,
                policy_hash=policy.policy_hash,
                now=now,
            )
            # 只做门禁（不可执行/无额度/工作负载份额）：通过的集合由下面的原子语句排序。
            _select_load(
                loads=loads,
                workload_inflight=workload_inflight,
                request=request,
                policy=policy,
                now=now,
            )
            eligible = [
                load
                for load in loads
                if load.executable and load.inflight + 1 <= load.max_inflight
            ]
            missing = [load.candidate.deployment_id for load in eligible if not load.has_state]
            if missing:
                await store.seed_dispatch_states(
                    tenant_id=tenant_id,
                    model_id=model_id,
                    policy_hash=policy.policy_hash,
                    epoch=epoch,
                    deployment_ids=missing,
                    now=now,
                )
            state = await self._pick_with_contention(
                store,
                tenant_id=tenant_id,
                model_id=model_id,
                policy_hash=policy.policy_hash,
                epoch=epoch,
                now=now,
                picks=[
                    (
                        load.candidate.deployment_id,
                        load.candidate.weight,
                        load.inflight / load.max_inflight if load.max_inflight else 0.0,
                    )
                    for load in eligible
                ],
            )
            elapsed_ms = int((time.monotonic() - started) * 1000)
            if gate_ms + elapsed_ms > 1000:
                logger.warning(
                    "model_select_slow",
                    extra={
                        "tenant_id": tenant_id,
                        "gate_ms": gate_ms,
                        "elapsed_ms": elapsed_ms,
                        "model_id": str(model_id),
                    },
                )
            return _Picked(
                member=_member_by_id(candidates, state.deployment_id),
                epoch=state.dispatch_epoch,
                count=state.dispatch_count,
                last_picked_at=state.last_picked_at,
            )

    async def _pick_with_contention(
        self,
        store: ModelDispatchStore,
        *,
        tenant_id: str,
        model_id: uuid.UUID,
        policy_hash: str,
        epoch: int,
        now: datetime,
        picks: Sequence[tuple[uuid.UUID, int, float]],
    ) -> DispatchState:
        """多成员并发各取各的行；全部被占时抖动退避重试（有界等待），不阻塞堆在单行上。"""
        if not picks:  # pragma: no cover - `_select_load` 已把无候选判成显式错误
            raise AppError(ErrorCode.MODEL_CAPACITY_UNAVAILABLE)
        if len(picks) == 1:
            state = await store.pick_and_bump_dispatch(
                tenant_id=tenant_id,
                model_id=model_id,
                policy_hash=policy_hash,
                epoch=epoch,
                now=now,
                picks=picks,
                skip_locked=False,
            )
            if state is None:  # pragma: no cover - 计数行已按需建好
                raise AppError(ErrorCode.MODEL_CAPACITY_UNAVAILABLE)
            return state
        deadline = time.monotonic() + _SELECT_ACQUIRE_TIMEOUT_SEC
        delay = _SELECT_RETRY_BASE_SEC
        while True:
            state = await store.pick_and_bump_dispatch(
                tenant_id=tenant_id,
                model_id=model_id,
                policy_hash=policy_hash,
                epoch=epoch,
                now=now,
                picks=picks,
                skip_locked=True,
            )
            if state is not None:
                return state
            if time.monotonic() >= deadline:
                raise AppError(ErrorCode.MODEL_CAPACITY_UNAVAILABLE)
            # 抖动：12 个等待者不要在同一毫秒一起回来抢同一行
            sleep_started = time.monotonic()
            await asyncio.sleep(delay * random.uniform(0.5, 1.5))
            record_latency(
                MODEL_CALL_SECONDS,
                time.monotonic() - sleep_started,
                {"stage": "select_pick_wait"},
            )
            delay = min(delay * 2, _SELECT_RETRY_MAX_SEC)
