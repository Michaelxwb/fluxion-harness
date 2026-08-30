"""PostgresEvalRunStore：EvalRunStore 的 PG 持久化实现（Phase 6 TASK-006，P0-5）。

production profile 的「显式 production adapter」——与 ``InMemoryEvalRunStore``
同形（put/get/list）。Release Gate（enforced）读取 EvalRun 事实评估发布决策，
run 事实必须跨进程持久（否则多副本 Console 各自为政，gate 语义失效）。

- put 幂等拒绝：同 (tenant_id, run_id) 重复写入 → ValueError（与 InMemory 一致）；
- 全方法 deadline（规则 18）；tenant scope 强制（规则 16）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any, TypeVar

from sqlalchemy import insert, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from fluxion.registry.schema import eval_runs
from fluxion.services.eval_app import EvalRunRecord

_T = TypeVar("_T")
_TIMEOUT_SECONDS = 10.0


class EvalRunStorePersistenceError(RuntimeError):
    """EvalRunStore 持久化失败（明确失败，不静默）。"""

    code = "eval_run_store_error"


class PostgresEvalRunStore:
    """EvalRun 落库实现（engine 注入：SQLite 契约 / PostgreSQL 生产）。"""

    def __init__(
        self, *, engine: AsyncEngine, timeout_seconds: float = _TIMEOUT_SECONDS
    ) -> None:
        self._engine = engine
        self._timeout_seconds = timeout_seconds

    async def initialize(self) -> None:
        """幂等建表（eval_runs）。"""
        async with self._engine.begin() as conn:
            await conn.run_sync(
                lambda sync_conn: eval_runs.create(sync_conn, checkfirst=True)
            )

    async def put(self, record: EvalRunRecord) -> None:
        async def _put() -> None:
            async with self._engine.begin() as conn:
                existing: Any = await conn.execute(
                    select(eval_runs.c.run_id).where(
                        eval_runs.c.tenant_id == record.tenant_id,
                        eval_runs.c.run_id == record.run_id,
                    )
                )
                if existing.scalar_one_or_none() is not None:
                    raise ValueError(f"EvalRun 已存在: {record.run_id}")
                await conn.execute(
                    insert(eval_runs).values(**_to_row(record))
                )

        await self._with_deadline(_put(), f"put {record.run_id}")

    async def get(self, run_id: str, *, tenant_id: str) -> EvalRunRecord | None:
        async def _get() -> Any:
            async with self._engine.connect() as conn:
                result = await conn.execute(
                    select(eval_runs).where(
                        eval_runs.c.tenant_id == tenant_id,
                        eval_runs.c.run_id == run_id,
                    )
                )
                return result.mappings().first()

        row = await self._with_deadline(_get(), f"get {run_id}")
        return _from_row(row) if row is not None else None

    async def list(self, *, tenant_id: str) -> list[EvalRunRecord]:
        async def _list() -> list[Any]:
            async with self._engine.connect() as conn:
                result = await conn.execute(
                    select(eval_runs)
                    .where(eval_runs.c.tenant_id == tenant_id)
                    .order_by(eval_runs.c.created_at.asc())
                )
                return list(result.mappings().all())

        rows = await self._with_deadline(_list(), f"list {tenant_id}")
        return [_from_row(row) for row in rows]

    async def _with_deadline(
        self, coro: Coroutine[Any, Any, _T], label: str
    ) -> _T:
        try:
            return await asyncio.wait_for(coro, timeout=self._timeout_seconds)
        except TimeoutError as error:
            raise EvalRunStorePersistenceError(
                f"{label} 超时（>{self._timeout_seconds}s）"
            ) from error
        except SQLAlchemyError as error:
            raise EvalRunStorePersistenceError(f"{label} 失败: {error}") from error


# ---------------------------------------------------------------------------
# EvalRunRecord ⇄ 行 序列化
#


def _to_row(record: EvalRunRecord) -> dict[str, Any]:
    return {
        "tenant_id": record.tenant_id,
        "run_id": record.run_id,
        "eval_set_id": record.eval_set_id,
        "eval_set_version": record.eval_set_version,
        "runtime_profile_id": record.runtime_profile_id,
        "runtime_profile_version": record.runtime_profile_version,
        "trace_id": record.trace_id,
        "execution_snapshot_json": record.execution_snapshot,
        "score": record.score,
        "passed": record.passed,
        "created_at": record.created_at,
    }


def _from_row(row: Any) -> EvalRunRecord:
    return EvalRunRecord(
        run_id=str(row["run_id"]),
        tenant_id=str(row["tenant_id"]),
        eval_set_id=str(row["eval_set_id"]),
        eval_set_version=str(row["eval_set_version"]),
        runtime_profile_id=str(row["runtime_profile_id"]),
        runtime_profile_version=str(row["runtime_profile_version"]),
        trace_id=str(row["trace_id"]),
        execution_snapshot=dict(row["execution_snapshot_json"]),
        score=float(row["score"]),
        passed=bool(row["passed"]),
        created_at=row["created_at"],
    )


__all__ = ["EvalRunStorePersistenceError", "PostgresEvalRunStore"]
