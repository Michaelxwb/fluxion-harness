"""B-223：加权均衡、共享 epoch 归一与工作负载公平调度（真实 PG）。

真实边界（不 mock 存储、租约或调度计数）：真实 `ModelAllocator`/`ModelDispatchStore` 与真实
`ModelCapacityService`，真实 PostgreSQL 行（`runtime.model_dispatch_state` /
`runtime.model_capacity` / `runtime.model_call_lease`）；成员关系与额度上限读真实
`control.model_deployment` / `model_credential` / `model_quota_group`。

关键断言（Acceptance Contract B-223）：
- 等权空闲池预占次数差 ≤1（顺序与真实并发各一遍）；
- 按 `dispatch_count/weight` 与归一化在飞量稳定轮转，重试也计一次尝试；
- 低额度（Key 与共享额度组）不超发，被跳过的成员不写计数；
- 后台工作按冻结权重/老化可进展，交互高峰不被后台挤占；
- 调度计数按共享 epoch（PG 时钟派生）归一，不在每次请求上全量清零；
- 池锁内完成选择与计数、锁内不联网；不同 `policy_hash` 的分布分别解释。
"""

from __future__ import annotations

import asyncio
import hashlib
import socket
import uuid
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest
import sqlalchemy as sa
from muad_agent_runtime.application.model_allocator import (
    DEFER_REASON_CAPACITY,
    DEFER_REASON_WORKLOAD,
    ModelAllocation,
    ModelAllocationRequest,
    ModelAllocator,
    ModelDispatchPolicy,
    ModelMemberCandidate,
    dispatch_policy_hash,
)
from muad_agent_runtime.application.model_capacity_service import (
    ModelCapacityService,
    PoolLockGate,
)
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.model_capacity_store import (
    RESOURCE_KEY,
    RESOURCE_QUOTA_GROUP,
)
from muad_agent_runtime.infrastructure.model_dispatch_store import (
    ModelDispatchStore,
)
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_common import SharedSettings
from muad_contracts.model_pool import ModelCapacityReservationRequest, ProbeCallOwner
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


@dataclass(frozen=True, slots=True)
class Tenant:
    tenant_id: str


@dataclass(frozen=True, slots=True)
class Member:
    """一名物理成员（冻结候选元信息 + 真实 `model_deployment` 行的可读身份）。"""

    deployment_id: uuid.UUID
    credential_id: uuid.UUID
    key_resource_id: uuid.UUID
    quota_group_id: uuid.UUID | None
    weight: int

    def candidate(self) -> ModelMemberCandidate:
        return ModelMemberCandidate(
            deployment_id=self.deployment_id,
            credential_id=self.credential_id,
            credential_revision=1,
            key_resource_id=self.key_resource_id,
            quota_group_id=self.quota_group_id,
            weight=self.weight,
        )


@pytest.fixture
async def tenant() -> AsyncIterator[Tenant]:
    """每用例独立租户；结束只删本租户行，不动共享库其它批次数据。"""
    context = Tenant(tenant_id=f"test-{uuid.uuid4().hex[:20]}")
    try:
        yield context
    finally:
        factory = get_session_factory()
        async with factory() as session:
            for statement in (
                "DELETE FROM runtime.model_dispatch_state WHERE tenant_id = :tenant_id",
                "DELETE FROM runtime.model_call_lease WHERE tenant_id = :tenant_id",
                "DELETE FROM runtime.model_capacity WHERE tenant_id = :tenant_id",
                "DELETE FROM control.model_deployment WHERE tenant_id = :tenant_id",
                "DELETE FROM control.model_credential WHERE tenant_id = :tenant_id",
                "DELETE FROM control.model_quota_group WHERE tenant_id = :tenant_id",
                "DELETE FROM control.model_definition WHERE tenant_id = :tenant_id",
            ):
                await session.execute(sa.text(statement), {"tenant_id": context.tenant_id})
            await session.commit()


# --------------------------------------------------------------------------------------
# 真实控制面/运行时行播种
# --------------------------------------------------------------------------------------


async def _seed_model(tenant_id: str) -> uuid.UUID:
    model_id = uuid.uuid4()
    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "INSERT INTO control.model_definition (id, tenant_id, key, name, model_id)"
                " VALUES (:id, :tenant_id, :key, :name, :provider_model)"
            ).bindparams(
                id=model_id,
                tenant_id=tenant_id,
                key=f"model-{uuid.uuid4().hex[:12]}",
                name="pool-model",
                provider_model="gpt-4o-mini",
            )
        )
        await session.commit()
    return model_id


async def _seed_quota_group(
    tenant_id: str, *, max_inflight: int, rpm: int | None = None, tpm: int | None = None
) -> uuid.UUID:
    group_id = uuid.uuid4()
    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "INSERT INTO control.model_quota_group (id, tenant_id, key, rpm, tpm, max_inflight, revision)"
                " VALUES (:id, :tenant_id, :key, :rpm, :tpm, :max_inflight, 1)"
            ).bindparams(
                id=group_id,
                tenant_id=tenant_id,
                key=f"group-{uuid.uuid4().hex[:12]}",
                rpm=rpm,
                tpm=tpm,
                max_inflight=max_inflight,
            )
        )
        await session.commit()
    return group_id


