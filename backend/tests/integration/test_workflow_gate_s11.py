"""TASK-008 验收（S-11 / E-02 / B-02 / RULE-P3-06）：workflow_run 投影 + status API。

真实边界（不 mock 引擎/存储/worker/DB/Registry/ASGI）：
- S-11[integration]：真实投影表（PG，与 DBOS sysdb 同库）+ 真实解释器分批写投影
  （真实 worker 子进程 + DBOS）+ 真实 ASGI 栈（console app + httpx ASGITransport）。
  终态 run 与运行中 run（human_task 挂起）查询 `GET /api/v1/workflows/runs/{run_id}`
  → node 级状态 + pinned refs + execution history。
- E-02[integration]：tenant B 查 tenant A 的 run → 404 NotFound + 统一 envelope。
- B-02[integration]：tenant A/B 真实双租户数据 → 列表/详情/投影全链路隔离
  （RULE-P3-06 / NFR-SEC-01）。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient

from fluxion.api.console import create_app
from fluxion.errors.console import RESOURCE_NOT_FOUND
from fluxion.registry.sqlalchemy_store import PostgreSQLRegistryStore
from fluxion.resources import ResourceDefinition, ResourceKind, ResourceStatus
from fluxion.runtime.workflow_dbos import DBOS_QUEUE_NAME, DbosWorkflowEngine, workflow_run_id
from fluxion.services.console_app import ConsoleApplicationService
from fluxion.services.workflow_projection import WorkflowProjectionService
from tests.workflow_runtime.worker_fixtures import (
    WorkerProcess,
    WorkflowTestClient,
    purge_stale_enqueued,
    purge_stale_workflows,
    worker_db_url,
)

REGISTRY_BOOTSTRAP = "tests.workflow_runtime.worker_fixtures:install_registry_worker_bootstrap"
PIN_FLOW_ID = "pin-flow"
TENANT_A = "tenant-s11-a"
TENANT_B = "tenant-s11-b"


def _pin_flow_spec(marker: str) -> dict[str, object]:
    """pin-flow：prepare(stamp) → review(human_task 挂起) → finalize(stamp)。"""
    return {
        "name": "pin-flow",
        "steps": [
            {
                "id": "prepare",
                "type": "capability",
                "capability_ref": "skill:stamp@1",
                "input": {"seconds": 0.2, "marker": marker},
            },
            {
                "id": "review",
                "type": "human_task",
                "depends_on": ["prepare"],
                "assignee": "user:alice",
                "message": "审批",
            },
            {
                "id": "finalize",
                "type": "capability",
                "depends_on": ["review"],
                "capability_ref": "skill:stamp@1",
                "input": {"seconds": 0.2},
            },
        ],
    }


def _asyncpg_db_url(db_url: str) -> str:
    return db_url.replace("postgresql://", "postgresql+asyncpg://", 1)


async def _fresh_registry_store(db_url: str) -> PostgreSQLRegistryStore:
    """真实 PG + fluxion metadata 重建（含 workflow_run 投影表，drop_all+create_all）。"""
    store = PostgreSQLRegistryStore(_asyncpg_db_url(db_url), reset_on_initialize=True)
    await store.initialize()
    return store


async def _publish_pin_flow(store: PostgreSQLRegistryStore, *, tenant_id: str) -> None:
    for version, marker in (("1", "v1"), ("2", "v2")):
        await store.put(
            ResourceDefinition(
                tenant_id=tenant_id,
                kind=ResourceKind.WORKFLOW,
                id=PIN_FLOW_ID,
                version=version,
                status=ResourceStatus.DRAFT,
                spec_json=_pin_flow_spec(marker),
            )
        )
        await store.publish(
            ResourceKind.WORKFLOW, PIN_FLOW_ID, tenant_id=tenant_id, version=version
        )


def _wait_durable_wait_checkpoint(db_url: str, run_id: str, *, timeout: float) -> None:
    """等 `dbos.operation_outputs` 出现 `DBOS.sleep` 行（human_task 挂起已 durable）。

    PENDING 是 DBOS 初始/在飞状态、非挂起检查点；`DBOS.sleep` 行落库 ⇒ prepare
    step 已 durable 提交、workflow 阻塞在 recv（`dbos-pending-not-a-checkpoint`）。
    """
    import psycopg

    deadline = time.monotonic() + timeout
    with psycopg.connect(db_url, autocommit=True) as conn:
        while time.monotonic() < deadline:
            row = conn.execute(
                "SELECT 1 FROM dbos.operation_outputs "
                "WHERE workflow_uuid = %s AND function_name = 'DBOS.sleep' LIMIT 1",
                (run_id,),
            ).fetchone()
            if row:
                return
            time.sleep(0.2)
    raise AssertionError(
        f"workflow {run_id} did not reach a durable sleep checkpoint within {timeout}s"
    )


async def _start_blocked_pin_flow(
    *,
    tenant_id: str,
    vmid: str,
    client: WorkflowTestClient,
) -> tuple[WorkerProcess, str]:
    """真实 worker start 一个 pin-flow 并阻塞在 review（返回 worker + run_id）。"""
    purge_stale_enqueued(worker_db_url(), DBOS_QUEUE_NAME)
    execution_id = f"{vmid}-{uuid.uuid4().hex[:8]}"
    run_id = workflow_run_id(PIN_FLOW_ID, execution_id)
    worker = WorkerProcess(
        [
            "start",
            "--workflow-id",
            PIN_FLOW_ID,
            "--version",
            "1",
            "--execution-id",
            execution_id,
            "--tenant",
            tenant_id,
            "--await-timeout",
            "180",
        ],
        extra_env={"DBOS__VMID": vmid},
        bootstrap=REGISTRY_BOOTSTRAP,
    )
    worker.wait_for("STARTED", timeout=30.0)
    _wait_durable_wait_checkpoint(worker_db_url(), run_id, timeout=20.0)
    return worker, run_id


@asynccontextmanager
async def _api_stack(
    store: PostgreSQLRegistryStore,
    *,
    db_url: str,
) -> AsyncIterator[AsyncClient]:
    """真实 ASGI 栈：console app + 投影服务（engine 注入 service 读 history，DBOSClient 免 launch）。"""
    service = ConsoleApplicationService(store)
    engine = DbosWorkflowEngine(database_url=db_url, auto_launch=False)
    projection_service = WorkflowProjectionService(store, workflow_engine=engine)
    app = create_app(service, projection_service=projection_service)
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    try:
        yield client
    finally:
        await client.aclose()


def _tenant_headers(tenant_id: str) -> dict[str, str]:
    return {"X-Tenant-ID": tenant_id, "X-Actor-ID": "tester"}


def _complete_and_stop(
    worker: WorkerProcess,
    client: WorkflowTestClient,
    run_id: str,
    *,
    signal: str,
) -> None:
    try:
        client.signal(run_id, signal, {"approved": True})
        worker.wait_for("RUN_RESULT", timeout=60.0)
    finally:
        if worker.proc.poll() is None:
            worker.stop()


# ---------------------------------------------------------------------------
# S-11：运行中/终态 run 查询 → node 级状态 + pinned refs + execution history
# ---------------------------------------------------------------------------


async def test_workflow_gate_s11_get_run_returns_projection() -> None:
    """S-11[integration]：`GET /api/v1/workflows/runs/{run_id}` 返回投影全量。

    真实边界：真实投影表 + 真实 worker（终态 run + 运行中 run）+ 真实 ASGI 栈。
    断言：终态 run status=succeeded、node_states 全节点 succeeded、pinned_refs 含
    pin-flow@1、execution_history 有 steps；运行中 run status=running、node_states
    已有 prepare（增量投影）；列表含两个 run。
    """
    db_url = worker_db_url()
    store = await _fresh_registry_store(db_url)
    purge_stale_workflows(db_url)  # 清残留 PENDING/ENQUEUED，防 recovery 误写投影
    client = WorkflowTestClient(db_url)
    workers: list[WorkerProcess] = []
    try:
        await _publish_pin_flow(store, tenant_id=TENANT_A)

        # 终态 run
        worker_ok, run_ok = await _start_blocked_pin_flow(
            tenant_id=TENANT_A, vmid="s11-ok", client=client
        )
        workers.append(worker_ok)
        client.signal(run_ok, "review", {"approved": True})
        worker_ok.wait_for("RUN_RESULT", timeout=60.0)

        # 运行中 run（阻塞在 review）
        worker_busy, run_busy = await _start_blocked_pin_flow(
            tenant_id=TENANT_A, vmid="s11-busy", client=client
        )
        workers.append(worker_busy)

        async with _api_stack(store, db_url=db_url) as api:
            # 终态 run
            ok = await api.get(
                f"/api/v1/workflows/runs/{run_ok}", headers=_tenant_headers(TENANT_A)
            )
            payload_ok = ok.json()
            assert ok.status_code == 200, payload_ok
            assert payload_ok["code"] == 0, payload_ok
            data_ok = payload_ok["data"]
            assert data_ok["run_id"] == run_ok
            assert data_ok["status"] == "succeeded"
            assert data_ok["workflow_id"] == PIN_FLOW_ID
            # review P1-5：wire 契约 string（前端 requiredString）
            assert data_ok["workflow_version"] == "1"
            assert data_ok["pinned_refs"][0] == {
                "kind": "workflow",
                "id": PIN_FLOW_ID,
                "version": "1",
            }
            node_ok = data_ok["node_states"]
            assert {node_ok[n]["status"] for n in ("prepare", "review", "finalize")} == {
                "succeeded"
            }
            history_ok = data_ok["execution_history"]
            assert history_ok["run_id"] == run_ok
            assert history_ok["status"] == "succeeded"
            # execution history 是 DBOS step 记录（function_name + output + error）；
            # 节点 ID 级状态由 node_states 承载（prepare/review/finalize 已断言）。
            assert history_ok["steps"], history_ok
            assert all(s["status"] == "succeeded" for s in history_ok["steps"]), history_ok
            assert any("_run_node" in s["node_id"] for s in history_ok["steps"]), history_ok
            assert data_ok["created_at"] and data_ok["updated_at"]

            # 运行中 run：status=running + 增量 node_states（prepare 已落库）
            busy = await api.get(
                f"/api/v1/workflows/runs/{run_busy}", headers=_tenant_headers(TENANT_A)
            )
            payload_busy = busy.json()
            assert busy.status_code == 200, payload_busy
            assert payload_busy["data"]["status"] == "running"
            assert payload_busy["data"]["node_states"]["prepare"]["status"] == "succeeded"
            assert "execution_history" in payload_busy["data"]

            # 列表含两个 run
            listing = await api.get(
                f"/api/v1/workflows/{PIN_FLOW_ID}/runs?page=1&page_size=20",
                headers=_tenant_headers(TENANT_A),
            )
            listed = listing.json()
            assert listed["code"] == 0, listed
            assert listed["data"]["total"] == 2
            assert {item["run_id"] for item in listed["data"]["items"]} == {run_ok, run_busy}
    finally:
        # 运行中 run 先 signal 到终态再停 worker：不留 PENDING（防后续 recovery 误写投影）
        try:
            client.signal(run_busy, "review", {"approved": True})
            worker_busy.wait_for("RUN_RESULT", timeout=60.0)
        except Exception:  # noqa: BLE001 — 清理路径不掩盖主断言
            pass
        for worker in workers:
            if worker.proc.poll() is None:
                worker.stop()
        client.close()
        await store.close()


# ---------------------------------------------------------------------------
# E-02：查询他租户 run → 404 NotFound + 统一 envelope（不可见）
# ---------------------------------------------------------------------------


async def test_workflow_gate_e02_cross_tenant_run_not_found() -> None:
    """E-02[integration]：tenant B 查 tenant A 的 run → 404 + 统一 envelope。"""
    db_url = worker_db_url()
    store = await _fresh_registry_store(db_url)
    purge_stale_workflows(db_url)
    client = WorkflowTestClient(db_url)
    worker: WorkerProcess | None = None
    try:
        await _publish_pin_flow(store, tenant_id=TENANT_A)
        worker, run_id = await _start_blocked_pin_flow(
            tenant_id=TENANT_A, vmid="s11-e02", client=client
        )
        async with _api_stack(store, db_url=db_url) as api:
            response = await api.get(
                f"/api/v1/workflows/runs/{run_id}", headers=_tenant_headers(TENANT_B)
            )
        payload = response.json()
        assert response.status_code == 404, payload
        assert payload["code"] == RESOURCE_NOT_FOUND, payload
        assert payload["message"]
        assert payload["request_id"]
        assert payload["data"] is None
    finally:
        if worker is not None:
            _complete_and_stop(worker, client, run_id, signal="review")
        client.close()
        await store.close()


# ---------------------------------------------------------------------------
# B-02：tenant A/B 真实双租户数据 → 列表/详情/投影全链路隔离（RULE-P3-06）
# ---------------------------------------------------------------------------


async def test_workflow_gate_b02_tenant_scope_isolated() -> None:
    """B-02[integration]：双租户真实数据，列表/详情全链路隔离（NFR-SEC-01）。"""
    db_url = worker_db_url()
    store = await _fresh_registry_store(db_url)
    purge_stale_workflows(db_url)
    client = WorkflowTestClient(db_url)
    workers: list[WorkerProcess] = []
    try:
        await _publish_pin_flow(store, tenant_id=TENANT_A)
        await _publish_pin_flow(store, tenant_id=TENANT_B)
        worker_a, run_a = await _start_blocked_pin_flow(
            tenant_id=TENANT_A, vmid="s11-b02a", client=client
        )
        workers.append(worker_a)
        worker_b, run_b = await _start_blocked_pin_flow(
            tenant_id=TENANT_B, vmid="s11-b02b", client=client
        )
        workers.append(worker_b)

        async with _api_stack(store, db_url=db_url) as api:
            # 列表隔离：A 只见 A，B 只见 B
            list_a = (await api.get(
                f"/api/v1/workflows/{PIN_FLOW_ID}/runs?page=1&page_size=20",
                headers=_tenant_headers(TENANT_A),
            )).json()
            list_b = (await api.get(
                f"/api/v1/workflows/{PIN_FLOW_ID}/runs?page=1&page_size=20",
                headers=_tenant_headers(TENANT_B),
            )).json()
            assert {i["run_id"] for i in list_a["data"]["items"]} == {run_a}
            assert {i["run_id"] for i in list_b["data"]["items"]} == {run_b}

            # 详情隔离：跨租户详情 404，本租户 200
            a_see_b = await api.get(
                f"/api/v1/workflows/runs/{run_b}", headers=_tenant_headers(TENANT_A)
            )
            b_see_a = await api.get(
                f"/api/v1/workflows/runs/{run_a}", headers=_tenant_headers(TENANT_B)
            )
            assert a_see_b.status_code == 404 and a_see_b.json()["code"] == RESOURCE_NOT_FOUND
            assert b_see_a.status_code == 404 and b_see_a.json()["code"] == RESOURCE_NOT_FOUND
            own = await api.get(
                f"/api/v1/workflows/runs/{run_a}", headers=_tenant_headers(TENANT_A)
            )
            assert own.status_code == 200 and own.json()["data"]["status"] == "running"
    finally:
        # 双 run 都 signal 到终态再停 worker：不留 PENDING（防后续 recovery 误写投影）
        for run_id, worker in ((run_a, worker_a), (run_b, worker_b)):
            try:
                client.signal(run_id, "review", {"approved": True})
                worker.wait_for("RUN_RESULT", timeout=60.0)
            except Exception:  # noqa: BLE001 — 清理路径不掩盖主断言
                pass
        for worker in workers:
            if worker.proc.poll() is None:
                worker.stop()
        client.close()
        await store.close()
