"""workflow_run 投影表双库契约（TASK-008 / RULE-backend-database-001）。

SQLite 恒执行；PostgreSQL 由 `FLUXION_REQUIRE_POSTGRES_CONTRACT=1` 门控（S-R10，
需要真实 PostgreSQL）。断言：
- schema：全字段可读写、status 默认 running、node_states/pinned_refs JSON 列；
- upsert 幂等（ON CONFLICT (run_id)，双 dialect 一致）；
- tenant scope：跨租户 get/list 隔离（rule 16 / RULE-P3-06）；
- node_states 整列批写（PATTERN-backend-003：单行 JSON 列，无逐节点行、无 N+1）；
- 索引 `idx_wf_run_tenant` / `idx_wf_run_exec` 存在。
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator

import pytest

from fluxion.registry import (
    PostgreSQLRegistryStore,
    SQLiteRegistryStore,
)
from fluxion.registry.schema import metadata
from fluxion.registry.sqlalchemy_store import SQLAlchemyRegistryStore


def _sqlite_factory() -> SQLAlchemyRegistryStore:
    return SQLiteRegistryStore("sqlite+aiosqlite:///:memory:", reset_on_initialize=True)


def _postgres_factory() -> SQLAlchemyRegistryStore:
    dsn = os.environ.get(
        "FLUXION_POSTGRES_DSN",
        "postgresql+asyncpg://mmuser:mmuser@localhost:5432/fluxion_test",
    )
    return PostgreSQLRegistryStore(dsn, reset_on_initialize=True)


def _store_params() -> list[object]:
    params: list[object] = [pytest.param(_sqlite_factory, id="sqlite")]
    if os.environ.get("FLUXION_REQUIRE_POSTGRES_CONTRACT") == "1":
        params.append(pytest.param(_postgres_factory, id="postgres"))
    return params


@pytest.fixture(params=_store_params())
async def store(request: pytest.FixtureRequest) -> AsyncGenerator[SQLAlchemyRegistryStore, None]:
    factory: object = request.param
    instance = factory()  # type: ignore[operator]
    await instance.initialize()
    try:
        yield instance
    finally:
        await instance.close()


async def _upsert(
    store: SQLAlchemyRegistryStore,
    *,
    tenant_id: str = "tenant-a",
    run_id: str = "wf:exec-1",
    workflow_id: str = "wf",
    version: int = 1,
    status: str = "running",
) -> None:
    await store.upsert_workflow_run(
        tenant_id=tenant_id,
        run_id=run_id,
        workflow_id=workflow_id,
        workflow_version=version,
        execution_id="exec-1",
        trace_id="trace-1",
        pinned_refs=[{"kind": "workflow", "id": workflow_id, "version": "1"}],
        status=status,
        node_states={"prepare": {"status": "succeeded", "output_ref": "run:1", "error": None}},
    )


async def test_workflow_run_upsert_then_get_roundtrip(store: SQLAlchemyRegistryStore) -> None:
    """schema 契约：全字段读写 + status 默认值 + JSON 列。"""
    await _upsert(store)
    row = await store.get_workflow_run(tenant_id="tenant-a", run_id="wf:exec-1")
    assert row is not None
    assert row["run_id"] == "wf:exec-1"
    assert row["tenant_id"] == "tenant-a"
    assert row["workflow_id"] == "wf"
    assert row["workflow_version"] == 1
    assert row["execution_id"] == "exec-1"
    assert row["trace_id"] == "trace-1"
    assert row["status"] == "running"
    assert row["pinned_refs"] == [{"kind": "workflow", "id": "wf", "version": "1"}]
    assert row["node_states"]["prepare"]["status"] == "succeeded"
    assert row["created_at"] is not None and row["updated_at"] is not None

    # 默认 status=running（未显式传 status）
    await store.upsert_workflow_run(
        tenant_id="tenant-a",
        run_id="wf:default-status",
        workflow_id="wf",
        workflow_version=1,
        execution_id="exec-default",
        trace_id="trace-default",
        pinned_refs=[],
    )
    row2 = await store.get_workflow_run(tenant_id="tenant-a", run_id="wf:default-status")
    assert row2 is not None and row2["status"] == "running"


async def test_workflow_run_upsert_is_idempotent(store: SQLAlchemyRegistryStore) -> None:
    """upsert 幂等：同 run_id 二次 upsert 覆盖单行（ON CONFLICT，双 dialect 一致）。"""
    await _upsert(store)
    await _upsert(store, status="succeeded")
    row = await store.get_workflow_run(tenant_id="tenant-a", run_id="wf:exec-1")
    assert row is not None and row["status"] == "succeeded"
    rows, total = await store.list_workflow_runs(
        tenant_id="tenant-a", workflow_id="wf", limit=20, offset=0
    )
    assert total == 1


async def test_workflow_run_tenant_scope(store: SQLAlchemyRegistryStore) -> None:
    """tenant scope（RULE-P3-06）：跨租户 get/list 不可见。"""
    await _upsert(store, tenant_id="tenant-a")
    await _upsert(store, tenant_id="tenant-b", run_id="wf:exec-b")

    # 跨租户 get → None
    assert await store.get_workflow_run(tenant_id="tenant-b", run_id="wf:exec-1") is None
    assert await store.get_workflow_run(tenant_id="tenant-a", run_id="wf:exec-b") is None

    # list 只回本租户
    rows_a, total_a = await store.list_workflow_runs(
        tenant_id="tenant-a", workflow_id="wf", limit=20, offset=0
    )
    assert {r["run_id"] for r in rows_a} == {"wf:exec-1"}
    assert total_a == 1


async def test_workflow_run_composite_pk_tenant_isolation(
    store: SQLAlchemyRegistryStore,
) -> None:
    """复合 PK (tenant_id, run_id)：跨租户同 run_id（同 workflow+execution）不串写。"""
    await _upsert(store, tenant_id="tenant-a", run_id="wf:shared")
    await _upsert(store, tenant_id="tenant-b", run_id="wf:shared", status="succeeded")
    row_a = await store.get_workflow_run(tenant_id="tenant-a", run_id="wf:shared")
    row_b = await store.get_workflow_run(tenant_id="tenant-b", run_id="wf:shared")
    assert row_a is not None and row_a["status"] == "running"  # 不被 tenant-b 覆盖
    assert row_b is not None and row_b["status"] == "succeeded"
    _, total_a = await store.list_workflow_runs(
        tenant_id="tenant-a", workflow_id="wf", limit=10, offset=0
    )
    _, total_b = await store.list_workflow_runs(
        tenant_id="tenant-b", workflow_id="wf", limit=10, offset=0
    )
    assert total_a == 1 and total_b == 1


async def test_workflow_run_node_states_is_single_json_column(store: SQLAlchemyRegistryStore) -> None:
    """PATTERN-backend-003：node_states 是单行 JSON 列整批写，无逐节点行（无 N+1）。"""
    node_states = {
        f"node-{i}": {"status": "succeeded", "output_ref": f"run:{i}", "error": None}
        for i in range(20)
    }
    await store.upsert_workflow_run(
        tenant_id="tenant-a",
        run_id="wf:batch",
        workflow_id="wf",
        workflow_version=1,
        execution_id="exec-batch",
        trace_id="trace-batch",
        pinned_refs=[],
        node_states=node_states,
    )
    rows, total = await store.list_workflow_runs(
        tenant_id="tenant-a", workflow_id="wf", limit=20, offset=0
    )
    # 一次 upsert 只产生 1 行投影（而非 20 行逐节点行）
    assert total == 1
    assert rows[0]["node_states"]["node-19"]["status"] == "succeeded"
    assert len(rows[0]["node_states"]) == 20


def test_workflow_run_index_declarations() -> None:
    """索引契约：schema 声明 idx_wf_run_tenant / idx_wf_run_exec（双库 create_all 落地）。"""
    table = metadata.tables["workflow_run"]
    index_names = {index.name for index in table.indexes}
    assert {"idx_wf_run_tenant", "idx_wf_run_exec"} <= index_names