async def _seed_member(
    tenant_id: str,
    model_id: uuid.UUID,
    *,
    weight: int = 1,
    key_max_inflight: int = 5,
    quota_group_id: uuid.UUID | None = None,
    member_enabled: bool = True,
    credential_enabled: bool = True,
) -> Member:
    """一名真实成员（`model_deployment` 行 + 同租户 `model_credential` 行）。

    `key_resource_id` 由测试给出：真实派生在 Console（ADR-08），本用例只验证调度与预占
    对同一冻结身份的合并，不复制 Owner 的派生实现。
    """
    credential_id, deployment_id, key_resource_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "INSERT INTO control.model_credential (id, tenant_id, key, provider_key, base_url, api_key,"
                " api_key_digest, quota_group_id, max_inflight, enabled, revision)"
                " VALUES (:id, :tenant_id, :key, 'openai', 'https://api.example.com/v1', :api_key,"
                " :digest, :quota_group_id, :max_inflight, :enabled, 1)"
            ).bindparams(
                id=credential_id,
                tenant_id=tenant_id,
                key=f"key-{uuid.uuid4().hex[:12]}",
                api_key=f"sk-test-{uuid.uuid4().hex}",
                digest=hashlib.sha256(uuid.uuid4().hex.encode()).hexdigest(),
                quota_group_id=quota_group_id,
                max_inflight=key_max_inflight,
                enabled=credential_enabled,
            )
        )
        await session.execute(
            sa.text(
                "INSERT INTO control.model_deployment (id, tenant_id, key, model_id, credential_id, weight,"
                " enabled) VALUES (:id, :tenant_id, :key, :model_id, :credential_id, :weight, :enabled)"
            ).bindparams(
                id=deployment_id,
                tenant_id=tenant_id,
                key=f"member-{uuid.uuid4().hex[:12]}",
                model_id=model_id,
                credential_id=credential_id,
                weight=weight,
                enabled=member_enabled,
            )
        )
        await session.commit()
    return Member(deployment_id, credential_id, key_resource_id, quota_group_id, weight)


def _policy(
    *,
    dispatch_epoch_sec: int = 3600,
    interactive_weight: int = 8,
    background_weight: int = 1,
    aging_interval_sec: int = 30,
) -> ModelDispatchPolicy:
    return ModelDispatchPolicy.frozen(
        dispatch_epoch_sec=dispatch_epoch_sec,
        interactive_weight=interactive_weight,
        background_weight=background_weight,
        aging_interval_sec=aging_interval_sec,
    )


def _allocator(
    *,
    store_factory: Callable[[AsyncSession], ModelDispatchStore] | None = None,
    order: list[str] | None = None,
    gate: PoolLockGate | None = None,
) -> ModelAllocator:
    if order is None:
        return ModelAllocator(
            get_session_factory,
            store_factory=store_factory or ModelDispatchStore,
            gate=gate,
        )
    recording: Callable[[AsyncSession], ModelDispatchStore] = (
        store_factory or (lambda session: _RecordingStore(session, order))
    )
    return ModelAllocator(
        get_session_factory,
        capacity=ModelCapacityService(
            get_session_factory, store_factory=recording, gate=gate
        ),
        store_factory=recording,
    )


async def _allocate(
    allocator: ModelAllocator,
    tenant: Tenant,
    model_id: uuid.UUID,
    *,
    candidates: Sequence[ModelMemberCandidate],
    policy: ModelDispatchPolicy,
    workload: str = "INTERACTIVE",
    attempt_id: uuid.UUID | None = None,
    deadline_in_sec: int = 300,
    wait_since: datetime | None = None,
    lease_owner: str = "rt-1",
    reserved_tokens: int = 100,
) -> ModelAllocation:
    request = ModelAllocationRequest(
        call_attempt_id=attempt_id or uuid.uuid4(),
        owner=ProbeCallOwner(probe_id=uuid.uuid4()),
        workload=workload,  # type: ignore[arg-type]
        deadline_at=datetime.now(UTC) + timedelta(seconds=deadline_in_sec),
        reserved_tokens=reserved_tokens,
        wait_since=wait_since or datetime.now(UTC),
    )
    return await allocator.allocate(
        tenant_id=tenant.tenant_id,
        lease_owner=lease_owner,
        model_id=model_id,
        request=request,
        candidates=candidates,
        policy=policy,
    )


async def _allocate_error(
    allocator: ModelAllocator, tenant: Tenant, model_id: uuid.UUID, **kwargs: Any
) -> AppError:
    with pytest.raises(AppError) as captured:
        await _allocate(allocator, tenant, model_id, **kwargs)
    return captured.value


# --------------------------------------------------------------------------------------
# 真实行读取（证据）
# --------------------------------------------------------------------------------------


async def _dispatch_rows(tenant_id: str) -> list[dict[str, Any]]:
    async with get_session_factory()() as session:
        rows = (
            await session.execute(
                sa.text(
                    "SELECT deployment_id, policy_hash, dispatch_epoch, dispatch_count, last_picked_at"
                    " FROM runtime.model_dispatch_state WHERE tenant_id = :tenant_id"
                ).bindparams(tenant_id=tenant_id)
            )
        ).mappings().all()
    return [dict(row) for row in rows]


