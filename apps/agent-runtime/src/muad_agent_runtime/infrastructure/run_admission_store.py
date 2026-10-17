"""PG 侧全局准入投影行与计数行的读写（设计 §3.2/§3.3 固定锁序）。

固定锁序由服务层调用顺序保证：`run_admission_policy` 版本行 → Conversation → Run →
USER 计数行 → TENANT 计数行。本模块只做带租户谓词的加锁读写，不做阈值判定，也不提交事务。

为什么投影在 Runtime 自己的 PG：全局接纳上限的权威来源是 Console capacity 设置，Runtime 只在
受理事务内把**更高** `source_settings_revision` 应用到本表；所有实例按同一 PG 行计数与判定，
不按每 Pod 本地缓存独立判定（§3.7）。行缺失即按本次配置建立（只可能发生在首次受理）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models.runtime import RunAdmissionCounter, RunAdmissionPolicy

SCOPE_USER = "USER"
SCOPE_TENANT = "TENANT"


@dataclass(frozen=True, slots=True)
class PolicyRow:
    """`runtime.run_admission_policy` 当前行（全实例共用的有效阈值投影）。"""

    source_settings_revision: int
    max_open_runs_per_tenant: int
    max_open_runs_per_user: int


def _policy_row(row: RunAdmissionPolicy) -> PolicyRow:
    return PolicyRow(
        source_settings_revision=row.source_settings_revision,
        max_open_runs_per_tenant=row.max_open_runs_per_tenant,
        max_open_runs_per_user=row.max_open_runs_per_user,
    )


class RunAdmissionStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def lock_policy(
        self,
        *,
        tenant_id: str,
        revision: int,
        max_open_runs_per_tenant: int,
        max_open_runs_per_user: int,
    ) -> PolicyRow:
        """策略行锁：缺失按本次配置建立，随后锁回当前行（准入事务的第一条语句）。

        调用方必须在任何 Conversation/Run/计数行锁之前调用本方法（§3.2 固定锁序）。
        """
        await self._session.execute(
            pg_insert(RunAdmissionPolicy)
            .values(
                tenant_id=tenant_id,
                source_settings_revision=revision,
                max_open_runs_per_tenant=max_open_runs_per_tenant,
                max_open_runs_per_user=max_open_runs_per_user,
            )
            .on_conflict_do_nothing(index_elements=["tenant_id"])
        )
        row = await self._session.scalar(
            sa.select(RunAdmissionPolicy)
            .where(RunAdmissionPolicy.tenant_id == tenant_id)
            .with_for_update()
        )
        if row is None:  # pragma: no cover - upsert 后必然存在
            raise RuntimeError("admission policy row missing after upsert")
        return _policy_row(row)

    async def write_policy(
        self,
        *,
        tenant_id: str,
        revision: int,
        max_open_runs_per_tenant: int,
        max_open_runs_per_user: int,
        now: datetime,
    ) -> PolicyRow:
        """把更高版本与阈值写回策略行（调用方已持有该行锁）。"""
        row = await self._session.scalar(
            sa.update(RunAdmissionPolicy)
            .where(RunAdmissionPolicy.tenant_id == tenant_id)
            .values(
                source_settings_revision=revision,
                max_open_runs_per_tenant=max_open_runs_per_tenant,
                max_open_runs_per_user=max_open_runs_per_user,
                update_time=now,
            )
            .returning(RunAdmissionPolicy)
        )
        if row is None:  # pragma: no cover - 行锁内已确认存在
            raise RuntimeError("admission policy row missing while writing")
        return _policy_row(row)

    async def lock_counter(self, *, tenant_id: str, scope_type: str, scope_key: str) -> int:
        """计数行锁（缺失按 0 建立）：返回当前计数。

        调用方在锁定期间判定与自增；Run 是权威占位事实，本表只承载事务内计数（§3.3）。
        """
        await self._session.execute(
            pg_insert(RunAdmissionCounter)
            .values(tenant_id=tenant_id, scope_type=scope_type, scope_key=scope_key)
            .on_conflict_do_nothing(index_elements=["tenant_id", "scope_type", "scope_key"])
        )
        count = await self._session.scalar(
            sa.select(RunAdmissionCounter.open_run_count)
            .where(
                RunAdmissionCounter.tenant_id == tenant_id,
                RunAdmissionCounter.scope_type == scope_type,
                RunAdmissionCounter.scope_key == scope_key,
            )
            .with_for_update()
        )
        if count is None:  # pragma: no cover - upsert 后必然存在
            raise RuntimeError("admission counter row missing after upsert")
        return int(count)

    async def increment_counter(self, *, tenant_id: str, scope_type: str, scope_key: str) -> int:
        """把已锁定的计数行原地 +1（调用方在同一事务内先经 `lock_counter` 判定阈值）。

        自增与 Run 行同事务提交：达阈值被拒时调用方回滚整个事务，计数与 Run 一起消失，
        不出现「只增不减」的占位残留；相对自增在行锁下与并发受理串行收敛到阈值。
        """
        count = await self._session.scalar(
            sa.update(RunAdmissionCounter)
            .where(
                RunAdmissionCounter.tenant_id == tenant_id,
                RunAdmissionCounter.scope_type == scope_type,
                RunAdmissionCounter.scope_key == scope_key,
            )
            .values(open_run_count=RunAdmissionCounter.open_run_count + 1)
            .returning(RunAdmissionCounter.open_run_count)
        )
        if count is None:  # pragma: no cover - 行锁内已确认存在
            raise RuntimeError("admission counter row missing while incrementing")
        return int(count)
