"""TASK-010（Phase 5）Operations Queues/Workers 后端端点（S-11）。

真实边界：真实 DBOS sysdb（本地 PG `fluxion_workflow` 库）+ 真实 worker 子进程
（`python -m fluxion.cli.workflow_worker`，DBOS__VMID=worker-s11 → executor_id）
+ 真实 HTTP（ASGITransport + 统一 envelope）。

覆盖：queue 注册行（dbos.queues，workers=worker_concurrency）+ ENQUEUED 深度
（workflow_status 计数）；worker 实例视图（executor_id 派生：queues/started_at/
状态）；sysdb 不可达 → 503 envelope。
"""

from __future__ import annotations

import asyncio
import uuid

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from tests.workflow_runtime.worker_fixtures import (
    WorkerProcess,
    install_worker_bootstrap,
    purge_stale_enqueued,
    worker_db_url,
)

from fluxion.api.middleware import RequestContextMiddleware
from fluxion.api.operations import register_operations_routes
from fluxion.config import DevModeSettings
from fluxion.runtime.workflow import (
    WorkflowPinnedRef,
    WorkflowStartRequest,
)
from fluxion.runtime.workflow_dbos import DBOS_QUEUE_NAME, DbosWorkflowEngine
from fluxion.services.operations_app import OperationsApplicationService


def test_s11_operations_queues_workers_endpoints() -> None:
    """S-11[integration]：真实 DBOS sysdb + HTTP 端点返回 queue/worker 状态。"""
    db_url = worker_db_url()
    install_worker_bootstrap(db_url)
    purge_stale_enqueued(db_url, DBOS_QUEUE_NAME)

    worker = WorkerProcess(
        ["serve", "--index", "0", "--idle-seconds", "60"],
        extra_env={"DBOS__VMID": "worker-s11"},
        timeout=60.0,
    )
    try:
        worker.wait_for("READY-0", timeout=60.0)
        engine = DbosWorkflowEngine(
            database_url=db_url, listen_queues=[], enqueue_start=True
        )
        tag = uuid.uuid4().hex[:6]
        # 1) worker 存活时跑一条 → executor_id=worker-s11 出现（worker 视图数据源）
        asyncio.run(_run_workflow(engine, f"s11-{tag}-a"))
        # 2) worker 停止后再 enqueue → 停留 ENQUEUED（depth 证据）
        worker.stop()
        asyncio.run(_enqueue_only(engine, f"s11-{tag}-b"))

        results = asyncio.run(_query_endpoints(db_url))
    finally:
        if worker.proc.poll() is None:
            worker.stop()

    queues_body, workers_body, queues_status, workers_status = results
    assert queues_status == 200
    assert queues_body["code"] == 0
    assert "request_id" in queues_body
    fluxion_queue = next(
        (q for q in queues_body["data"] if q["name"] == DBOS_QUEUE_NAME), None
    )
    assert fluxion_queue is not None, f"缺 fluxion-workflow queue：{queues_body['data']}"
    assert fluxion_queue["depth"] >= 1, "ENQUEUED 深度应 ≥1（worker 停止后 enqueue）"
    assert fluxion_queue["workers"] >= 1, "注册 queue 应带 worker_concurrency"
    assert fluxion_queue["queue_id"]

    assert workers_status == 200
    assert workers_body["code"] == 0
    worker_row = next(
        (w for w in workers_body["data"] if w["worker_id"] == "worker-s11"), None
    )
    assert worker_row is not None, f"缺 worker-s11 视图：{workers_body['data']}"
    assert DBOS_QUEUE_NAME in worker_row["queues"]
    assert worker_row["started_at"] != ""
    assert worker_row["status"] in {"running", "idle", "stopped"}
    assert isinstance(worker_row["running_workflows"], int)


def test_s11_unavailable_sysdb_returns_envelope() -> None:
    """sysdb 不可达 → 503 + 统一 envelope（明确失败，不静默空数据）。"""
    results = asyncio.run(
        _query_endpoints("postgresql://mmuser:mmuser@localhost:59999/none")
    )
    queues_body, _workers_body, queues_status, _ws = results
    assert queues_status == 503
    assert queues_body["code"] == 45_001
    assert queues_body["data"] is None
    assert "不可用" in queues_body["message"]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _run_workflow(engine: DbosWorkflowEngine, execution_id: str) -> str:
    from dbos import DBOS

    request = WorkflowStartRequest(
        workflow_id="quick-flow",
        tenant_id="tenant-s11",
        user_id="user-s11",
        execution_id=execution_id,
        trace_id=f"trace-{execution_id}",
        arguments={"greeting": "ops"},
        pinned=(WorkflowPinnedRef(kind="workflow", id="quick-flow", version="1"),),
    )
    run_id = (await engine.start(request)).run_id
    await asyncio.wait_for(asyncio.to_thread(DBOS.get_result, run_id), timeout=60.0)
    return run_id


async def _enqueue_only(engine: DbosWorkflowEngine, execution_id: str) -> None:
    request = WorkflowStartRequest(
        workflow_id="quick-flow",
        tenant_id="tenant-s11",
        user_id="user-s11",
        execution_id=execution_id,
        trace_id=f"trace-{execution_id}",
        arguments={"greeting": "queued"},
        pinned=(WorkflowPinnedRef(kind="workflow", id="quick-flow", version="1"),),
    )
    await engine.start(request)


async def _query_endpoints(sysdb_dsn: str) -> tuple[dict, dict, int, int]:
    service = OperationsApplicationService(sysdb_dsn)
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware, dev_mode=DevModeSettings(enabled=True))
    register_operations_routes(app, service)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://ops"
        ) as client:
            headers = {
                "X-Tenant-ID": "tenant-s11",
                "X-Actor-ID": "admin-a",
                "X-Request-ID": "req-s11",
                "X-Trace-ID": "trace-s11",
            }
            queues_resp = await client.get("/api/v1/operations/queues", headers=headers)
            workers_resp = await client.get("/api/v1/operations/workers", headers=headers)
        return (
            queues_resp.json(),
            workers_resp.json(),
            queues_resp.status_code,
            workers_resp.status_code,
        )
    finally:
        await service.close()