async def _dispatch_counts(tenant_id: str) -> dict[uuid.UUID, int]:
    rows = await _dispatch_rows(tenant_id)
    return {row["deployment_id"]: row["dispatch_count"] for row in rows}


async def _capacity_row(
    tenant_id: str, resource_type: str, resource_id: uuid.UUID
) -> dict[str, Any]:
    async with get_session_factory()() as session:
        row = (
            await session.execute(
                sa.text(
                    "SELECT inflight, reserved_requests, reserved_tokens FROM runtime.model_capacity"
                    " WHERE tenant_id = :tenant_id AND resource_type = :resource_type"
                    " AND resource_id = :resource_id"
                ).bindparams(
                    tenant_id=tenant_id, resource_type=resource_type, resource_id=resource_id
                )
            )
        ).mappings().one_or_none()
    return {} if row is None else dict(row)


async def _lease_rows(tenant_id: str) -> list[dict[str, Any]]:
    async with get_session_factory()() as session:
        rows = (
            await session.execute(
                sa.text(
                    "SELECT call_attempt_id, deployment_id, key_resource_id, quota_group_id, workload,"
                    " state, epoch FROM runtime.model_call_lease WHERE tenant_id = :tenant_id"
                ).bindparams(tenant_id=tenant_id)
            )
        ).mappings().all()
    return [dict(row) for row in rows]


async def _set_dispatch_row(
    tenant_id: str,
    model_id: uuid.UUID,
    deployment_id: uuid.UUID,
    policy_hash: str,
    *,
    epoch: int,
    count: int,
) -> None:
    """直接写真实调度计数行（模拟"上一 epoch 留下的计数"与共享状态，不改实现判断）。"""
    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "INSERT INTO runtime.model_dispatch_state (tenant_id, model_id, deployment_id, policy_hash,"
                " dispatch_epoch, dispatch_count) VALUES (:tenant_id, :model_id, :deployment_id,"
                " :policy_hash, :epoch, :count) ON CONFLICT (tenant_id, model_id, deployment_id,"
                " policy_hash) DO UPDATE"
                " SET dispatch_epoch = EXCLUDED.dispatch_epoch, dispatch_count = EXCLUDED.dispatch_count,"
                " update_time = now()"
            ).bindparams(
                tenant_id=tenant_id,
                model_id=model_id,
                deployment_id=deployment_id,
                policy_hash=policy_hash,
                epoch=epoch,
                count=count,
            )
        )
        await session.commit()


async def _pg_epoch(epoch_sec: int) -> int:
    async with get_session_factory()() as session:
        value = await session.scalar(
            sa.text(
                "SELECT floor(extract(epoch FROM clock_timestamp()) / :epoch_sec)::bigint"
            ).bindparams(epoch_sec=epoch_sec)
        )
    assert value is not None  # pragma: no cover - PG 必然返回值
    return int(value)


async def _fill_slot(
    tenant: Tenant, model_id: uuid.UUID, member: Member, *, reserved_tokens: int = 100
) -> None:
    """用真实容量服务占一个槽位（不经调度器），制造"在飞/满载"的真实事实。"""
    service = ModelCapacityService(get_session_factory)
    await service.reserve(
        tenant_id=tenant.tenant_id,
        lease_owner="rt-fill",
        request=ModelCapacityReservationRequest(
            call_attempt_id=uuid.uuid4(),
            model_id=model_id,
            deployment_id=member.deployment_id,
            credential_id=member.credential_id,
            credential_revision=1,
            key_resource_id=member.key_resource_id,
            quota_group_id=member.quota_group_id,
            owner=ProbeCallOwner(probe_id=uuid.uuid4()),
            workload="PROBE",
            deadline_at=datetime.now(UTC) + timedelta(minutes=5),
            reserved_tokens=reserved_tokens,
        ),
    )


class _RecordingStore(ModelDispatchStore):
    """真实存储行为不变（逐调用直通 super），只记录调用顺序。"""

    def __init__(self, session: AsyncSession, order: list[str]) -> None:
        super().__init__(session)
        self._order = order

    async def lock_attempt_selection(
        self, *, tenant_id: str, call_attempt_id: uuid.UUID
    ) -> None:
        self._order.append("lock_attempt_selection")
        await super().lock_attempt_selection(
            tenant_id=tenant_id, call_attempt_id=call_attempt_id
        )

    async def read_attempt(self, *, tenant_id: str, call_attempt_id: uuid.UUID) -> Any:
        self._order.append("read_attempt")
        return await super().read_attempt(
            tenant_id=tenant_id, call_attempt_id=call_attempt_id
        )

    async def seed_dispatch_states(self, **kwargs: Any) -> None:
        self._order.append("seed_dispatch_states")
        await super().seed_dispatch_states(**kwargs)

    async def pick_and_bump_dispatch(self, **kwargs: Any) -> Any:
        self._order.append("pick_and_bump_dispatch")
        return await super().pick_and_bump_dispatch(**kwargs)

    async def ensure_capacity_row(self, **kwargs: Any) -> None:
        self._order.append(f"ensure:{kwargs['resource_type']}")
        await super().ensure_capacity_row(**kwargs)

    async def try_admit_capacity(self, **kwargs: Any) -> Any:
        self._order.append(f"admit:{kwargs['resource_type']}")
        return await super().try_admit_capacity(**kwargs)

    async def insert_lease(self, **kwargs: Any) -> Any:
        self._order.append("insert_lease")
        return await super().insert_lease(**kwargs)


