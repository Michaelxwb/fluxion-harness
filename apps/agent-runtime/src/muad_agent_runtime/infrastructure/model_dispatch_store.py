"""PG 侧成员候选、调度计数与工作负载在飞的读写（设计 §3.1 ADR-04、§3.3、§3.7）。

- 调度计数 `runtime.model_dispatch_state` 以 `(tenant_id, model_id, deployment_id, policy_hash)`
  为唯一键：不同冻结策略版本（`dispatch_epoch_sec`/权重/老化）的分布分别解释、互不覆盖；
- 计数**只就地按共享 epoch 归一**：`dispatch_epoch` 与当前 epoch 不同的行读作 0，被选中时在
  同一条 UPDATE 里写回当前 epoch 并从 1 起算；不做全量清零，也不承载物理额度；
- 池调度 advisory 事务锁由调用方（`ModelAllocator`）先取；本模块只做带租户谓词的读写，
  不提交事务，也不发起任何网络请求。

为什么读 `control.model_deployment`：成员关系与启用状态由 Console 拥有（§3.3），Runtime 只读
同一 PG 的当前事实（先例：`model_capacity_store` 只读 `control.model_credential` 的额度上限）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .model_capacity_store import ModelCapacityStore
from .models.runtime import ModelCallLease, ModelCapacity, ModelDispatchState

_LIVE_MEMBERS_SQL = sa.text(
    "SELECT id, credential_id, enabled FROM control.model_deployment"
    " WHERE tenant_id = :tenant_id AND model_id = :model_id AND is_deleted = false"
)


@dataclass(frozen=True, slots=True)
class LiveMember:
    """`control.model_deployment` 行当前事实：启用与否但**不带权重**（权重随 Run 冻结）。"""

    deployment_id: uuid.UUID
    credential_id: uuid.UUID
    enabled: bool


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
        self, *, tenant_id: str, deployment_ids: list[uuid.UUID]
    ) -> dict[str, int]:
        """该池成员上真实在飞的模型调用按工作负载计数（UNKNOWN 不再算在飞）。"""
        if not deployment_ids:
            return {}
        rows = (
            await self._session.execute(
                sa.select(ModelCallLease.workload, sa.func.count())
                .where(
                    ModelCallLease.tenant_id == tenant_id,
                    ModelCallLease.deployment_id.in_(deployment_ids),
                    ModelCallLease.state == "ACTIVE",
                    ModelCallLease.is_deleted.is_(False),
                )
                .group_by(ModelCallLease.workload)
            )
        ).all()
        return {str(workload): int(count) for workload, count in rows}

    async def bump_dispatch_state(
        self,
        *,
        tenant_id: str,
        model_id: uuid.UUID,
        deployment_id: uuid.UUID,
        policy_hash: str,
        epoch: int,
        now: datetime,
    ) -> DispatchState:
        """把一次尝试计到被选中的成员：同一语句内按共享 epoch 归一后再 +1（池锁内调用）。"""
        statement = _bump_statement(
            tenant_id=tenant_id,
            model_id=model_id,
            deployment_id=deployment_id,
            policy_hash=policy_hash,
            epoch=epoch,
            now=now,
        )
        row = (await self._session.execute(statement)).one()
        return DispatchState(
            deployment_id=deployment_id,
            dispatch_epoch=int(row[0]),
            dispatch_count=int(row[1]),
            last_picked_at=row[2],
        )


def _bump_statement(
    *,
    tenant_id: str,
    model_id: uuid.UUID,
    deployment_id: uuid.UUID,
    policy_hash: str,
    epoch: int,
    now: datetime,
) -> Any:
    """原子 upsert + 计数：同 epoch 累加，旧 epoch 归一为 1（一条 UPDATE，不先清零）。"""
    return (
        pg_insert(ModelDispatchState)
        .values(
            tenant_id=tenant_id,
            model_id=model_id,
            deployment_id=deployment_id,
            policy_hash=policy_hash,
            dispatch_epoch=epoch,
            dispatch_count=1,
            last_picked_at=now,
            create_time=now,
            update_time=now,
        )
        .on_conflict_do_update(
            index_elements=["tenant_id", "model_id", "deployment_id", "policy_hash"],
            set_={
                "dispatch_epoch": epoch,
                "dispatch_count": sa.case(
                    (
                        ModelDispatchState.dispatch_epoch == epoch,
                        ModelDispatchState.dispatch_count + 1,
                    ),
                    else_=1,
                ),
                "last_picked_at": now,
                "update_time": now,
            },
        )
        .returning(
            ModelDispatchState.dispatch_epoch,
            ModelDispatchState.dispatch_count,
            ModelDispatchState.last_picked_at,
        )
    )
