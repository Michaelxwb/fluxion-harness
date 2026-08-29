"""TASK-011（Phase 5）workflow runs list-all 端点（S-12）。

真实边界：真实 SQLite registry（workflow_run 投影表真实 upsert 行）+ 真实
WorkflowProjectionService + 真实 HTTP（ASGITransport + 统一 envelope）。

覆盖：跨工作流 list-all（GET /api/v1/workflows/runs，无 workflow_id 过滤）+
分页 {items,page,page_size,total} + tenant scope（跨租户隔离）。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from fluxion.api.middleware import RequestContextMiddleware
from fluxion.api.responses import success  # noqa: F401 —— 同一 envelope 家族
from fluxion.api.workflow import register_workflow_projection_routes
from fluxion.registry import SQLiteRegistryStore
from fluxion.services.workflow_projection import WorkflowProjectionService


@pytest.fixture
async def store(tmp_path: Path) -> AsyncGenerator[SQLiteRegistryStore, None]:
    store = SQLiteRegistryStore(f"sqlite+aiosqlite:///{tmp_path / 'runs.db'}")
    await store.initialize()
    try:
        yield store
    finally:
        await store.close()


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


async def _seed_run(
    store: SQLiteRegistryStore,
    *,
    tenant_id: str,
    workflow_id: str,
    execution_id: str,
    status: str = "succeeded",
) -> None:
    await store.upsert_workflow_run(
        tenant_id=tenant_id,
        run_id=f"{workflow_id}:{execution_id}",
        workflow_id=workflow_id,
        workflow_version=1,
        execution_id=execution_id,
        trace_id=f"trace-{execution_id}",
        pinned_refs=[{"kind": "workflow", "id": workflow_id, "version": "1"}],
        status=status,
    )


@pytest.mark.asyncio
async def test_s12_list_all_runs_paginated_tenant_scoped(store: SQLiteRegistryStore) -> None:
    tenant = _unique("tenant")
    other = _unique("tenant")
    # 两个工作流 × 3 条 run（本租户）+ 他租户 2 条
    for i in range(3):
        await _seed_run(store, tenant_id=tenant, workflow_id="flow-x", execution_id=f"ex-x-{i}")
        await _seed_run(store, tenant_id=tenant, workflow_id="flow-y", execution_id=f"ex-y-{i}")
    await _seed_run(store, tenant_id=other, workflow_id="flow-x", execution_id="ex-other-1")
    await _seed_run(store, tenant_id=other, workflow_id="flow-x", execution_id="ex-other-2")

    service = WorkflowProjectionService(store)
    app = FastAPI()
    # 非 dev 模式：真实租户头生效（dev_mode 会把 tenant 固定为 "dev"）
    app.add_middleware(RequestContextMiddleware)
    register_workflow_projection_routes(app, projection_service=service)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://runs") as client:
        headers = {
            "X-Tenant-ID": tenant,
            "X-Actor-ID": "admin-a",
            "X-Request-ID": "req-s12",
            "X-Trace-ID": "trace-s12",
        }
        page1 = await client.get("/api/v1/workflows/runs?page=1&page_size=4", headers=headers)
        page2 = await client.get("/api/v1/workflows/runs?page=2&page_size=4", headers=headers)
        other_view = await client.get(
            "/api/v1/workflows/runs", headers={**headers, "X-Tenant-ID": other}
        )

    assert page1.status_code == 200
    body = page1.json()
    assert body["code"] == 0
    assert "request_id" in body
    data = body["data"]
    assert data["page"] == 1
    assert data["page_size"] == 4
    assert data["total"] == 6  # 跨两个工作流的本租户全部 run
    assert len(data["items"]) == 4
    # 跨工作流：items 覆盖 flow-x 与 flow-y
    workflow_ids = {item["workflow_id"] for item in data["items"]}
    assert workflow_ids == {"flow-x", "flow-y"}
    # review P1-5：workflow_version wire 契约为 string（前端 requiredString）
    assert all(
        isinstance(item["workflow_version"], str) for item in data["items"]
    ), "workflow_version 必须以 string 上 wire"

    assert page2.status_code == 200
    data2 = page2.json()["data"]
    assert data2["page"] == 2
    assert len(data2["items"]) == 2
    # 两页无重叠
    ids1 = {item["run_id"] for item in data["items"]}
    ids2 = {item["run_id"] for item in data2["items"]}
    assert not (ids1 & ids2)

    # tenant scope：他租户只见自己的 2 条
    assert other_view.status_code == 200
    other_data = other_view.json()["data"]
    assert other_data["total"] == 2
    other_ids = {item["run_id"] for item in other_data["items"]}
    assert not (other_ids & ids1), "他租户 run 不得混入本租户视图"