# --------------------------------------------------------------------------------------
# B-223：等权均衡与稳定轮转
# --------------------------------------------------------------------------------------


async def test_b223_equal_weight_idle_pool_spreads_picks_within_one(tenant: Tenant) -> None:
    """[B-223] 等权空闲池顺序 10 次分配：成员累计预占次数最大差 ≤1。"""
    model_id = await _seed_model(tenant.tenant_id)
    members = [await _seed_member(tenant.tenant_id, model_id) for _ in range(3)]
    candidates = [member.candidate() for member in members]
    policy = _policy()
    allocator = _allocator()

    picked: list[uuid.UUID] = []
    allocations = []
    for _ in range(10):
        allocation = await _allocate(
            allocator, tenant, model_id, candidates=candidates, policy=policy
        )
        assert allocation.reused is False and allocation.state == "ACTIVE"
        assert allocation.reservation_id is not None
        assert allocation.member.deployment_id in {member.deployment_id for member in members}
        picked.append(allocation.member.deployment_id)
        allocations.append(allocation)

    counts = await _dispatch_counts(tenant.tenant_id)
    assert set(counts) == {member.deployment_id for member in members}
    assert sorted(counts.values()) == [3, 3, 4]
    assert max(counts.values()) - min(counts.values()) <= 1
    # 返回值与真实计数一致：本轮选中的成员，其次数就是全池累计次数
    for deployment_id, count in counts.items():
        assert sorted(picked).count(deployment_id) == count

    rows = await _dispatch_rows(tenant.tenant_id)
    assert {row["policy_hash"] for row in rows} == {policy.policy_hash}
    assert all(row["last_picked_at"] is not None for row in rows)
    assert len(await _lease_rows(tenant.tenant_id)) == 10
    for member in members:
        row = await _capacity_row(tenant.tenant_id, RESOURCE_KEY, member.key_resource_id)
        assert row["inflight"] == counts[member.deployment_id]


async def test_b223_equal_weight_pool_concurrent_picks_stay_balanced(tenant: Tenant) -> None:
    """[B-223] 等权空闲池真实并发 12 次分配：无失败、计数差 ≤1、容量不超发。"""
    model_id = await _seed_model(tenant.tenant_id)
    members = [await _seed_member(tenant.tenant_id, model_id, key_max_inflight=5) for _ in range(3)]
    candidates = [member.candidate() for member in members]
    policy = _policy()
    allocator = _allocator()

    allocations = await asyncio.gather(
        *(
            _allocate(
                allocator, tenant, model_id, candidates=candidates, policy=policy,
                lease_owner=f"rt-{index}",
            )
            for index in range(12)
        )
    )
    assert all(allocation.reused is False for allocation in allocations)
    counts = await _dispatch_counts(tenant.tenant_id)
    assert sorted(counts.values()) == [4, 4, 4]
    assert max(counts.values()) - min(counts.values()) <= 1
    for member in members:
        row = await _capacity_row(tenant.tenant_id, RESOURCE_KEY, member.key_resource_id)
        assert row["inflight"] == 4
    assert len(await _lease_rows(tenant.tenant_id)) == 12


async def test_b223_weighted_picks_follow_count_over_weight(tenant: Tenant) -> None:
    """[B-223] 权重 3:1 的成员池，8 次分配按 dispatch_count/weight 收敛到 6:2。"""
    model_id = await _seed_model(tenant.tenant_id)
    heavy = await _seed_member(tenant.tenant_id, model_id, weight=3, key_max_inflight=10)
    light = await _seed_member(tenant.tenant_id, model_id, weight=1, key_max_inflight=10)
    candidates = [heavy.candidate(), light.candidate()]
    policy = _policy()
    allocator = _allocator()

    for _ in range(8):
        await _allocate(allocator, tenant, model_id, candidates=candidates, policy=policy)

    counts = await _dispatch_counts(tenant.tenant_id)
    assert counts[heavy.deployment_id] == 6
    assert counts[light.deployment_id] == 2


async def test_b223_tie_breaks_by_normalized_inflight_then_rotation(tenant: Tenant) -> None:
    """[B-223] 计数平局按归一化在飞量让路，再按真实选中时间稳定轮转。"""
    model_id = await _seed_model(tenant.tenant_id)
    busy = await _seed_member(tenant.tenant_id, model_id, key_max_inflight=5)
    idle = await _seed_member(tenant.tenant_id, model_id, key_max_inflight=5)
    candidates = [busy.candidate(), idle.candidate()]
    policy = _policy()
    allocator = _allocator()
    await _fill_slot(tenant, model_id, busy)

    first = await _allocate(allocator, tenant, model_id, candidates=candidates, policy=policy)
    assert first.member.deployment_id == idle.deployment_id  # 平局时在飞量小的成员优先
    second = await _allocate(allocator, tenant, model_id, candidates=candidates, policy=policy)
    assert second.member.deployment_id == busy.deployment_id  # 再平局时轮到另一成员

    counts = await _dispatch_counts(tenant.tenant_id)
    assert counts == {busy.deployment_id: 1, idle.deployment_id: 1}


