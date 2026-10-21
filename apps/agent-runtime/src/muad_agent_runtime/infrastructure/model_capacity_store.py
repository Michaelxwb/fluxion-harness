"""PG 侧模型容量行与调用租约行的读写（设计 §3.2/§3.3 固定锁序）。

固定锁序由服务层调用顺序保证：quota_group 容量行 → key 容量行 → attempt 租约行。
本模块只做带租户谓词的读写与加锁，不做超限判断，也不提交事务。

**池级 advisory 锁只保护成员选择**（`lock_pool`）：等权/加权轮转的串行判定是行为契约
（B-223），锁本身由 `PoolLockGate` 限制同时在 DB 上等待的会话数，避免等待者占满连接池
把准入饿死。容量预占**不取**池锁：组行 → Key 行 → attempt 的固定锁序已足以保证多行判定
的原子性与无死锁。

为什么读 `control.model_credential`/`control.model_quota_group`：额度上限（KEY 的
max_inflight、额度组的 max_inflight/rpm/tpm）由 Console 拥有，Runtime 只读同一 PG 的当前
配置数字，不落副本、不双写（先例：`admin_run_repository` JOIN `control.agent_definition`）。
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from muad_contracts.model_pool import derive_model_key_resource_id
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models.runtime import ModelCallLease, ModelCapacity

RESOURCE_KEY = "KEY"
RESOURCE_QUOTA_GROUP = "QUOTA_GROUP"
POOL_LOCK_NAMESPACE = "muad:model-capacity-pool"

_CREDENTIAL_LIMITS_SQL = sa.text(
    "SELECT max_inflight, enabled FROM control.model_credential"
    " WHERE tenant_id = :tenant_id AND id = :credential_id AND is_deleted = false"
)
_QUOTA_GROUP_LIMITS_SQL = sa.text(
    "SELECT max_inflight, rpm, tpm FROM control.model_quota_group"
    " WHERE tenant_id = :tenant_id AND id = :quota_group_id AND is_deleted = false"
)
_PROBE_TARGET_SQL = sa.text(
    "SELECT d.id AS deployment_id, c.revision AS credential_revision, c.provider_key,"
    " c.api_key_digest, c.quota_group_id"
    " FROM control.model_deployment d"
    " JOIN control.model_credential c ON c.tenant_id = d.tenant_id AND c.id = d.credential_id"
    " WHERE d.tenant_id = :tenant_id AND d.model_id = :model_id"
    " AND d.credential_id = :credential_id AND d.is_deleted = false AND c.is_deleted = false"
)


@dataclass(frozen=True, slots=True)
class CapacityLimits:
    """一个容量资源维度的上限；`None` = 该维度不设应用内上限。"""

    max_inflight: int
    rpm: int | None = None
    tpm: int | None = None


@dataclass(frozen=True, slots=True)
class CredentialLimits:
    """凭据当前配置：KEY 行上限 + 可选共享额度组上限。"""

    enabled: bool
    key: CapacityLimits
    quota_group: CapacityLimits | None


@dataclass(frozen=True, slots=True)
class CapacitySnapshot:
    """容量行当前值（在行锁内读取）。"""

    resource_type: str
    resource_id: uuid.UUID
    inflight: int
    window_start: datetime
    reserved_requests: int
    reserved_tokens: int


@dataclass(frozen=True, slots=True)
class LeaseSnapshot:
    """调用租约行当前值（含调用方身份与 Owner 引用）。"""

    reservation_id: uuid.UUID
    call_attempt_id: uuid.UUID
    run_id: uuid.UUID | None
    memory_job_id: uuid.UUID | None
    probe_id: uuid.UUID | None
    deployment_id: uuid.UUID
    credential_id: uuid.UUID
    key_resource_id: uuid.UUID
    quota_group_id: uuid.UUID | None
    credential_revision: int
    workload: str
    lease_owner: str
    epoch: int
    lease_until: datetime
    reserved_tokens: int
    state: str
    create_time: datetime


@dataclass(frozen=True, slots=True)
class LeaseWrite:
    """新建 ACTIVE 租约要写的调用方身份（容量计数由服务层在同一事务内更新）。"""

    call_attempt_id: uuid.UUID
    deployment_id: uuid.UUID
    credential_id: uuid.UUID
    key_resource_id: uuid.UUID
    quota_group_id: uuid.UUID | None
    credential_revision: int
    workload: str
    lease_owner: str
    epoch: int
    lease_until: datetime
    reserved_tokens: int
    run_id: uuid.UUID | None = None
    memory_job_id: uuid.UUID | None = None
    probe_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class ProbeTarget:
    """探针预占的冻结身份：成员 + 凭据当前版本 + 稳定计量身份 + 共享额度组（无密钥）。"""

    deployment_id: uuid.UUID
    credential_id: uuid.UUID
    credential_revision: int
    key_resource_id: uuid.UUID
    quota_group_id: uuid.UUID | None


def pool_lock_key(tenant_id: str, model_id: uuid.UUID) -> int:
    """成员选择事务的 64 位 advisory 键（同 tenant/model 跨进程一致）。"""
    digest = hashlib.sha256(f"{POOL_LOCK_NAMESPACE}:{tenant_id}:{model_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def _capacity_snapshot(row: ModelCapacity) -> CapacitySnapshot:
    return CapacitySnapshot(
        resource_type=row.resource_type,
        resource_id=row.resource_id,
        inflight=row.inflight,
        window_start=row.window_start,
        reserved_requests=row.reserved_requests,
        reserved_tokens=row.reserved_tokens,
    )


def _lease_snapshot(row: ModelCallLease) -> LeaseSnapshot:
    return LeaseSnapshot(
        reservation_id=row.id,
        call_attempt_id=row.call_attempt_id,
        run_id=row.run_id,
        memory_job_id=row.memory_job_id,
        probe_id=row.probe_id,
        deployment_id=row.deployment_id,
        credential_id=row.credential_id,
        key_resource_id=row.key_resource_id,
        quota_group_id=row.quota_group_id,
        credential_revision=row.credential_revision,
        workload=row.workload,
        lease_owner=row.lease_owner,
        epoch=row.epoch,
        lease_until=row.lease_until,
        reserved_tokens=row.reserved_tokens,
        state=row.state,
        create_time=row.create_time,
    )


class ModelCapacityStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def database_clock(self) -> datetime:
        """PG 时钟（同一事务内取一次，作为窗口、签发与期限判定的唯一时间源）。"""
        value: datetime | None = await self._session.scalar(sa.select(sa.func.clock_timestamp()))
        if value is None:  # pragma: no cover - PG 必然返回值
            raise RuntimeError("database clock unavailable")
        return value

    async def read_limits(
        self, *, tenant_id: str, credential_id: uuid.UUID, quota_group_id: uuid.UUID | None
    ) -> CredentialLimits | None:
        """读当前额度上限（无锁）；凭据或请求的额度组行缺失时返回 `None`。"""
        credential = (
            await self._session.execute(
                _CREDENTIAL_LIMITS_SQL,
                {"tenant_id": tenant_id, "credential_id": credential_id},
            )
        ).mappings().one_or_none()
        if credential is None:
            return None
        quota_group: CapacityLimits | None = None
        if quota_group_id is not None:
            group = (
                await self._session.execute(
                    _QUOTA_GROUP_LIMITS_SQL,
                    {"tenant_id": tenant_id, "quota_group_id": quota_group_id},
                )
            ).mappings().one_or_none()
            if group is None:
                return None
            quota_group = CapacityLimits(
                max_inflight=int(group["max_inflight"]),
                rpm=None if group["rpm"] is None else int(group["rpm"]),
                tpm=None if group["tpm"] is None else int(group["tpm"]),
            )
        return CredentialLimits(
            enabled=bool(credential["enabled"]),
            key=CapacityLimits(max_inflight=int(credential["max_inflight"])),
            quota_group=quota_group,
        )

    async def read_probe_target(
        self, *, tenant_id: str, model_id: uuid.UUID, credential_id: uuid.UUID
    ) -> ProbeTarget | None:
        """解析探针目标（成员 + 凭据当前版本 + 稳定计量身份）；未绑定的对不存在。

        `key_resource_id` 由 provider_key/摘要经**唯一派生函数**得到：探针与主调用/摘要/提炼
        因此在同一物理 Key 行上合并计量，而不是各记一份。
        """
        row = (
            await self._session.execute(
                _PROBE_TARGET_SQL,
                {"tenant_id": tenant_id, "model_id": model_id, "credential_id": credential_id},
            )
        ).mappings().one_or_none()
        if row is None:
            return None
        return ProbeTarget(
            deployment_id=uuid.UUID(str(row["deployment_id"])),
            credential_id=credential_id,
            credential_revision=int(row["credential_revision"]),
            key_resource_id=derive_model_key_resource_id(
                tenant_id, str(row["provider_key"]), str(row["api_key_digest"])
            ),
            quota_group_id=None
            if row["quota_group_id"] is None
            else uuid.UUID(str(row["quota_group_id"])),
        )

    async def lock_pool(self, *, tenant_id: str, model_id: uuid.UUID) -> None:
        """成员选择 advisory 事务锁：把候选判定与 dispatch 计数归一串行化（B-223）。

        调用方必须已经在 `PoolLockGate` 内，且只在选择事务里使用；容量预占按行锁固定锁序，
        不再取本锁——它曾覆盖预占事务，把每次模型调用串成两段全局队列。
        """
        await self._session.execute(
            sa.select(sa.func.pg_advisory_xact_lock(pool_lock_key(tenant_id, model_id)))
        )

    async def lock_capacity(
        self, *, tenant_id: str, resource_type: str, resource_id: uuid.UUID, window_start: datetime
    ) -> CapacitySnapshot:
        """取容量行（缺失即建）并加行锁；同一实际 Key 在所有池中合并为一行。"""
        await self._session.execute(
            pg_insert(ModelCapacity)
            .values(
                tenant_id=tenant_id,
                resource_type=resource_type,
                resource_id=resource_id,
                window_start=window_start,
            )
            .on_conflict_do_nothing(
                index_elements=["tenant_id", "resource_type", "resource_id"]
            )
        )
        row = await self._session.scalar(
            sa.select(ModelCapacity)
            .where(
                ModelCapacity.tenant_id == tenant_id,
                ModelCapacity.resource_type == resource_type,
                ModelCapacity.resource_id == resource_id,
            )
            .with_for_update()
        )
        if row is None:  # pragma: no cover - upsert 后必然存在
            raise RuntimeError("capacity row missing after upsert")
        return _capacity_snapshot(row)

    async def write_capacity(
        self,
        *,
        tenant_id: str,
        snapshot: CapacitySnapshot,
        inflight: int,
        window_start: datetime,
        reserved_requests: int,
        reserved_tokens: int,
        now: datetime,
    ) -> None:
        """把行锁内算好的绝对值写回容量行（调用方已持有该行锁）。"""
        await self._session.execute(
            sa.update(ModelCapacity)
            .where(
                ModelCapacity.tenant_id == tenant_id,
                ModelCapacity.resource_type == snapshot.resource_type,
                ModelCapacity.resource_id == snapshot.resource_id,
            )
            .values(
                inflight=inflight,
                window_start=window_start,
                reserved_requests=reserved_requests,
                reserved_tokens=reserved_tokens,
                update_time=now,
            )
        )

    async def read_lease(self, *, tenant_id: str, call_attempt_id: uuid.UUID) -> LeaseSnapshot | None:
        """无锁读租约（用于取资源身份与栅栏核验，不参与锁序）。"""
        row = await self._session.scalar(
            sa.select(ModelCallLease).where(
                ModelCallLease.tenant_id == tenant_id,
                ModelCallLease.call_attempt_id == call_attempt_id,
            )
        )
        return None if row is None else _lease_snapshot(row)

    async def read_lease_by_reservation(
        self, *, tenant_id: str, reservation_id: uuid.UUID
    ) -> LeaseSnapshot | None:
        """按租约主键（reservation_id）无锁读：受信端口用路径 id 定位并守卫 PROBE 归属。"""
        row = await self._session.scalar(
            sa.select(ModelCallLease).where(
                ModelCallLease.tenant_id == tenant_id,
                ModelCallLease.id == reservation_id,
            )
        )
        return None if row is None else _lease_snapshot(row)

    async def lock_attempt(
        self, *, tenant_id: str, call_attempt_id: uuid.UUID
    ) -> LeaseSnapshot | None:
        """attempt 行锁：固定锁序的最后一步（容量行之后）。"""
        row = await self._session.scalar(
            sa.select(ModelCallLease)
            .where(
                ModelCallLease.tenant_id == tenant_id,
                ModelCallLease.call_attempt_id == call_attempt_id,
            )
            .with_for_update()
        )
        return None if row is None else _lease_snapshot(row)

    async def insert_lease(
        self, *, tenant_id: str, write: LeaseWrite, now: datetime
    ) -> LeaseSnapshot:
        """插入 ACTIVE 租约（`uq_model_call_lease_attempt` 保证 attempt 唯一）。"""
        row = ModelCallLease(
            tenant_id=tenant_id,
            call_attempt_id=write.call_attempt_id,
            run_id=write.run_id,
            memory_job_id=write.memory_job_id,
            probe_id=write.probe_id,
            deployment_id=write.deployment_id,
            credential_id=write.credential_id,
            key_resource_id=write.key_resource_id,
            quota_group_id=write.quota_group_id,
            credential_revision=write.credential_revision,
            workload=write.workload,
            lease_owner=write.lease_owner,
            epoch=write.epoch,
            lease_until=write.lease_until,
            reserved_tokens=write.reserved_tokens,
            state="ACTIVE",
            create_time=now,
            update_time=now,
        )
        self._session.add(row)
        await self._session.flush()
        return _lease_snapshot(row)

    async def write_lease_state(
        self, *, tenant_id: str, call_attempt_id: uuid.UUID, state: str, now: datetime
    ) -> LeaseSnapshot:
        """推进租约状态（ACTIVE→SETTLED/UNKNOWN）；调用方已持有该行锁。"""
        row = await self._session.scalar(
            sa.update(ModelCallLease)
            .where(
                ModelCallLease.tenant_id == tenant_id,
                ModelCallLease.call_attempt_id == call_attempt_id,
            )
            .values(state=state, update_time=now)
            .returning(ModelCallLease)
        )
        if row is None:  # pragma: no cover - 行锁内已确认存在
            raise RuntimeError("lease row missing while writing state")
        return _lease_snapshot(row)

    async def write_lease_until(
        self, *, tenant_id: str, call_attempt_id: uuid.UUID, lease_until: datetime, now: datetime
    ) -> LeaseSnapshot:
        """推后租约期限；调用方已持有该行锁并保证不越过调用的绝对期限。"""
        row = await self._session.scalar(
            sa.update(ModelCallLease)
            .where(
                ModelCallLease.tenant_id == tenant_id,
                ModelCallLease.call_attempt_id == call_attempt_id,
            )
            .values(lease_until=lease_until, update_time=now)
            .returning(ModelCallLease)
        )
        if row is None:  # pragma: no cover - 行锁内已确认存在
            raise RuntimeError("lease row missing while extending lease")
        return _lease_snapshot(row)

    async def lock_expired_active(
        self, *, tenant_id: str, now: datetime, limit: int
    ) -> list[LeaseSnapshot]:
        """锁定一批已过期仍 ACTIVE 的租约（`SKIP LOCKED`：多实例清扫互不阻塞）。

        谓词在锁授予后按 READ COMMITTED 重新求值，因此并发续租成功的租约不会被误扫成
        UNKNOWN；只读不写容量行，占用保留。
        """
        rows = await self._session.scalars(
            sa.select(ModelCallLease)
            .where(
                ModelCallLease.tenant_id == tenant_id,
                ModelCallLease.state == "ACTIVE",
                ModelCallLease.lease_until <= now,
            )
            .order_by(ModelCallLease.lease_until)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return [_lease_snapshot(row) for row in rows]
