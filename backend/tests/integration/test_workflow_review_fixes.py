"""P0/P1 修复回归（GLM review 2026-08-29）。

真实边界（不 mock worker/DBOS/Registry/投影表）：
- P0-1 subworkflow 独立 run_id：子流程写自己的投影行（`{child}:{parent}:sub:{node}`），
  不复用父 run_id 覆盖父 node_states / 提前 finish——父 run 投影保持完整。
- P0-2 终态接线下沉解释器：失败 run（human_task 超时 ERROR）写 `failed` 投影 +
  释放 active refs（start/recover/serve 三模式统一，serve 不再漏）。
"""

from __future__ import annotations

import asyncio
import time
import uuid

import pytest

from fluxion.registry.sqlalchemy_store import PostgreSQLRegistryStore
from fluxion.resources import ResourceDefinition, ResourceKind, ResourceStatus
from fluxion.runtime.workflow import WorkflowPinnedRef, WorkflowStartRequest
from fluxion.runtime.workflow_dbos import DBOS_QUEUE_NAME, DbosWorkflowEngine, workflow_run_id
from tests.workflow_runtime.worker_fixtures import (
    WorkerProcess,
    WorkflowTestClient,
    install_registry_worker_bootstrap,
    purge_stale_enqueued,
    purge_stale_workflows,
    worker_db_url,
)

REGISTRY_BOOTSTRAP = "tests.workflow_runtime.worker_fixtures:install_registry_worker_bootstrap"
TENANT = "tenant-review"


def _asyncpg_db_url(db_url: str) -> str:
    return db_url.replace("postgresql://", "postgresql+asyncpg://", 1)


async def _fresh_registry_store(db_url: str) -> PostgreSQLRegistryStore:
    store = PostgreSQLRegistryStore(_asyncpg_db_url(db_url), reset_on_initialize=True)
    await store.initialize()
    return store


async def _publish(
    store: PostgreSQLRegistryStore,
    workflow_id: str,
    version: str,
    spec: dict[str, object],
    *,
    tenant_id: str,
) -> None:
    await store.put(
        ResourceDefinition(
            tenant_id=tenant_id,
            kind=ResourceKind.WORKFLOW,
            id=workflow_id,
            version=version,
            status=ResourceStatus.DRAFT,
            spec_json=spec,
        )
    )
    await store.publish(ResourceKind.WORKFLOW, workflow_id, tenant_id=tenant_id, version=version)


def _child_flow_spec() -> dict[str, object]:
    return {
        "name": "child-flow",
        "steps": [
            {
                "id": "child_step",
                "type": "capability",
                "capability_ref": "skill:stamp@1",
                "input": {"seconds": 0.2},
            }
        ],
    }


def _parent_flow_spec() -> dict[str, object]:
    return {
        "name": "parent-flow",
        "steps": [
            {
                "id": "sub",
                "type": "subworkflow",
                "workflow_ref": "workflow:child-flow@1",
                "input": {},
            },
            {
                "id": "done",
                "type": "capability",
                "capability_ref": "skill:stamp@1",
                "depends_on": ["sub"],
                "input": {"seconds": 0.2},
            },
        ],
    }


def _timeout_flow_spec() -> dict[str, object]:
    """review(human_task timeout=1s) 无 signal → recv 超时 → ERROR（P0-2 失败路径）。"""
    return {
        "name": "timeout-flow",
        "steps": [
            {
                "id": "review",
                "type": "human_task",
                "assignee": "user:alice",
                "message": "超时审批",
                "timeout_seconds": 1.0,
            }
        ],
    }


def _quick_flow_spec() -> dict[str, object]:
    """单 echo 节点，毫秒级 SUCCESS（serve 模式成功终态路径）。"""
    return {
        "name": "quick-flow",
        "steps": [
            {
                "id": "echo",
                "type": "capability",
                "capability_ref": "skill:echo@1",
                "input": {"greeting": "{{ input.greeting }}"},
            }
        ],
    }