# --------------------------------------------------------------------------------------
# B-223：共享 epoch 归一与策略版本
# --------------------------------------------------------------------------------------


async def test_b223_shared_epoch_normalizes_stale_counts_without_blanket_reset(
    tenant: Tenant,
) -> None:
    """[B-223] 上轮 epoch 的计数按共享 epoch 归一为 0，只就地归一被选中的行。"""
    model_id = await _seed_model(tenant.tenant_id)
    stale = await _seed_member(tenant.tenant_id, model_id)
    current = await _seed_member(tenant.tenant_id, model_id)
    candidates = [stale.candidate(), current.candidate()]
    policy = _policy(dispatch_epoch_sec=3600)
    allocator = _allocator()

    epoch_before = await _pg_epoch(policy.dispatch_epoch_sec)
    await _set_dispatch_row(
        tenant.tenant_id, model_id, current.deployment_id, policy.policy_hash,
        epoch=epoch_before, count=3,
    )
    await _set_dispatch_row(
        tenant.tenant_id, model_id, stale.deployment_id, policy.policy_hash,
        epoch=epoch_before - 5, count=9,
    )

    allocation = await _allocate(allocator, tenant, model_id, candidates=candidates, policy=policy)
    epoch_after = await _pg_epoch(policy.dispatch_epoch_sec)

    assert allocation.member.deployment_id == stale.deployment_id  # 归一后 0 < 3
    assert allocation.dispatch_count == 1
    assert allocation.dispatch_epoch is not None
    assert epoch_before <= allocation.dispatch_epoch <= epoch_after
    rows = {row["deployment_id"]: row for row in await _dispatch_rows(tenant.tenant_id)}
    assert rows[stale.deployment_id]["dispatch_count"] == 1
    assert rows[stale.deployment_id]["dispatch_epoch"] == allocation.dispatch_epoch
    # 未被选中的行不被全量清零：仍是上一 epoch 的 3
    assert rows[current.deployment_id]["dispatch_count"] == 3
    assert rows[current.deployment_id]["dispatch_epoch"] == epoch_before

    # 归一后计数小的成员继续被选中，直到两者平局；共享 epoch 跨调用一致
    for expected_count in (2, 3):
        repeat = await _allocate(allocator, tenant, model_id, candidates=candidates, policy=policy)
        assert repeat.member.deployment_id == stale.deployment_id
        assert repeat.dispatch_count == expected_count
        assert repeat.dispatch_epoch == allocation.dispatch_epoch

    # 平局按归一化在飞量让路：stale 已有 3 个在飞，current 0 个 → 选中 current
    tie = await _allocate(allocator, tenant, model_id, candidates=candidates, policy=policy)
    assert tie.member.deployment_id == current.deployment_id
    assert tie.dispatch_epoch == allocation.dispatch_epoch
    assert tie.dispatch_count == 4  # 同一 epoch 内继续累加，不逐请求清零
    rows = {row["deployment_id"]: row for row in await _dispatch_rows(tenant.tenant_id)}
    assert rows[current.deployment_id]["dispatch_count"] == 4
    assert rows[current.deployment_id]["dispatch_epoch"] == tie.dispatch_epoch


async def test_b223_policy_hash_separates_versions_and_must_self_verify(tenant: Tenant) -> None:
    """[B-223] 不同策略版本各自一列分布；伪造 policy_hash 立即显式失败且不写计数。"""
    model_id = await _seed_model(tenant.tenant_id)
    member = await _seed_member(tenant.tenant_id, model_id)
    candidates = [member.candidate()]
    first_policy = _policy(dispatch_epoch_sec=3600, interactive_weight=8, background_weight=1)
    second_policy = _policy(dispatch_epoch_sec=60, interactive_weight=2, background_weight=1)
    allocator = _allocator()

    assert first_policy.policy_hash != second_policy.policy_hash
    assert first_policy.policy_hash == dispatch_policy_hash(
        dispatch_epoch_sec=3600, interactive_weight=8, background_weight=1, aging_interval_sec=30
    )
    await _allocate(allocator, tenant, model_id, candidates=candidates, policy=first_policy)
    await _allocate(allocator, tenant, model_id, candidates=candidates, policy=second_policy)
    rows = await _dispatch_rows(tenant.tenant_id)
    assert {row["policy_hash"] for row in rows} == {
        first_policy.policy_hash, second_policy.policy_hash
    }
    assert {row["dispatch_count"] for row in rows} == {1}

    forged = ModelDispatchPolicy(
        dispatch_epoch_sec=3600, interactive_weight=8, background_weight=1,
        aging_interval_sec=30, policy_hash="f" * 64,
    )
    error = await _allocate_error(
        allocator, tenant, model_id, candidates=candidates, policy=forged
    )
    assert error.code == ErrorCode.COMMON_VALIDATION_ERROR
    assert len(await _dispatch_rows(tenant.tenant_id)) == 2
    assert len(await _lease_rows(tenant.tenant_id)) == 2


# --------------------------------------------------------------------------------------
# B-223：低额度不超发
# --------------------------------------------------------------------------------------


