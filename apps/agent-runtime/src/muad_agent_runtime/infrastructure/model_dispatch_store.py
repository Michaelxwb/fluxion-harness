"""PG 侧成员候选、调度计数与工作负载在飞的读写（设计 §3.1 ADR-04、§3.3、§3.7）。

- 调度计数 `runtime.model_dispatch_state` 以 `(tenant_id, model_id, deployment_id, policy_hash)`
  为唯一键：不同冻结策略版本（`dispatch_epoch_sec`/权重/老化）的分布分别解释、互不覆盖；
- 计数**只就地按共享 epoch 归一**：`dispatch_epoch` 与当前 epoch 不同的行读作 0，被选中时在
  同一条 UPDATE 里写回当前 epoch 并从 1 起算；不做全量清零，也不承载物理额度；
- 选择是**一条原子语句**（`pick_and_bump_dispatch`）：按 `dispatch_count/weight`、
  在飞归一、轮转次序选出成员并在同一语句里计数，不再有跨多次往返的池级 advisory 锁；
  多成员并发用 `FOR UPDATE SKIP LOCKED` 各取各的行，单成员直接阻塞取唯一行；
- 同 `call_attempt_id` 的并发选择由一把独立的 attempt 身份锁串行（不同 attempt 互不相干），
  保证重试只计一次尝试；
- 本模块只做带租户谓词的读写，不提交事务，也不发起任何网络请求。

为什么读 `control.model_deployment`：成员关系与启用状态由 Console 拥有（§3.3），Runtime 只读
同一 PG 的当前事实（先例：`model_capacity_store` 只读 `control.model_credential` 的额度上限）。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from muad_contracts.model_pool import derive_model_key_resource_id
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from .model_capacity_store import ModelCapacityStore
from .models.runtime import ModelCallLease, ModelCapacity, ModelDispatchState

_SELECT_AND_BUMP_TEMPLATE = (
    "WITH eligible(deployment_id, weight, inflight_ratio) AS ("
    " SELECT * FROM unnest("
    "  CAST(:deployment_ids AS uuid[]), CAST(:weights AS float8[])," 
    "  CAST(:ratios AS float8[]))"
    "), picked AS ("
    " SELECT s.deployment_id FROM runtime.model_dispatch_state s"
    " JOIN eligible e ON e.deployment_id = s.deployment_id"
    " WHERE s.tenant_id = :tenant_id AND s.model_id = :model_id"
    " AND s.policy_hash = :policy_hash"
    " ORDER BY"
    "  (CASE WHEN s.dispatch_epoch = :epoch THEN s.dispatch_count ELSE 0 END)::float8 / e.weight,"
    "  e.inflight_ratio,"
    "  COALESCE(s.last_picked_at, '-infinity'::timestamptz),"
    "  s.deployment_id"
    " {skip} LIMIT 1"
    ")"
    " UPDATE runtime.model_dispatch_state s SET"
    "  dispatch_epoch = :epoch,"
    "  dispatch_count = CASE WHEN s.dispatch_epoch = :epoch THEN s.dispatch_count + 1 ELSE 1 END,"
    "  last_picked_at = :now, update_time = :now"
    " FROM picked p"
    " WHERE s.tenant_id = :tenant_id AND s.model_id = :model_id"
    "  AND s.policy_hash = :policy_hash AND s.deployment_id = p.deployment_id"
    " RETURNING s.deployment_id, s.dispatch_epoch, s.dispatch_count, s.last_picked_at"
)
_SEED_DISPATCH_SQL = sa.text(
    "INSERT INTO runtime.model_dispatch_state"
    " (tenant_id, model_id, deployment_id, policy_hash, dispatch_epoch, dispatch_count,"
    "  last_picked_at, create_time, update_time)"
    " SELECT :tenant_id, :model_id, member.deployment_id, :policy_hash, :epoch, 0, NULL, :now, :now"
    " FROM unnest(CAST(:deployment_ids AS uuid[])) AS member(deployment_id)"
    " ON CONFLICT (tenant_id, model_id, deployment_id, policy_hash) DO NOTHING"
)

_LIVE_MEMBERS_SQL = sa.text(
    "SELECT id, credential_id, enabled FROM control.model_deployment"
    " WHERE tenant_id = :tenant_id AND model_id = :model_id AND is_deleted = false"
)
_MEMBER_REFS_SQL = sa.text(
    "SELECT id, credential_id, weight FROM control.model_deployment"
    " WHERE tenant_id = :tenant_id AND model_id = :model_id AND is_deleted = false"
    " ORDER BY key, id"
)
_FROZEN_CANDIDATE_SQL = sa.text(
    "SELECT d.id AS deployment_id, d.credential_id, d.weight, c.revision, c.provider_key,"
    " c.quota_group_id, c.api_key_digest FROM control.model_deployment d"
    " JOIN control.model_credential c"
    " ON c.tenant_id = d.tenant_id AND c.id = d.credential_id"
    " WHERE d.tenant_id = :tenant_id AND d.model_id = :model_id AND d.is_deleted = false"
    " AND c.is_deleted = false AND d.id = ANY(:deployment_ids)"
)


@dataclass(frozen=True, slots=True)
class LiveMember:
    """`control.model_deployment` 行当前事实：启用与否但**不带权重**（权重随 Run 冻结）。"""

    deployment_id: uuid.UUID
    credential_id: uuid.UUID
    enabled: bool


@dataclass(frozen=True, slots=True)
class MemberRef:
    """成员的**非密钥**引用与权重：新 Run 冻结的候选身份。

    只含这三件非密钥事实；`api_key` / `api_key_digest` / 由其派生的计量身份
    （`key_resource_id`）与 `credential_revision` 都不在这里——调用前经受信接口按本引用
    实时解析（API-09），密钥与指纹因此永不落进快照。
    """

    deployment_id: uuid.UUID
    credential_id: uuid.UUID
    weight: int


@dataclass(frozen=True, slots=True)
class FrozenCandidate:
    """一名**冻结成员**的当前非密钥候选元信息（Key 计量身份 + 版本 + 额度组 + 冻结权重）。

    与 `MemberRef` 的区别：这里带调用前实时读到的 Key 身份事实，因此**只用于本次选择/预占**，
    不进快照；密钥明文与指纹本身都不在其中（指纹只在读取时用于派生计量身份）。
    """

    deployment_id: uuid.UUID
    credential_id: uuid.UUID
    credential_revision: int
    key_resource_id: uuid.UUID
    quota_group_id: uuid.UUID | None
    weight: int


@dataclass(frozen=True, slots=True)
class DispatchState:
    """一名成员在一个冻结策略版本下的调度计数（读取时按共享 epoch 归一）。"""

    deployment_id: uuid.UUID
    dispatch_epoch: int
    dispatch_count: int
    last_picked_at: datetime | None

    def count_in_epoch(self, epoch: int) -> int:
        """共享 epoch 归一：不同 epoch 的行读作 0，不清库、不逐请求重置。"""
        return self.dispatch_count if self.dispatch_epoch == epoch else 0


class ModelDispatchStore(ModelCapacityStore):
    """容量行读写 + 调度计数（同租户/模型的成员与计数事实）。"""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def read_live_members(
        self, *, tenant_id: str, model_id: uuid.UUID
    ) -> dict[uuid.UUID, LiveMember]:
        """该逻辑模型当前未删除的成员关系（含停用：停用是暂时不可执行，不是解绑）。"""
        rows = (
            await self._session.execute(
                _LIVE_MEMBERS_SQL, {"tenant_id": tenant_id, "model_id": model_id}
            )
        ).mappings().all()
        return {
            row["id"]: LiveMember(
                deployment_id=row["id"],
                credential_id=row["credential_id"],
                enabled=bool(row["enabled"]),
            )
            for row in rows
        }

    async def read_member_refs(
        self, *, tenant_id: str, model_id: uuid.UUID
    ) -> tuple[MemberRef, ...]:
        """该模型当前未删除的成员引用（含停用：停用是运行期事实，不是解绑）。

        这是**冻结**口径的读：权重随 Run 冻结，因此这里带权重；成员是否可执行仍由调用方的
        实时扫描（`read_live_members` + 凭据/额度上限）裁决。
        """
        rows = (
            await self._session.execute(
                _MEMBER_REFS_SQL, {"tenant_id": tenant_id, "model_id": model_id}
            )
        ).mappings().all()
        return tuple(
            MemberRef(
                deployment_id=row["id"],
                credential_id=row["credential_id"],
                weight=int(row["weight"]),
            )
            for row in rows
        )

    async def read_member_refs_many(
        self, *, tenant_id: str, model_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, tuple[MemberRef, ...]]:
        """一次查询取多个逻辑模型的成员引用（主模型 + 摘要模型），避免逐模型往返。"""
        if not model_ids:
            return {}
        rows = (
            await self._session.execute(
                sa.text(
                    "SELECT model_id, id, credential_id, weight FROM control.model_deployment"
                    " WHERE tenant_id = :tenant_id AND model_id = ANY(CAST(:model_ids AS uuid[]))"
                    " AND is_deleted = false ORDER BY key, id"
                ).bindparams(
                    sa.bindparam(
                        "model_ids",
                        type_=postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
                    )
                ),
                {"tenant_id": tenant_id, "model_ids": list(model_ids)},
            )
        ).mappings().all()
        grouped: dict[uuid.UUID, list[MemberRef]] = {}
        for row in rows:
            grouped.setdefault(row["model_id"], []).append(
                MemberRef(
                    deployment_id=row["id"],
                    credential_id=row["credential_id"],
                    weight=int(row["weight"]),
                )
            )
        return {model_id: tuple(refs) for model_id, refs in grouped.items()}

    async def read_frozen_candidates(
        self,
        *,
        tenant_id: str,
        model_id: uuid.UUID,
        members: Sequence[MemberRef],
    ) -> tuple[FrozenCandidate, ...]:
        """按**冻结成员引用**读当前非密钥元信息：Key 计量身份、版本、额度组与冻结权重。

        非密钥候选元信息在短事务外取得（design §3.3）：本读不持锁、不发网络请求，密钥明文不在
        其中；`key_resource_id` 由共享派生（`derive_model_key_resource_id`）从当前指纹得到——
        与 Owner 同一口径。预占提交后仍要经 API-09 按 `credential_id`+`expected_revision` 解析
        并校验一致，**轮换竞争由那一步裁决**。成员或凭据已软删即不在候选集内（解绑只影响后续
        选择，不伪造身份）；停用成员保留在候选集里，由实时扫描裁决可执行性。
        """
        if not members:
            return ()
        rows = (
            await self._session.execute(
                _FROZEN_CANDIDATE_SQL,
                {
                    "tenant_id": tenant_id,
                    "model_id": model_id,
                    "deployment_ids": [member.deployment_id for member in members],
                },
            )
        ).mappings().all()
        weights = {member.deployment_id: member.weight for member in members}
        return tuple(
            FrozenCandidate(
                deployment_id=row["deployment_id"],
                credential_id=row["credential_id"],
                credential_revision=int(row["revision"]),
                key_resource_id=derive_model_key_resource_id(
                    tenant_id, str(row["provider_key"]), str(row["api_key_digest"])
                ),
                quota_group_id=row["quota_group_id"],
                weight=weights[row["deployment_id"]],
            )
            for row in rows
        )

    async def read_capacity_inflight(
        self, *, tenant_id: str, resource_type: str, resource_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, int]:
        """容量行当前在飞（无锁读，仅用于候选排序与"满员跳过"提示）。"""
        if not resource_ids:
            return {}
        rows = (
            await self._session.execute(
                sa.select(ModelCapacity.resource_id, ModelCapacity.inflight).where(
                    ModelCapacity.tenant_id == tenant_id,
                    ModelCapacity.resource_type == resource_type,
                    ModelCapacity.resource_id.in_(resource_ids),
                )
            )
        ).all()
        return {resource_id: int(inflight) for resource_id, inflight in rows}

    async def read_dispatch_states(
        self,
        *,
        tenant_id: str,
        model_id: uuid.UUID,
        policy_hash: str,
        deployment_ids: list[uuid.UUID],
    ) -> dict[uuid.UUID, DispatchState]:
        """一个冻结策略版本下的全部候选计数（缺失即"本版本从未选过"）。"""
        if not deployment_ids:
            return {}
        rows = (
            await self._session.execute(
                sa.select(
                    ModelDispatchState.deployment_id,
                    ModelDispatchState.dispatch_epoch,
                    ModelDispatchState.dispatch_count,
                    ModelDispatchState.last_picked_at,
                ).where(
                    ModelDispatchState.tenant_id == tenant_id,
                    ModelDispatchState.model_id == model_id,
                    ModelDispatchState.policy_hash == policy_hash,
                    ModelDispatchState.deployment_id.in_(deployment_ids),
                )
            )
        ).all()
        return {
            deployment_id: DispatchState(
                deployment_id=deployment_id,
                dispatch_epoch=int(dispatch_epoch),
                dispatch_count=int(dispatch_count),
                last_picked_at=last_picked_at,
            )
            for deployment_id, dispatch_epoch, dispatch_count, last_picked_at in rows
        }

    async def read_workload_inflight(
        self, *, tenant_id: str, deployment_ids: list[uuid.UUID], now: datetime
    ) -> dict[str, int]:
        """该池成员上**执行权仍有效**的在飞模型调用按工作负载计数。

        `UNKNOWN` 不再算在飞；已过期仍 `ACTIVE` 的租约也不算——执行方失约即必须停手
        （`renew` 同样拒绝过期租约），把它继续算成后台在飞会让 SIGKILL 之后的接管任务
        在老化间隔内白等，最终撞上自己的总超时。
        """
        if not deployment_ids:
            return {}
        rows = (
            await self._session.execute(
                sa.select(ModelCallLease.workload, sa.func.count())
                .where(
                    ModelCallLease.tenant_id == tenant_id,
                    ModelCallLease.deployment_id.in_(deployment_ids),
                    ModelCallLease.state == "ACTIVE",
                    ModelCallLease.lease_until > now,
                    ModelCallLease.is_deleted.is_(False),
                )
                .group_by(ModelCallLease.workload)
            )
        ).all()
        return {str(workload): int(count) for workload, count in rows}

    async def seed_dispatch_states(
        self,
        *,
        tenant_id: str,
        model_id: uuid.UUID,
        policy_hash: str,
        epoch: int,
        deployment_ids: Sequence[uuid.UUID],
        now: datetime,
    ) -> None:
        """为**尚无计数行**的候选建 0 计数行（新成员/新策略版本），不覆盖既有行。"""
        if not deployment_ids:
            return
        await self._session.execute(
            _SEED_DISPATCH_SQL.bindparams(
                sa.bindparam(
                    "deployment_ids",
                    type_=postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
                )
            ),
            {
                "tenant_id": tenant_id,
                "model_id": model_id,
                "policy_hash": policy_hash,
                "epoch": epoch,
                "now": now,
                "deployment_ids": list(deployment_ids),
            },
        )

    async def pick_and_bump_dispatch(
        self,
        *,
        tenant_id: str,
        model_id: uuid.UUID,
        policy_hash: str,
        epoch: int,
        now: datetime,
        picks: Sequence[tuple[uuid.UUID, int, float]],
        skip_locked: bool,
    ) -> DispatchState | None:
        """单条语句完成「按排序选成员 + epoch 归一 + 计数 +1」。

        `picks` 是 `(deployment_id, weight, inflight_ratio)` 的**可执行且未满员**候选；
        排序与旧的 Python 口径一致：`归一计数/权重 → 归一在飞 → 最久未被选 → 成员 ID`。
        `skip_locked=True` 时并发调用各取各的未锁行（多成员严格轮流）；`False` 时阻塞在
        唯一/剩余行上（单成员本来就必须串行）。返回 None 表示候选都还没有计数行。
        """
        if not picks:
            return None
        skip = "FOR UPDATE OF s SKIP LOCKED" if skip_locked else ""
        statement = sa.text(_SELECT_AND_BUMP_TEMPLATE.format(skip=skip)).bindparams(
            sa.bindparam("deployment_ids", type_=postgresql.ARRAY(postgresql.UUID(as_uuid=True))),
            sa.bindparam("weights", type_=postgresql.ARRAY(sa.Float)),
            sa.bindparam("ratios", type_=postgresql.ARRAY(sa.Float)),
        )
        row = (
            await self._session.execute(
                statement,
                {
                    "tenant_id": tenant_id,
                    "model_id": model_id,
                    "policy_hash": policy_hash,
                    "epoch": epoch,
                    "now": now,
                    "deployment_ids": [pick[0] for pick in picks],
                    "weights": [float(pick[1]) for pick in picks],
                    "ratios": [pick[2] for pick in picks],
                },
            )
        ).mappings().one_or_none()
        if row is None:
            return None
        return DispatchState(
            deployment_id=row["deployment_id"],
            dispatch_epoch=int(row["dispatch_epoch"]),
            dispatch_count=int(row["dispatch_count"]),
            last_picked_at=row["last_picked_at"],
        )