async def _wait_projection_status(
    store: PostgreSQLRegistryStore,
    *,
    tenant_id: str,
    run_id: str,
    status: str,
    timeout: float = 20.0,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        row = await store.get_workflow_run(tenant_id=tenant_id, run_id=run_id)
        if row is not None and row["status"] == status:
            return
        await asyncio.sleep(0.3)
    raise AssertionError(f"projection {run_id} not {status!r} within {timeout}s")


# ---------------------------------------------------------------------------
# P0-1：subworkflow 派生独立 run_id，父投影不被覆盖
# ---------------------------------------------------------------------------


async def test_review_p01_subworkflow_writes_own_projection() -> None:
    """P0-1[integration]：子流程写独立投影行，父 run 投影保持完整（succeeded）。"""
    db_url = worker_db_url()
    store = await _fresh_registry_store(db_url)
    purge_stale_workflows(db_url)
    client = WorkflowTestClient(db_url)
    worker: WorkerProcess | None = None
    try:
        await _publish(store, "child-flow", "1", _child_flow_spec(), tenant_id=TENANT)
        await _publish(store, "parent-flow", "1", _parent_flow_spec(), tenant_id=TENANT)
        purge_stale_enqueued(db_url, DBOS_QUEUE_NAME)
        execution_id = f"p01-{uuid.uuid4().hex[:8]}"
        run_id = workflow_run_id("parent-flow", execution_id)
        worker = WorkerProcess(
            [
                "start",
                "--workflow-id",
                "parent-flow",
                "--version",
                "1",
                "--execution-id",
                execution_id,
                "--tenant",
                TENANT,
                "--await-timeout",
                "60",
            ],
            extra_env={"DBOS__VMID": "p01"},
            bootstrap=REGISTRY_BOOTSTRAP,
        )
        worker.wait_for("STARTED", timeout=30.0)
        worker.wait_for("RUN_RESULT", timeout=60.0)

        # 父 run 投影完整：succeeded + sub/done 节点
        parent = await store.get_workflow_run(tenant_id=TENANT, run_id=run_id)
        assert parent is not None, "parent projection missing"
        assert parent["status"] == "succeeded"
        node = parent["node_states"]
        assert node["sub"]["status"] == "succeeded", node
        assert node["done"]["status"] == "succeeded", node
        # 子流程节点是 subworkflow → 输出是子流程结果（非空 dict 即子流程已跑）
        assert node["sub"]["output_ref"] is not None

        # 子流程独立投影行：`child-flow:{parent_run_id}:sub:sub`，不与父 run 串行
        child_run_id = f"child-flow:{run_id}:sub:sub"
        child = await store.get_workflow_run(tenant_id=TENANT, run_id=child_run_id)
        assert child is not None, "child projection missing"
        assert child["status"] == "succeeded"
        assert child["node_states"]["child_step"]["status"] == "succeeded"
        # 父 run 无子流程节点串入（子流程不在父 node_states 里出现额外项）
        assert "child_step" not in parent["node_states"]
    finally:
        if worker is not None and worker.proc.poll() is None:
            worker.stop()
        client.close()
        await store.close()


# ---------------------------------------------------------------------------
# P0-2：失败 run 终态接线（failed 投影 + 释放 active refs）
# ---------------------------------------------------------------------------


async def test_review_p02_failed_run_writes_failed_projection_and_releases_refs() -> None:
    """P0-2[integration]：human_task 超时 ERROR → 解释器写 failed 投影 + 释放 refs。

    serve 形态与 start/recover 共用同一终态路径（终态处理下沉解释器，P0-2）。
    """
    db_url = worker_db_url()
    store = await _fresh_registry_store(db_url)
    purge_stale_workflows(db_url)
    client = WorkflowTestClient(db_url)
    worker: WorkerProcess | None = None
    try:
        await _publish(store, "timeout-flow", "1", _timeout_flow_spec(), tenant_id=TENANT)
        purge_stale_enqueued(db_url, DBOS_QUEUE_NAME)
        execution_id = f"p02-{uuid.uuid4().hex[:8]}"
        run_id = workflow_run_id("timeout-flow", execution_id)
        worker = WorkerProcess(
            [
                "start",
                "--workflow-id",
                "timeout-flow",
                "--version",
                "1",
                "--execution-id",
                execution_id,
                "--tenant",
                TENANT,
                "--await-timeout",
                "60",
            ],
            extra_env={"DBOS__VMID": "p02"},
            bootstrap=REGISTRY_BOOTSTRAP,
        )
        worker.wait_for("STARTED", timeout=30.0)
        worker.wait_for("RUN_FAILED", timeout=60.0)

        # 投影 status=failed（P0-2：不再永远 running）
        row = await store.get_workflow_run(tenant_id=TENANT, run_id=run_id)
        assert row is not None, "projection missing"
        assert row["status"] == "failed", row["status"]

        # 终态释放 active refs（解释器 except 路径统一接线，S-07 guard 不被架空）
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            refs = await store.check_active_references(
                tenant_id=TENANT,
                kind=ResourceKind.WORKFLOW,
                resource_id="timeout-flow",
                version="1",
            )
            if not refs:
                break
            await asyncio.sleep(0.2)
        else:
            pytest.fail("active references not released after failed run")
    finally:
        if worker is not None and worker.proc.poll() is None:
            worker.stop()
        client.close()
        await store.close()


# ---------------------------------------------------------------------------
# P0-2：serve（生产 Deployment 形态，design §4.1）终态接线
# ---------------------------------------------------------------------------


async def test_review_p02_serve_mode_terminal_wiring() -> None:
    """P0-2[serve]：serve worker 消费 queue 执行 run，终态由解释器统一接线。

    ERROR run（human_task 超时）→ failed 投影 + 释放 active refs；SUCCESS run →
    succeeded 投影。serve 不再"终态无人写"（run_graph_workflow except/else 路径）。
    """
    db_url = worker_db_url()
    store = await _fresh_registry_store(db_url)
    purge_stale_workflows(db_url)
    client = WorkflowTestClient(db_url)
    worker: WorkerProcess | None = None
    try:
        await _publish(store, "timeout-flow", "1", _timeout_flow_spec(), tenant_id=TENANT)
        await _publish(store, "quick-flow", "1", _quick_flow_spec(), tenant_id=TENANT)
        # 驱动进程装配（engine.start 需要 provider + reference store 用于 acquire）
        install_registry_worker_bootstrap(db_url)

        worker = WorkerProcess(
            ["serve", "--index", "0", "--idle-seconds", "60"],
            extra_env={"DBOS__VMID": "p02-serve"},
            timeout=60.0,
            bootstrap=REGISTRY_BOOTSTRAP,
        )
        worker.wait_for("READY-0", timeout=60.0)

        engine = DbosWorkflowEngine(
            database_url=db_url, listen_queues=[], enqueue_start=True
        )

        async def _enqueue(workflow_id: str, marker: str) -> str:
            eid = f"p02s-{marker}-{uuid.uuid4().hex[:8]}"
            run_id = workflow_run_id(workflow_id, eid)
            await engine.start(
                WorkflowStartRequest(
                    workflow_id=workflow_id,
                    tenant_id=TENANT,
                    user_id="user-a",
                    execution_id=eid,
                    trace_id=f"trace-{eid}",
                    arguments={"greeting": "hi"},
                    pinned=(WorkflowPinnedRef(kind="workflow", id=workflow_id, version="1"),),
                )
            )
            return run_id

        run_ok = await _enqueue("quick-flow", "ok")
        run_err = await _enqueue("timeout-flow", "err")

        # serve worker 消费执行 → 终态投影（failed / succeeded）
        await _wait_projection_status(store, tenant_id=TENANT, run_id=run_ok, status="succeeded")
        await _wait_projection_status(store, tenant_id=TENANT, run_id=run_err, status="failed")

        # 终态释放 active refs（serve 不再泄漏，S-07 guard 不架空）
        for run_id in (run_ok, run_err):
            deadline = time.monotonic() + 15.0
            while time.monotonic() < deadline:
                refs = await store.check_active_references(
                    tenant_id=TENANT,
                    kind=ResourceKind.WORKFLOW,
                    resource_id=run_id.split(":", 1)[0],
                    version="1",
                )
                if not refs:
                    break
                await asyncio.sleep(0.2)
            else:
                pytest.fail(f"serve mode: active references not released for {run_id}")
    finally:
        if worker is not None and worker.proc.poll() is None:
            worker.stop()
        client.close()
        await store.close()