async def test_b223_low_key_quota_member_is_skipped_and_never_oversubscribed(tenant: Tenant) -> None:
    """[B-223] Key 额度低的成员满员即跳过：不在满员成员上写计数，也不超发。"""
    model_id = await _seed_model(tenant.tenant_id)
    small = await _seed_member(tenant.tenant_id, model_id, key_max_inflight=1)
    large = await _seed_member(tenant.tenant_id, model_id, key_max_inflight=5)
    candidates = [small.candidate(), large.candidate()]
    policy = _policy()
    allocator = _allocator()
    await _fill_slot(tenant, model_id, small)

    for _ in range(3):
        allocation = await _allocate(allocator, tenant, model_id, candidates=candidates, policy=policy)
        assert allocation.member.deployment_id == large.deployment_id

    counts = await _dispatch_counts(tenant.tenant_id)
    assert counts.get(small.deployment_id, 0) == 0
    assert counts[large.deployment_id] == 3
    assert (await _capacity_row(tenant.tenant_id, RESOURCE_KEY, small.key_resource_id))["inflight"] == 1


async def test_b223_shared_quota_group_capacity_blocks_oversubscribe(tenant: Tenant) -> None:
    """[B-223] 两成员共享额度组：组在飞上限是共享事实，第 3 次明确不可用且不写计数。"""
    model_id = await _seed_model(tenant.tenant_id)
    group_id = await _seed_quota_group(tenant.tenant_id, max_inflight=2)
    left = await _seed_member(
        tenant.tenant_id, model_id, key_max_inflight=5, quota_group_id=group_id
    )
    right = await _seed_member(
        tenant.tenant_id, model_id, key_max_inflight=5, quota_group_id=group_id
    )
    candidates = [left.candidate(), right.candidate()]
    policy = _policy()
    allocator = _allocator()

    granted = [
        await _allocate(allocator, tenant, model_id, candidates=candidates, policy=policy)
        for _ in range(2)
    ]
    assert {allocation.member.deployment_id for allocation in granted} == {
        left.deployment_id, right.deployment_id
    }
    denied = await _allocate_error(allocator, tenant, model_id, candidates=candidates, policy=policy)
    assert denied.code == ErrorCode.MODEL_CAPACITY_UNAVAILABLE
    assert denied.data == {"reason": DEFER_REASON_CAPACITY}

    group_row = await _capacity_row(tenant.tenant_id, RESOURCE_QUOTA_GROUP, group_id)
    assert group_row["inflight"] == 2
    counts = await _dispatch_counts(tenant.tenant_id)
    assert sorted(counts.values()) == [1, 1]
    assert len(await _lease_rows(tenant.tenant_id)) == 2


async def test_b223_pool_lock_gate_bounds_db_waiters(tenant: Tenant) -> None:
    """闸门把同时在 DB 上等池锁的会话数压到 limit：其余调用在 asyncio 排队，不占连接。"""
    gate = PoolLockGate(limit=2)
    model_id = await _seed_model(tenant.tenant_id)
    member = await _seed_member(tenant.tenant_id, model_id, key_max_inflight=50)
    candidates = [member.candidate()]
    policy = _policy()
    allocator = _allocator(gate=gate)
    assert allocator.gate is gate
    assert allocator._capacity.gate is gate  # noqa: SLF001 - 断言选择与预占共享同一闸门

    results = await asyncio.gather(
        *(
            _allocate(
                allocator, tenant, model_id, candidates=candidates, policy=policy,
                lease_owner=f"rt-gate-{index}",
            )
            for index in range(6)
        ),
        return_exceptions=True,
    )

    assert all(isinstance(item, ModelAllocation) for item in results)
    assert gate.max_concurrency == 2


async def test_b223_concurrent_shared_group_never_oversubscribes(tenant: Tenant) -> None:
    """[B-223] 并发 6 次分配共享额度组（上限 2）：恰好 2 次成功，组在飞恒不超发。"""
    model_id = await _seed_model(tenant.tenant_id)
    group_id = await _seed_quota_group(tenant.tenant_id, max_inflight=2)
    members = [
        await _seed_member(tenant.tenant_id, model_id, key_max_inflight=5, quota_group_id=group_id)
        for _ in range(2)
    ]
    candidates = [member.candidate() for member in members]
    policy = _policy()
    allocator = _allocator()

    results = await asyncio.gather(
        *(
            _allocate(
                allocator, tenant, model_id, candidates=candidates, policy=policy,
                lease_owner=f"rt-{index}",
            )
            for index in range(6)
        ),
        return_exceptions=True,
    )
    granted = [item for item in results if isinstance(item, ModelAllocation)]
    denied = [item for item in results if isinstance(item, AppError)]
    assert len(granted) == 2
    assert len(denied) == 4
    assert {error.code for error in denied} == {ErrorCode.MODEL_CAPACITY_UNAVAILABLE}
    assert (await _capacity_row(tenant.tenant_id, RESOURCE_QUOTA_GROUP, group_id))["inflight"] == 2
    for member in members:
        # 行只在该成员的预占真正提交后才存在：没有行就是 0 在飞（未提交的预占随事务回滚）
        row = await _capacity_row(tenant.tenant_id, RESOURCE_KEY, member.key_resource_id)
        assert row.get("inflight", 0) <= 5
    active = [row for row in await _lease_rows(tenant.tenant_id) if row["state"] == "ACTIVE"]
    assert len(active) == 2
    counts = await _dispatch_counts(tenant.tenant_id)
    assert sum(counts.values()) >= 2  # 已发出的尝试都被计入（重试计一次尝试）


