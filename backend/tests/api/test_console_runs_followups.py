"""FU-02 后端：run_payload 补字段 + keyword 匹配 trace_id。

- run_payload 新增 latency_ms / error（截断 500）/ agent_definition{id,version}。
- list_recent keyword 扩展 OR 匹配 trace_id（内存 + PG 双实现同形）。
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from fluxion.repositories.trace_store import PostgresTraceStore
from fluxion.resources import ExecutionSnapshot
from fluxion.runtime.context import TraceEvent
from fluxion.runtime.tracing import InMemoryTraceStore, TraceRecord
from fluxion.services.console_payloads import run_payload


def _snapshot(exec_id: str, agent: bool = True) -> ExecutionSnapshot:
    return ExecutionSnapshot(
        execution_id=exec_id,
        tenant_id="tenant-a",
        user_id="user-1",
        runtime_profile_id="runtime-main",
        runtime_profile_version="7",
        agent_definition_id="agent_demo" if agent else None,
        agent_definition_version="3" if agent else None,
        model_resolution={"routes": []},
        trace_id=f"trace-{exec_id}",
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
    )


def _record(trace_id: str, exec_id: str, *, error: str | None = None) -> TraceRecord:
    return TraceRecord(
        trace_id=trace_id,
        execution_id=exec_id,
        tenant_id="tenant-a",
        runtime_profile_id="runtime-main",
        runtime_profile_version="7",
        snapshot=_snapshot(exec_id),
        events=(
            TraceEvent(
                name="model.response",
                tenant_id="tenant-a",
                execution_id=exec_id,
                trace_id=trace_id,
                attributes={},
            ),
        ),
        latency_ms=1234.0,
        error=error,
        model={"provider": "dev.echo", "latency_ms": 10},
        tools=({"tool": "demo", "ok": True},),
    )


def test_run_payload_exposes_latency_error_and_agent() -> None:
    payload = run_payload(_record("trace-1", "exec-1", error="boom " * 200))
    assert payload["latency_ms"] == 1234.0
    assert payload["status"] == "failed"
    assert isinstance(payload["error"], str)
    assert len(payload["error"]) <= 500  # type: ignore[arg-type]
    assert payload["agent_definition"] == {"id": "agent_demo", "version": "3"}


def test_run_payload_agent_none_without_agent() -> None:
    record = _record("trace-2", "exec-2")
    payload = run_payload(
        TraceRecord(
            trace_id=record.trace_id,
            execution_id=record.execution_id,
            tenant_id=record.tenant_id,
            runtime_profile_id=record.runtime_profile_id,
            runtime_profile_version=record.runtime_profile_version,
            snapshot=_snapshot(record.execution_id, agent=False),
            events=record.events,
            latency_ms=record.latency_ms,
            error=None,
            model=record.model,
            tools=record.tools,
        )
    )
    assert payload["status"] == "succeeded"
    assert payload["error"] is None
    assert payload["agent_definition"] is None


async def test_memory_keyword_matches_trace_id() -> None:
    store = InMemoryTraceStore()
    await store.append(_record("trace-abc-123", "exec-xyz"))
    rows, total = await store.list_recent(
        tenant_id="tenant-a", offset=0, limit=10, keyword="abc-123"
    )
    assert total == 1
    assert rows[0].execution_id == "exec-xyz"


@pytest.fixture
async def pg_engine() -> AsyncGenerator[AsyncEngine, None]:
    dsn = os.environ.get(
        "FLUXION_POSTGRES_DSN",
        "postgresql+asyncpg://mmuser:mmuser@localhost:5432/fluxion_test",
    )
    engine = create_async_engine(dsn)
    async with engine.begin() as connection:
        await connection.execute(text("TRUNCATE TABLE trace_records"))
    try:
        yield engine
    finally:
        await engine.dispose()


async def test_pg_keyword_matches_trace_id(pg_engine: AsyncEngine) -> None:
    store = PostgresTraceStore(engine=pg_engine)
    await store.initialize()
    trace_id = f"trace-{uuid.uuid4().hex[:8]}"
    await store.append(_record(trace_id, f"exec-{uuid.uuid4().hex[:8]}"))
    rows, total = await store.list_recent(
        tenant_id="tenant-a", offset=0, limit=10, keyword=trace_id[-6:]
    )
    assert total == 1
    assert rows[0].trace_id == trace_id
