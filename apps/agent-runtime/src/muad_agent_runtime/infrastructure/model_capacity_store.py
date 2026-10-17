"""PG 侧模型容量行与调用租约行的读写（设计 §3.2/§3.3 固定锁序）。

固定锁序由服务层调用顺序保证：池调度 advisory 事务锁 → quota_group 容量行 → key 容量行 →
attempt 租约行。本模块只做带租户谓词的读写与加锁，不做超限判断，也不提交事务。

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


def pool_lock_key(tenant_id: str, model_id: uuid.UUID) -> int:
    """池调度 advisory 事务锁的 64 位键（同 tenant/model 跨进程一致）。"""
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

    async def lock_pool(self, *, tenant_id: str, model_id: uuid.UUID) -> None:
        """池调度 advisory 事务锁：必须先于任何容量/租约行锁。"""
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