# --------------------------------------------------------------------------------------
# B-223：工作负载公平与老化
# --------------------------------------------------------------------------------------

async def test_b223_background_defers_under_interactive_load_then_advances(tenant: Tenant) -> None:
    """[B-223] 后台按冻结权重让路给交互，等待超过老化间隔后必须能进展。"""
    model_id = await _seed_model(tenant.tenant_id)
    members = [
        await _seed_member(tenant.tenant_id, model_id, key_max_inflight=5) for _ in range(4)
    ]
    candidates = [member.candidate() for member in members]
    policy = _policy(interactive_weight=8, background_weight=1, aging_interval_sec=30)
    allocator = _allocator()

    interactive = await _allocate(allocator, tenant, model_id, candidates=candidates, policy=policy)
    assert interactive.member.deployment_id in {member.deployment_id for member in members}
    counts_before = await _dispatch_counts(tenant.tenant_id)

    deferred = await _allocate_error(
        allocator, tenant, model_id, candidates=candidates, policy=policy,
        workload="BACKGROUND", wait_since=datetime.now(UTC),
    )
    assert deferred.code == ErrorCode.MODEL_CAPACITY_UNAVAILABLE
    assert deferred.data == {"reason": DEFER_REASON_WORKLOAD}
    assert await _dispatch_counts(tenant.tenant_id) == counts_before
    assert len(await _lease_rows(tenant.tenant_id)) == 1

    aged = await _allocate(
        allocator, tenant, model_id, candidates=candidates, policy=policy,
        workload="BACKGROUND", wait_since=datetime.now(UTC) - timedelta(seconds=31),
    )
    assert aged.member.deployment_id in {member.deployment_id for member in members}
    workloads = [row["workload"] for row in await _lease_rows(tenant.tenant_id)]
    assert workloads.count("BACKGROUND") == 1 and workloads.count("INTERACTIVE") == 1


async def test_b223_frozen_weights_and_interactive_never_gated_by_background(tenant: Tenant) -> None:
    """[B-223] 冻结权重相等时后台按同权进展；后台在飞不阻止新的交互调用。"""
    model_id = await _seed_model(tenant.tenant_id)
    members = [
        await _seed_member(tenant.tenant_id, model_id, key_max_inflight=5) for _ in range(4)
    ]
    candidates = [member.candidate() for member in members]
    equal_policy = _policy(interactive_weight=8, background_weight=8, aging_interval_sec=300)
    allocator = _allocator()

    await _allocate(allocator, tenant, model_id, candidates=candidates, policy=equal_policy)
    background = await _allocate(
        allocator, tenant, model_id, candidates=candidates, policy=equal_policy,
        workload="BACKGROUND", wait_since=datetime.now(UTC),
    )
    assert background.state == "ACTIVE"
    for _ in range(2):
        allocation = await _allocate(
            allocator, tenant, model_id, candidates=candidates, policy=equal_policy
        )
        assert allocation.state == "ACTIVE"
    workloads = [row["workload"] for row in await _lease_rows(tenant.tenant_id)]
    assert sorted(workloads) == ["BACKGROUND", "INTERACTIVE", "INTERACTIVE", "INTERACTIVE"]


# --------------------------------------------------------------------------------------
# B-223：尝试计数、复用与失败分支
# --------------------------------------------------------------------------------------


async def test_b223_retry_counts_one_attempt_and_replay_reuses_lease(tenant: Tenant) -> None:
    """[B-223] 重试计一次尝试；同一 attempt 重放复用既有租约且不重复计数。"""
    model_id = await _seed_model(tenant.tenant_id)
    member = await _seed_member(tenant.tenant_id, model_id)
    candidates = [member.candidate()]
    policy = _policy()
    allocator = _allocator()

    first_attempt = uuid.uuid4()
    first = await _allocate(
        allocator, tenant, model_id, candidates=candidates, policy=policy, attempt_id=first_attempt
    )
    assert first.dispatch_count == 1
    retry = await _allocate(
        allocator, tenant, model_id, candidates=candidates, policy=policy, attempt_id=uuid.uuid4()
    )
    assert retry.dispatch_count == 2

    replay = await _allocate(
        allocator, tenant, model_id, candidates=candidates, policy=policy, attempt_id=first_attempt
    )
    assert replay.reused is True
    assert replay.reservation_id == first.reservation_id
    assert replay.lease_until == first.lease_until
    assert replay.dispatch_epoch is None and replay.dispatch_count is None
    assert (await _dispatch_counts(tenant.tenant_id))[member.deployment_id] == 2
    assert len(await _lease_rows(tenant.tenant_id)) == 2

    stranger = await _allocate_error(
        allocator, tenant, model_id, candidates=[], policy=policy, attempt_id=first_attempt
    )
    assert stranger.code == ErrorCode.MODEL_CALL_LEASE_LOST
    assert (await _dispatch_counts(tenant.tenant_id))[member.deployment_id] == 2


