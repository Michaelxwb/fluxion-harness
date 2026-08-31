"""TASK-006（Phase 6）生产 durable store 双库契约测试（FEAT-P6-06 装配前提）。

S-10 支撑：PostgresTraceStore / PostgresApprovalStore / PostgresEvalRunStore
是 production profile fail-fast 守卫（E-07/P0-5）要求的「显式 production
adapter」——本文件验证三者与 InMemory 实现同形（规则 7：SQLite/PG 双库共享
Contract Test）。

真实边界：
- SQLite 恒有（文件库，进程级重建 = 新 store 实例）；
- PostgreSQL 门控（FLUXION_REQUIRE_POSTGRES_CONTRACT=1，复用 local-pg-test-env
  fluxion_test 库）；
- 契约：TraceStore（append/get/query_by_execution/list_recent/get_by_execution）、
  ApprovalStore（create/get/decide/consume + CAS）、EvalRunStore（put/get/list）。
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncGenerator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from fluxion.repositories.approval_store import PostgresApprovalStore
from fluxion.repositories.eval_run_store import PostgresEvalRunStore
from fluxion.repositories.trace_store import PostgresTraceStore
from fluxion.resources import ExecutionSnapshot
from fluxion.runtime.context import TraceEvent
from fluxion.runtime.tracing import TraceRecord
from fluxion.services.approval_app import ApprovalRecord, ApprovalStatus
from fluxion.services.eval_app import EvalRunRecord

# ---------------------------------------------------------------------------
# 双库引擎参数化（与 test_secret_store 同模式）
#


def _engine_params() -> list[object]:
    params: list[object] = [pytest.param("sqlite", id="sqlite")]
    if os.environ.get("FLUXION_REQUIRE_POSTGRES_CONTRACT") == "1":
        params.append(pytest.param("postgres", id="postgres"))
    return params


@pytest.fixture(params=_engine_params())
async def engine(
    request: pytest.FixtureRequest, tmp_path: Path
) -> AsyncGenerator[tuple[AsyncEngine, str], None]:
    kind: str = request.param
    if kind == "sqlite":
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'durable.db'}")
    else:
        dsn = os.environ.get(
            "FLUXION_POSTGRES_DSN",
            "postgresql+asyncpg://mmuser:mmuser@localhost:5432/fluxion_test",
        )
        engine = create_async_engine(dsn)
    try:
        yield engine, kind
    finally:
        await engine.dispose()


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _trace_record(trace_id: str, *, tenant_id: str = "tenant-a") -> TraceRecord:
    snapshot = ExecutionSnapshot(
        execution_id=f"exec-{trace_id}",
        tenant_id=tenant_id,
        user_id="user-1",
        runtime_profile_id="runtime-main",
        runtime_profile_version="7",
        model_resolution={"provider_ref": {"id": "dev.echo", "version": "1"}},
        trace_id=trace_id,
    )
    events = (
        TraceEvent(
            name="model.response",
            tenant_id=tenant_id,
            execution_id=f"exec-{trace_id}",
            trace_id=trace_id,
            attributes={"answer": "清晰答复"},
        ),
    )
    return TraceRecord(
        trace_id=trace_id,
        execution_id=f"exec-{trace_id}",
        tenant_id=tenant_id,
        runtime_profile_id="runtime-main",
        runtime_profile_version="7",
        snapshot=snapshot,
        events=events,
        latency_ms=12.5,
        error=None,
        model={"provider": "dev.echo", "latency_ms": 10},
        tools=({"tool": "demo", "ok": True},),
    )


# ---------------------------------------------------------------------------
# PostgresTraceStore 契约
#


class TestTraceStoreContract:
    async def test_append_get_roundtrip(
        self, engine: tuple[AsyncEngine, str]
    ) -> None:
        engine_obj, _ = engine
        store = PostgresTraceStore(engine=engine_obj)
        await store.initialize()
        trace_id = _unique("trace")
        record = _trace_record(trace_id)

        await store.append(record)
        loaded = await store.get(trace_id)

        assert loaded is not None
        assert loaded.trace_id == trace_id
        assert loaded.tenant_id == record.tenant_id
        assert loaded.execution_id == record.execution_id
        assert loaded.latency_ms == record.latency_ms
        assert loaded.snapshot == record.snapshot
        assert list(loaded.events) == list(record.events)
        assert loaded.model == record.model

    async def test_tenant_isolation(
        self, engine: tuple[AsyncEngine, str]
    ) -> None:
        engine_obj, _ = engine
        store = PostgresTraceStore(engine=engine_obj)
        await store.initialize()
        trace_id = _unique("trace")
        await store.append(_trace_record(trace_id, tenant_id="tenant-a"))

        # 另一租户不可见（list_recent 按 tenant 过滤）
        records, total = await store.list_recent(tenant_id="tenant-b", offset=0, limit=10)
        assert total == 0
        assert records == []
        records_a, total_a = await store.list_recent(
            tenant_id="tenant-a", offset=0, limit=10
        )
        assert total_a >= 1
        assert any(r.trace_id == trace_id for r in records_a)

    async def test_query_by_execution_and_get_by_execution(
        self, engine: tuple[AsyncEngine, str]
    ) -> None:
        engine_obj, _ = engine
        store = PostgresTraceStore(engine=engine_obj)
        await store.initialize()
        trace_id = _unique("trace")
        record = _trace_record(trace_id)
        await store.append(record)

        since = record.snapshot.created_at - timedelta(seconds=1)
        found = await store.query_by_execution(
            tenant_id=record.tenant_id,
            execution_id=record.execution_id,
            since=since,
            limit=10,
        )
        assert [r.trace_id for r in found] == [trace_id]

        by_exec = await store.get_by_execution(
            tenant_id=record.tenant_id, execution_id=record.execution_id
        )
        assert by_exec is not None and by_exec.trace_id == trace_id

    async def test_append_same_trace_id_overwrites(
        self, engine: tuple[AsyncEngine, str]
    ) -> None:
        """与 InMemoryTraceStore 同形：同 trace_id 重复 append 覆盖（upsert）。"""
        engine_obj, _ = engine
        store = PostgresTraceStore(engine=engine_obj)
        await store.initialize()
        trace_id = _unique("trace")
        first = _trace_record(trace_id)
        second = replace(first, latency_ms=99.0)
        await store.append(first)
        await store.append(second)
        loaded = await store.get(trace_id)
        assert loaded is not None
        assert loaded.latency_ms == 99.0


# ---------------------------------------------------------------------------
# PostgresApprovalStore 契约
#


def _approval_record(approval_id: str, *, tenant_id: str = "tenant-a") -> ApprovalRecord:
    now = datetime.now(UTC)
    return ApprovalRecord(
        approval_id=approval_id,
        tenant_id=tenant_id,
        kind="runtime_profile",  # type: ignore[arg-type]
        resource_id="runtime-main",
        target_version="9",
        operation="rollback",
        requester_actor_id="actor-1",
        status=ApprovalStatus.PENDING,
        approver_actor_id=None,
        reason=None,
        expires_at=now + timedelta(minutes=30),
        created_at=now,
        decided_at=None,
    )


class TestApprovalStoreContract:
    async def test_create_get_decide_consume(
        self, engine: tuple[AsyncEngine, str]
    ) -> None:
        engine_obj, _ = engine
        store = PostgresApprovalStore(engine=engine_obj)
        await store.initialize()
        approval_id = _unique("approval")
        record = _approval_record(approval_id)

        created = await store.create(record)
        assert created.approval_id == approval_id

        loaded = await store.get(approval_id, tenant_id=record.tenant_id)
        assert loaded is not None
        assert loaded.status is ApprovalStatus.PENDING
        assert loaded.consumed_at is None

        decided = await store.decide(
            approval_id,
            tenant_id=record.tenant_id,
            approver_actor_id="approver-1",
            approved=True,
            reason="同意",
            decided_at=datetime.now(UTC),
        )
        assert decided.status is ApprovalStatus.APPROVED
        assert decided.approver_actor_id == "approver-1"
        assert decided.reason == "同意"

        consumed = await store.consume(
            approval_id, tenant_id=record.tenant_id, consumed_at=datetime.now(UTC)
        )
        assert consumed.consumed_at is not None

    async def test_duplicate_create_rejected(
        self, engine: tuple[AsyncEngine, str]
    ) -> None:
        engine_obj, _ = engine
        store = PostgresApprovalStore(engine=engine_obj)
        await store.initialize()
        approval_id = _unique("approval")
        await store.create(_approval_record(approval_id))
        with pytest.raises(Exception, match="already exists"):
            await store.create(_approval_record(approval_id))

    async def test_double_decide_rejected(
        self, engine: tuple[AsyncEngine, str]
    ) -> None:
        engine_obj, _ = engine
        store = PostgresApprovalStore(engine=engine_obj)
        await store.initialize()
        approval_id = _unique("approval")
        await store.create(_approval_record(approval_id))
        await store.decide(
            approval_id,
            tenant_id="tenant-a",
            approver_actor_id="approver-1",
            approved=True,
            reason=None,
            decided_at=datetime.now(UTC),
        )
        with pytest.raises(Exception, match="already decided"):
            await store.decide(
                approval_id,
                tenant_id="tenant-a",
                approver_actor_id="approver-1",
                approved=False,
                reason=None,
                decided_at=datetime.now(UTC),
            )

    async def test_double_consume_rejected(
        self, engine: tuple[AsyncEngine, str]
    ) -> None:
        """A9：审批单一次性消费——DB 级 CAS（UPDATE ... WHERE consumed_at IS NULL）。"""
        engine_obj, _ = engine
        store = PostgresApprovalStore(engine=engine_obj)
        await store.initialize()
        approval_id = _unique("approval")
        await store.create(_approval_record(approval_id))
        await store.consume(
            approval_id, tenant_id="tenant-a", consumed_at=datetime.now(UTC)
        )
        with pytest.raises(Exception, match="already consumed"):
            await store.consume(
                approval_id, tenant_id="tenant-a", consumed_at=datetime.now(UTC)
            )

    async def test_tenant_isolation(
        self, engine: tuple[AsyncEngine, str]
    ) -> None:
        engine_obj, _ = engine
        store = PostgresApprovalStore(engine=engine_obj)
        await store.initialize()
        approval_id = _unique("approval")
        await store.create(_approval_record(approval_id, tenant_id="tenant-a"))
        assert await store.get(approval_id, tenant_id="tenant-b") is None


# ---------------------------------------------------------------------------
# PostgresEvalRunStore 契约
#


def _eval_run_record(run_id: str, *, tenant_id: str = "tenant-a") -> EvalRunRecord:
    return EvalRunRecord(
        run_id=run_id,
        tenant_id=tenant_id,
        eval_set_id="quality",
        eval_set_version="3",
        runtime_profile_id="runtime-main",
        runtime_profile_version="7",
        trace_id=f"trace-{run_id}",
        execution_snapshot={"execution_id": f"exec-{run_id}", "digest": "sha256:abc"},
        score=0.9,
        passed=True,
        created_at=datetime.now(UTC),
    )


class TestEvalRunStoreContract:
    async def test_put_get_list_roundtrip(
        self, engine: tuple[AsyncEngine, str]
    ) -> None:
        engine_obj, _ = engine
        store = PostgresEvalRunStore(engine=engine_obj)
        await store.initialize()
        run_id = _unique("run")
        record = _eval_run_record(run_id)

        await store.put(record)
        loaded = await store.get(run_id, tenant_id=record.tenant_id)
        assert loaded is not None
        assert loaded.run_id == run_id
        assert loaded.score == record.score
        assert loaded.passed is True
        assert loaded.execution_snapshot == record.execution_snapshot

        listed = await store.list(tenant_id=record.tenant_id)
        assert any(r.run_id == run_id for r in listed)

    async def test_duplicate_put_rejected(
        self, engine: tuple[AsyncEngine, str]
    ) -> None:
        engine_obj, _ = engine
        store = PostgresEvalRunStore(engine=engine_obj)
        await store.initialize()
        run_id = _unique("run")
        await store.put(_eval_run_record(run_id))
        with pytest.raises(Exception, match="已存在"):
            await store.put(_eval_run_record(run_id))

    async def test_tenant_isolation(
        self, engine: tuple[AsyncEngine, str]
    ) -> None:
        engine_obj, _ = engine
        store = PostgresEvalRunStore(engine=engine_obj)
        await store.initialize()
        run_id = _unique("run")
        await store.put(_eval_run_record(run_id, tenant_id="tenant-a"))
        assert await store.get(run_id, tenant_id="tenant-b") is None
        assert await store.list(tenant_id="tenant-b") == []
