"""PG 侧准入投影行与计数行的读写（设计 §3.2/§3.3）。

**热路径不锁策略行**：`run_admission_policy` 只在更高 `source_settings_revision` 时用一条
`INSERT … ON CONFLICT DO UPDATE … WHERE revision < 新版本` 原子应用（并发同版本写幂等 no-op），
不再有 `FOR UPDATE`——它曾持有整个受理事务，把 1000 个互不相关的用户串成一条全局队列
（2026-10-20 真 Pod 实测准入吞吐固定在 ~30/s）。调用方读回当前行判定低版本（stale）或同版本
阈值冲突。

计数仍是严格语义：`run_admission_counter` 的 USER 与 TENANT 两行在受理事务末尾加锁判定并自增
（USER → TENANT 锁序），持有到提交只有毫秒级；达阈值即整事务回滚，不留半建 Run，也不留只增
不减的占位。

为什么投影在 Runtime 自己的 PG：全局接纳上限的权威来源是 Console capacity 设置，Runtime 在
受理事务内应用**更高**版本；所有实例按同一事实判定，不按每 Pod 本地缓存独立判定（§3.7）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models.runtime import RunAdmissionCounter, RunAdmissionPolicy, RunRecord

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

    async def read_policy(self, *, tenant_id: str) -> PolicyRow | None:
        """无锁读取当前策略行（不存在返回 None）。"""
        row = await self._session.scalar(
            sa.select(RunAdmissionPolicy).where(RunAdmissionPolicy.tenant_id == tenant_id)
        )
        return None if row is None else _policy_row(row)

    async def upsert_policy(
        self,
        *,
        tenant_id: str,
        revision: int,
        max_open_runs_per_tenant: int,
        max_open_runs_per_user: int,
        now: datetime,
    ) -> PolicyRow:
        """无锁应用更高版本（单条原子 upsert）：并发同版本写幂等，后到者 no-op。

        版本相等时不更新（`WHERE revision < 新版本`）；读回的仍是当前行，由调用方判定
        「低版本 → stale 重取」或「同版本不同阈值 → 投影损坏」。
        """
        await self._session.execute(
            pg_insert(RunAdmissionPolicy)
            .values(
                tenant_id=tenant_id,
                source_settings_revision=revision,
                max_open_runs_per_tenant=max_open_runs_per_tenant,
                max_open_runs_per_user=max_open_runs_per_user,
            )
            .on_conflict_do_update(
                index_elements=["tenant_id"],
                set_={
                    "source_settings_revision": revision,
                    "max_open_runs_per_tenant": max_open_runs_per_tenant,
                    "max_open_runs_per_user": max_open_runs_per_user,
                    "update_time": now,
                },
                where=RunAdmissionPolicy.source_settings_revision < revision,
            )
        )
        row = await self._session.scalar(
            sa.select(RunAdmissionPolicy).where(RunAdmissionPolicy.tenant_id == tenant_id)
        )
        if row is None:  # pragma: no cover - upsert 后必然存在
            raise RuntimeError("admission policy row missing after upsert")
        return _policy_row(row)

    async def count_open_runs(
        self,
        *,
        tenant_id: str,
        exclude_run_id: object | None = None,
        limit: int | None = None,
    ) -> int:
        """租户在飞 Run 的实时计数（无锁 MVCC 读）：租户上限是护栏值，不锁共享计数行。

        `exclude_run_id`：调用方已在本事务内插入该 Run 行，计数只算「其它」在飞 Run。
        `limit`：只判定「是否达到上限」时传入上限值；计数在 LIMIT 处提前停止并返回该上限，
        不改变 `>= max` 的判定，避免大表上扫描全部在飞行。
        并发窗口内允许少量软超发（上限远高于真实用量）；每用户上限仍由 USER 计数行严格保证。
        """
        inner = (
            sa.select(sa.literal(1))
            .select_from(RunRecord)
            .where(
                RunRecord.tenant_id == tenant_id,
                RunRecord.admission_released_at.is_(None),
            )
        )
        if exclude_run_id is not None:
            inner = inner.where(RunRecord.id != exclude_run_id)
        if limit is not None:
            inner = inner.limit(limit)
        return int(
            await self._session.scalar(sa.select(sa.func.count()).select_from(inner.subquery()))
            or 0
        )

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

    async def decrement_counter(self, *, tenant_id: str, scope_type: str, scope_key: str) -> int:
        """把已锁定的计数行原地 -1，并在 0 处截断（绝不产生负计数）。

        终态释放与 Run 终态同事务：`admission_released_at` CAS 已保证每个 Run 恰释放一次，
        这里的截断是最后一道防线——早于准入计数的历史 Run 或人工种入的行不会把共享计数压到
        0 以下（`open_run_count >= 0` 是表级 CHECK，负值会直接违反约束并卡住终态收尾）。
        """
        count = await self._session.scalar(
            sa.update(RunAdmissionCounter)
            .where(
                RunAdmissionCounter.tenant_id == tenant_id,
                RunAdmissionCounter.scope_type == scope_type,
                RunAdmissionCounter.scope_key == scope_key,
            )
            .values(
                open_run_count=sa.func.greatest(RunAdmissionCounter.open_run_count - 1, 0)
            )
            .returning(RunAdmissionCounter.open_run_count)
        )
        if count is None:  # pragma: no cover - 行锁内已确认存在
            raise RuntimeError("admission counter row missing while decrementing")
        return int(count)
