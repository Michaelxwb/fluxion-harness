"""PG 侧模型容量行与调用租约行的读写（设计 §3.2/§3.3 固定锁序）。

固定锁序由服务层调用顺序保证：quota_group 容量行 → key 容量行 → attempt 租约行。
本模块只做带租户谓词的读写与加锁，不做超限判断，也不提交事务。

**没有池级 advisory 锁**：成员选择由 `ModelDispatchStore.pick_and_bump_dispatch` 用一条
原子语句完成（排序、取行、epoch 归一、计数），并发由 `FOR UPDATE SKIP LOCKED` 分摊到各
成员行；容量预占按 组行 → Key 行 → attempt 的固定锁序取行锁。旧的全池串行锁曾把 800 个
用户的每次模型调用串成一条队列（实测该锁占 DB 等待时间的 94.7%）。

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
ATTEMPT_SELECTION_LOCK_NAMESPACE = "muad:model-attempt-selection"

_RELEASE_CAPACITY_SQL = sa.text(
    "UPDATE runtime.model_capacity SET"
    " inflight = GREATEST(inflight - :release_inflight, 0),"
    " reserved_requests = CASE WHEN :lease_create_time >= window_start"
    "   THEN GREATEST(reserved_requests - :release_request, 0) ELSE reserved_requests END,"
    " reserved_tokens = CASE WHEN :lease_create_time >= window_start"
    "   THEN GREATEST(reserved_tokens - :released_tokens, 0) ELSE reserved_tokens END,"
    " update_time = :now"
    " WHERE tenant_id = :tenant_id AND resource_type = :resource_type"
    "  AND resource_id = :resource_id"
)
_ADMIT_CAPACITY_SQL = sa.text(
    "UPDATE runtime.model_capacity SET"
    " inflight = inflight + 1,"
    " window_start = CASE WHEN EXTRACT(EPOCH FROM (CAST(:bucket AS timestamptz) - window_start))"
    "   >= :span_seconds THEN CAST(:bucket AS timestamptz) ELSE window_start END,"
    " reserved_requests = CASE WHEN EXTRACT(EPOCH FROM (CAST(:bucket AS timestamptz) - window_start))"
    "   >= :span_seconds THEN 1 ELSE reserved_requests + 1 END,"
    " reserved_tokens = CASE WHEN EXTRACT(EPOCH FROM (CAST(:bucket AS timestamptz) - window_start))"
    "   >= :span_seconds THEN :tokens ELSE reserved_tokens + :tokens END,"
    " update_time = :now"
    " WHERE tenant_id = :tenant_id AND resource_type = :resource_type"
    "  AND resource_id = :resource_id AND inflight + 1 <= :max_inflight"
    "  AND (:rpm IS NULL OR (CASE WHEN EXTRACT(EPOCH FROM (CAST(:bucket AS timestamptz) - window_start))"
    "    >= :span_seconds THEN 1 ELSE reserved_requests + 1 END) <= :rpm)"
    "  AND (:tpm IS NULL OR (CASE WHEN EXTRACT(EPOCH FROM (CAST(:bucket AS timestamptz) - window_start))"
    "    >= :span_seconds THEN :tokens ELSE reserved_tokens + :tokens END) <= :tpm)"
    " RETURNING inflight, window_start, reserved_requests, reserved_tokens"
)

_CREDENTIAL_LIMITS_SQL = sa.text(
    "SELECT max_inflight, enabled FROM control.model_credential"
    " WHERE tenant_id = :tenant_id AND id = :credential_id AND is_deleted = false"
)
_QUOTA_GROUP_LIMITS_SQL = sa.text(
    "SELECT max_inflight, rpm, tpm FROM control.model_quota_group"
    " WHERE tenant_id = :tenant_id AND id = :quota_group_id AND is_deleted = false"
)
_LIMITS_AND_CLOCK_SQL = sa.text(
    "SELECT clock_timestamp() AS now,"
    " c.max_inflight AS max_inflight, c.enabled AS enabled,"
    " g.max_inflight AS group_max_inflight, g.rpm AS group_rpm, g.tpm AS group_tpm"
    " FROM control.model_credential c"
    " LEFT JOIN control.model_quota_group g"
    "   ON g.tenant_id = :tenant_id AND g.id = :quota_group_id AND g.is_deleted = false"
    " WHERE c.tenant_id = :tenant_id AND c.id = :credential_id AND c.is_deleted = false"
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


def attempt_selection_lock_key(tenant_id: str, call_attempt_id: uuid.UUID) -> int:
    """attempt 身份锁的 64 位键（同 tenant/attempt 跨进程一致）。"""
    digest = hashlib.sha256(
        f"{ATTEMPT_SELECTION_LOCK_NAMESPACE}:{tenant_id}:{call_attempt_id}".encode()
    ).digest()
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

    async def read_limits_and_clock(
        self, *, tenant_id: str, credential_id: uuid.UUID, quota_group_id: uuid.UUID | None
    ) -> tuple[CredentialLimits | None, datetime | None]:
        """一次读齐额度上限与 PG 时钟：与 `read_limits` + `database_clock` 语义一致。

        凭据缺失返回 `(None, None)`；额度组缺失返回 `(None, now)`（调用方仍按 NOT_FOUND 处理）。
        """
        row = (
            await self._session.execute(
                _LIMITS_AND_CLOCK_SQL,
                {
                    "tenant_id": tenant_id,
                    "credential_id": credential_id,
                    "quota_group_id": quota_group_id,
                },
            )
        ).mappings().one_or_none()
        if row is None:
            return None, None
        now = row["now"]
        quota_group: CapacityLimits | None = None
        if quota_group_id is not None:
            if row["group_max_inflight"] is None:
                return None, now
            quota_group = CapacityLimits(
                max_inflight=int(row["group_max_inflight"]),
                rpm=None if row["group_rpm"] is None else int(row["group_rpm"]),
                tpm=None if row["group_tpm"] is None else int(row["group_tpm"]),
            )
        return (
            CredentialLimits(
                enabled=bool(row["enabled"]),
                key=CapacityLimits(max_inflight=int(row["max_inflight"])),
                quota_group=quota_group,
            ),
            now,
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

    async def ensure_capacity_row(
        self,
        *,
        tenant_id: str,
        resource_type: str,
        resource_id: uuid.UUID,
        window_start: datetime,
    ) -> None:
        """按需建容量行（不锁既有行）：后续判定与计数在单条条件 UPDATE 里完成。"""
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

    async def try_admit_capacity(
        self,
        *,
        tenant_id: str,
        resource_type: str,
        resource_id: uuid.UUID,
        bucket: datetime,
        span_seconds: int,
        tokens: int,
        limits: CapacityLimits,
        now: datetime,
    ) -> CapacitySnapshot | None:
        """单条条件 UPDATE 完成「窗口归一 + 在飞/RPM/TPM 判定 + 计数」。

        行锁只在语句执行期间持有（提交前不再有其它共享行语句），并发调用因此按语句
        串行而不是按「读-判-写」整段事务串行。返回 None 表示任一维度超限（调用方回滚
        整个预占事务：lease 与组行计数一起消失）。
        """
        row = (
            await self._session.execute(
                _ADMIT_CAPACITY_SQL.bindparams(
                    sa.bindparam("rpm", type_=sa.Integer),
                    sa.bindparam("tpm", type_=sa.Integer),
                ),
                {
                    "tenant_id": tenant_id,
                    "resource_type": resource_type,
                    "resource_id": resource_id,
                    "bucket": bucket,
                    "span_seconds": span_seconds,
                    "tokens": tokens,
                    "now": now,
                    "max_inflight": limits.max_inflight,
                    "rpm": limits.rpm,
                    "tpm": limits.tpm,
                },
            )
        ).mappings().one_or_none()
        if row is None:
            return None
        return CapacitySnapshot(
            resource_type=resource_type,
            resource_id=resource_id,
            inflight=int(row["inflight"]),
            window_start=row["window_start"],
            reserved_requests=int(row["reserved_requests"]),
            reserved_tokens=int(row["reserved_tokens"]),
        )

    async def lock_attempt_selection(
        self, *, tenant_id: str, call_attempt_id: uuid.UUID
    ) -> None:
        """同 attempt 的身份锁：不同 attempt 互不相干，只串行化重复重试。

        选择与预占都先取它：保证「同一 attempt 的并发重试只计一次尝试/只建一份租约」，
        且它不构成跨用户的串行点（键里带 attempt id）。
        """
        key = attempt_selection_lock_key(tenant_id, call_attempt_id)
        await self._session.execute(sa.select(sa.func.pg_advisory_xact_lock(key)))

    async def release_capacity(
        self,
        *,
        tenant_id: str,
        resource_type: str,
        resource_id: uuid.UUID,
        release_inflight: bool,
        release_request: bool,
        released_tokens: int,
        lease_create_time: datetime,
        now: datetime,
    ) -> None:
        """单条 UPDATE 释放在飞/窗口计数（跨分钟才计入窗口的租约不动窗口）。

        与预占同一原则：共享容量行的行锁只覆盖这一条语句到提交，不在锁内读-判-写。
        """
        await self._session.execute(
            _RELEASE_CAPACITY_SQL,
            {
                "tenant_id": tenant_id,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "release_inflight": 1 if release_inflight else 0,
                "release_request": 1 if release_request else 0,
                "released_tokens": max(released_tokens, 0),
                "lease_create_time": lease_create_time,
                "now": now,
            },
        )

    async def read_lease(
        self, *, tenant_id: str, call_attempt_id: uuid.UUID
    ) -> LeaseSnapshot | None:
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

    async def read_attempt(
        self, *, tenant_id: str, call_attempt_id: uuid.UUID
    ) -> LeaseSnapshot | None:
        """无锁读一次 attempt 租约（选择阶段判重放；同 attempt 的并发由调用方加身份锁）。"""
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