async def test_b223_expired_deadline_and_missing_members_never_count(tenant: Tenant) -> None:
    """[B-223] 期限已过与"没有可执行成员"都显式失败且不写任何调度计数。"""
    model_id = await _seed_model(tenant.tenant_id)
    member = await _seed_member(tenant.tenant_id, model_id)
    candidates = [member.candidate()]
    policy = _policy()
    allocator = _allocator()

    expired = await _allocate_error(
        allocator, tenant, model_id, candidates=candidates, policy=policy, deadline_in_sec=-5
    )
    assert expired.code == ErrorCode.MODEL_WAIT_TIMEOUT
    empty = await _allocate_error(
        allocator, tenant, model_id, candidates=[], policy=policy
    )
    assert empty.code == ErrorCode.CREDENTIAL_MISSING
    assert await _dispatch_rows(tenant.tenant_id) == []
    assert await _lease_rows(tenant.tenant_id) == []
    assert await _capacity_row(tenant.tenant_id, RESOURCE_KEY, member.key_resource_id) == {}


async def test_b223_disabled_or_rebound_members_are_not_picked(tenant: Tenant) -> None:
    """[B-223] 停用成员与冻结引用不符的候选都不可执行：要么跳过，要么明确失败。"""
    model_id = await _seed_model(tenant.tenant_id)
    disabled = await _seed_member(tenant.tenant_id, model_id, member_enabled=False)
    healthy = await _seed_member(tenant.tenant_id, model_id)
    candidates = [disabled.candidate(), healthy.candidate()]
    policy = _policy()
    allocator = _allocator()

    for _ in range(2):
        allocation = await _allocate(allocator, tenant, model_id, candidates=candidates, policy=policy)
        assert allocation.member.deployment_id == healthy.deployment_id
    counts = await _dispatch_counts(tenant.tenant_id)
    assert counts == {healthy.deployment_id: 2}

    disabled_credential = await _seed_member(tenant.tenant_id, model_id, credential_enabled=False)
    rebound = ModelMemberCandidate(
        deployment_id=healthy.deployment_id,
        credential_id=uuid.uuid4(),
        credential_revision=1,
        key_resource_id=uuid.uuid4(),
        quota_group_id=None,
        weight=1,
    )
    error = await _allocate_error(
        allocator, tenant, model_id, candidates=[disabled_credential.candidate(), rebound], policy=policy
    )
    assert error.code == ErrorCode.CREDENTIAL_MISSING


# --------------------------------------------------------------------------------------
# B-223：池锁、无网络与两段事务事实
# --------------------------------------------------------------------------------------


async def test_b223_selection_is_one_atomic_statement_without_network(
    tenant: Tenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """[B-223] 选择无池级锁：身份锁→读 attempt→一次原子 pick+bump；不联网、不写容量行。"""
    model_id = await _seed_model(tenant.tenant_id)
    group_id = await _seed_quota_group(tenant.tenant_id, max_inflight=5)
    member = await _seed_member(
        tenant.tenant_id, model_id, key_max_inflight=5, quota_group_id=group_id
    )
    candidates = [member.candidate()]
    policy = _policy()
    order: list[str] = []
    violations = _install_outbound_guard(monkeypatch)
    allocator = _allocator(order=order)

    allocation = await _allocate(allocator, tenant, model_id, candidates=candidates, policy=policy)
    deferred = await _allocate_error(
        allocator, tenant, model_id, candidates=candidates, policy=policy,
        workload="BACKGROUND", wait_since=datetime.now(UTC) - timedelta(seconds=1),
    )

    assert allocation.state == "ACTIVE"
    assert deferred.code == ErrorCode.MODEL_CAPACITY_UNAVAILABLE
    assert order[0] == "lock_attempt_selection"
    assert order[1] == "read_attempt"
    assert order[2] == "seed_dispatch_states"  # 首次选择先建 0 计数行
    assert order[3] == "pick_and_bump_dispatch"
    # 排序、取行、epoch 归一与计数是一条语句；被延迟的后台在门禁处退出，不计数
    assert order.count("pick_and_bump_dispatch") == 1
    assert order.count("seed_dispatch_states") == 1
    assert order.index("pick_and_bump_dispatch") < order.index("insert_lease")
    assert order.count("insert_lease") == 1
    # 选择与预占各取一次 attempt 身份锁（被延迟的调用只到门禁）；不再有跨语句 attempt 行锁
    assert order.count("lock_attempt_selection") == 3
    assert "lock_attempt" not in order
    # 预占：先写 lease，再各一条条件 UPDATE（容量更新之后只剩提交）
    assert order.index("insert_lease") < order.index("admit:QUOTA_GROUP")
    assert order.index("admit:QUOTA_GROUP") < order.index("admit:KEY")
    assert violations == []


def _install_outbound_guard(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """记录非数据库端口的真实出站连接；任何 HTTP 客户端调用都视为锁内联网。"""
    violations: list[str] = []
    database_port = urlsplit(SharedSettings().require_database_url().replace("+asyncpg", "")).port
    original_connect = socket.socket.connect

    def guarded_connect(self: socket.socket, address: Any) -> Any:
        if (
            isinstance(address, tuple)
            and len(address) > 1
            and int(address[1]) != database_port
        ):
            violations.append(f"{address[0]}:{address[1]}")
        return original_connect(self, address)

    async def forbidden_send(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("模型池调度路径不得发起 HTTP 调用")

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden_send)
    monkeypatch.setattr(httpx.AsyncClient, "request", forbidden_send)
    return violations
