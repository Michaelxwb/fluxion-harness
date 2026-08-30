"""PostgresTraceStore：TraceStore 的 PG 持久化实现（Phase 6 TASK-006，P0-5）。

production profile 的「显式 production adapter」——与 ``InMemoryTraceStore``
同形（append/get/query_by_execution/list_recent/get_by_execution），TraceRecord
（含 ExecutionSnapshot/TraceEvent）经 JSON 序列化落 ``trace_records`` 表。

- 同 trace_id 重复 append 为 upsert（与 InMemory 覆盖语义一致）；
- 全方法 deadline（规则 18）：超时/库错误 → TraceStoreError，不静默吞；
- tenant scope 全链路（规则 16）：查询强制带 tenant；
- SQLite/PG 双库同 DDL（规则 7，Contract Test 与 InMemory 实现同形）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from datetime import datetime
from typing import Any, TypeVar

from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from fluxion.registry.schema import trace_records
from fluxion.resources import ExecutionSnapshot
from fluxion.runtime.context import TraceEvent
from fluxion.runtime.tracing import TraceRecord

_T = TypeVar("_T")
_TIMEOUT_SECONDS = 10.0


class TraceStoreError(RuntimeError):
    """TraceStore 持久化失败（明确失败，不静默）。"""

    code = "trace_store_error"


class PostgresTraceStore:
    """Trace 落库实现（engine 注入：SQLite 契约 / PostgreSQL 生产）。"""

    def __init__(self, *, engine: AsyncEngine, timeout_seconds: float = _TIMEOUT_SECONDS) -> None:
        self._engine = engine
        self._timeout_seconds = timeout_seconds

    async def initialize(self) -> None:
        """幂等建表（trace_records）。"""
        async with self._engine.begin() as conn:
            await conn.run_sync(
                lambda sync_conn: trace_records.create(sync_conn, checkfirst=True)
            )

    async def append(self, record: TraceRecord) -> None:
        values = _to_row(record)
        await self._with_deadline(self._upsert(record.trace_id, values), "append")

    async def get(self, trace_id: str) -> TraceRecord | None:
        rows = await self._with_deadline(self._fetch(trace_id), f"get {trace_id}")
        if not rows:
            return None
        return _from_row(rows[0])

    async def query_by_execution(
        self,
        *,
        tenant_id: str,
        execution_id: str,
        since: datetime,
        limit: int,
    ) -> list[TraceRecord]:
        if limit < 1:
            raise ValueError("limit must be positive")

        async def _query() -> list[Any]:
            async with self._engine.connect() as conn:
                result = await conn.execute(
                    select(trace_records)
                    .where(trace_records.c.tenant_id == tenant_id)
                    .where(trace_records.c.execution_id == execution_id)
                    .where(trace_records.c.created_at >= since)
                    .order_by(trace_records.c.created_at.desc())
                    .limit(limit)
                )
                return list(result.mappings().all())

        rows = await self._with_deadline(
            _query(), f"query_by_execution {tenant_id}/{execution_id}"
        )
        return [_from_row(row) for row in rows]

    async def list_recent(
        self, *, tenant_id: str, offset: int, limit: int
    ) -> tuple[list[TraceRecord], int]:
        async def _query() -> tuple[list[Any], int]:
            async with self._engine.connect() as conn:
                total_row: Any = await conn.execute(
                    select(func.count())
                    .select_from(trace_records)
                    .where(trace_records.c.tenant_id == tenant_id)
                )
                total = int(total_row.scalar_one())
                result = await conn.execute(
                    select(trace_records)
                    .where(trace_records.c.tenant_id == tenant_id)
                    .order_by(trace_records.c.created_at.desc())
                    .offset(offset)
                    .limit(limit)
                )
                return list(result.mappings().all()), total

        rows, total = await self._with_deadline(
            _query(), f"list_recent {tenant_id}"
        )
        return [_from_row(row) for row in rows], total

    async def get_by_execution(
        self, *, tenant_id: str, execution_id: str
    ) -> TraceRecord | None:
        async def _query() -> Any:
            async with self._engine.connect() as conn:
                result = await conn.execute(
                    select(trace_records)
                    .where(trace_records.c.tenant_id == tenant_id)
                    .where(trace_records.c.execution_id == execution_id)
                    .order_by(trace_records.c.created_at.desc())
                    .limit(1)
                )
                return result.mappings().first()

        row = await self._with_deadline(
            _query(), f"get_by_execution {tenant_id}/{execution_id}"
        )
        return _from_row(row) if row is not None else None

    # ---- 内部实现 ----

    async def _upsert(self, trace_id: str, values: dict[str, Any]) -> None:
        async with self._engine.begin() as conn:
            existing: Any = await conn.execute(
                select(trace_records.c.trace_id).where(
                    trace_records.c.trace_id == trace_id
                )
            )
            if existing.scalar_one_or_none() is None:
                await conn.execute(insert(trace_records).values(**values))
            else:
                await conn.execute(
                    update(trace_records)
                    .where(trace_records.c.trace_id == trace_id)
                    .values(**values)
                )

    async def _fetch(self, trace_id: str) -> list[Any]:
        async with self._engine.connect() as conn:
            result = await conn.execute(
                select(trace_records).where(trace_records.c.trace_id == trace_id)
            )
            return list(result.mappings().all())

    async def _with_deadline(
        self, coro: Coroutine[Any, Any, _T], label: str
    ) -> _T:
        try:
            return await asyncio.wait_for(coro, timeout=self._timeout_seconds)
        except TimeoutError as error:
            raise TraceStoreError(f"{label} 超时（>{self._timeout_seconds}s）") from error
        except SQLAlchemyError as error:
            raise TraceStoreError(f"{label} 失败: {error}") from error


# ---------------------------------------------------------------------------
# TraceRecord ⇄ 行 序列化（ExecutionSnapshot/TraceEvent 经 JSON 往返）
#


def _to_row(record: TraceRecord) -> dict[str, Any]:
    return {
        "trace_id": record.trace_id,
        "tenant_id": record.tenant_id,
        "execution_id": record.execution_id,
        "runtime_profile_id": record.runtime_profile_id,
        "runtime_profile_version": record.runtime_profile_version,
        "snapshot_json": record.snapshot.model_dump(mode="json"),
        "events_json": [
            {
                "name": event.name,
                "tenant_id": event.tenant_id,
                "execution_id": event.execution_id,
                "trace_id": event.trace_id,
                "attributes": event.attributes,
            }
            for event in record.events
        ],
        "latency_ms": record.latency_ms,
        "error": record.error,
        "model_json": record.model,
        "tools_json": list(record.tools),
        "hooks_json": list(record.hooks),
        "created_at": record.snapshot.created_at,
    }


def _from_row(row: Any) -> TraceRecord:
    snapshot = ExecutionSnapshot.model_validate(dict(row["snapshot_json"]))
    events = tuple(
        TraceEvent(
            name=event["name"],
            tenant_id=event["tenant_id"],
            execution_id=event["execution_id"],
            trace_id=event["trace_id"],
            attributes=dict(event.get("attributes") or {}),
        )
        for event in (row["events_json"] or [])
    )
    return TraceRecord(
        trace_id=str(row["trace_id"]),
        execution_id=str(row["execution_id"]),
        tenant_id=str(row["tenant_id"]),
        runtime_profile_id=str(row["runtime_profile_id"]),
        runtime_profile_version=str(row["runtime_profile_version"]),
        snapshot=snapshot,
        events=events,
        latency_ms=float(row["latency_ms"]),
        error=row["error"],
        model=row["model_json"],
        tools=tuple(row["tools_json"] or ()),
        hooks=tuple(row["hooks_json"] or ()),
    )


__all__ = ["PostgresTraceStore", "TraceStoreError"]
