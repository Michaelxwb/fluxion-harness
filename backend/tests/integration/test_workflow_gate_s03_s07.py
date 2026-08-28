"""TASK-007 验收（S-03 / S-07 / RULE-P3-02 / RULE-P3-03）：Version pin / active ref / GC。

真实边界（不 mock 引擎/存储/worker/DB/Registry）：
- S-03[integration]：真实 Registry（PG `fluxion_workflow` 库 + ADR-SNAPSHOT-001
  `active_references`）+ 真实 worker 子进程 + DBOS。pin-flow v1（marker v1）与 v2
  （marker v2）均已发布；start 时 pinned v1 → worker 的 Registry-backed provider
  （`store.recall_pinned`，不 resolve latest）把解析坐标打印 `PROVIDER_RESOLVE
  pin-flow 1`。长 workflow（prepare → human_task 挂起）kill + recover → startup
  recovery 用持久化的 v1 定义续跑（RULE-P3-02），signal 唤醒继续到 finalize。
  断言：provider 只解析 v1（无 v2/latest）；recover 进程不再调用 provider（定义是
  持久化 DBOS arg）；业务 marker 为 v1；prepare 不重跑；active ref 在 run 期间
  持有、terminal 后释放。
- S-07[E2E]：运行中 workflow 引用 v1 → tombstone v1 + hard-delete v1 被拒
  （`active_reference_blocked`，RISK-P3-03 / RULE-P3-03）；signal 完成 run 后
  worker terminal 释放 → active refs 无残留 → hard-delete v1 成功。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import timedelta

import psycopg
import pytest

from fluxion.registry.sqlalchemy_store import PostgreSQLRegistryStore
from fluxion.registry.store import (
    PublicationCommand,
    PublicationOperation,
    RegistryStoreError,
)
from fluxion.resources import ResourceDefinition, ResourceKind, ResourceStatus
from fluxion.runtime.workflow_dbos import DBOS_QUEUE_NAME, workflow_run_id
from tests.workflow_runtime.worker_fixtures import (
    WorkerProcess,
    WorkflowTestClient,
    list_records,
    purge_stale_enqueued,
    worker_db_url,
)

REGISTRY_BOOTSTRAP = "tests.workflow_runtime.worker_fixtures:install_registry_worker_bootstrap"
PIN_FLOW_ID = "pin-flow"


def _pin_flow_spec(marker: str) -> dict[str, object]:
    """pin-flow：prepare(stamp) → review(human_task 挂起) → finalize(stamp)。

    v1/v2 仅在 prepare 的 input 里 marker 不同——若 resume 错误 resolve latest(v2)，
    provider 坐标或业务 payload 会暴露 'marker': 'v2'。
    """
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
                "message": "版本 pin 审批",
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
    """registry store 用 asyncpg 驱动（`postgresql://` 默认 psycopg2，非本项目依赖）。"""
    return db_url.replace("postgresql://", "postgresql+asyncpg://", 1)


async def _fresh_registry_store(db_url: str) -> PostgreSQLRegistryStore:
    """真实 PG + fluxion metadata 重建（drop_all+create_all，不动 dbos.* schema）。"""
    store = PostgreSQLRegistryStore(_asyncpg_db_url(db_url), reset_on_initialize=True)
    await store.initialize()
    return store


async def _publish_pin_flow(store: PostgreSQLRegistryStore, *, tenant_id: str) -> None:
    """真实 publish 路径发布 v1（marker v1）+ v2（marker v2）。"""
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


async def _tombstone_v1(store: PostgreSQLRegistryStore, *, tenant_id: str) -> None:
    """TASK-007：TOMBSTONE 必须 approval_id（publish_sqlalchemy 高影响操作强制）。"""
    await store.commit_publication(
        PublicationCommand(
            publish_id=f"ts-{uuid.uuid4().hex[:8]}",
            event_id=f"evt-ts-{uuid.uuid4().hex[:8]}",
            tenant_id=tenant_id,
            kind=ResourceKind.WORKFLOW,
            resource_id=PIN_FLOW_ID,
            version="1",
            operation=PublicationOperation.TOMBSTONE,
            actor_id="tester",
            request_id=f"req-{uuid.uuid4().hex[:8]}",
            trace_id=f"trace-{uuid.uuid4().hex[:8]}",
            approval_id=f"approve-{uuid.uuid4().hex[:8]}",
        )
    )


async def _check_run_refs(
    store: PostgreSQLRegistryStore, *, tenant_id: str
) -> list[object]:
    return await store.check_active_references(
        tenant_id=tenant_id,
        kind=ResourceKind.WORKFLOW,
        resource_id=PIN_FLOW_ID,
        version="1",
    )


async def _wait_active_ref(
    store: PostgreSQLRegistryStore, *, tenant_id: str, run_id: str, timeout: float = 15.0
) -> None:
    """等 worker acquire 的 workflow 引用（ref_type=workflow, ref_id=run_id）出现。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        refs = await _check_run_refs(store, tenant_id=tenant_id)
        if any(r.ref_type == "workflow" and r.ref_id == run_id for r in refs):
            return
        await asyncio.sleep(0.2)
    raise AssertionError(f"active reference for run {run_id} not acquired within {timeout}s")


async def _wait_refs_empty(
    store: PostgreSQLRegistryStore, *, tenant_id: str, timeout: float = 15.0
) -> None:
    """等 terminal 释放后 active_references 无残留行（GC 正确性断言）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not await _check_run_refs(store, tenant_id=tenant_id):
            return
        await asyncio.sleep(0.2)
    raise AssertionError(f"active references not released within {timeout}s")


def _wait_durable_wait_checkpoint(db_url: str, run_id: str, *, timeout: float) -> None:
    """等 `dbos.operation_outputs` 出现 `DBOS.sleep` 行（human_task 挂起已 durable）。

    PENDING 是 DBOS 初始/在飞状态、非挂起检查点（`dbos-pending-not-a-checkpoint`
    memory）；`DBOS.sleep` 行落库 ⇒ prepare step 已 durable 提交、workflow 阻塞在
    recv，kill 后 recovery 从 memo 续跑。
    """
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


# ---------------------------------------------------------------------------
# S-03：start pinned v1、v2 已发布 → resume 长 workflow 使用 v1，不 resolve latest
# ---------------------------------------------------------------------------


async def test_workflow_gate_s03_resume_uses_pinned_version() -> None:
    """S-03[integration]：resume 始终使用 pinned v1（RULE-P3-02），不 resolve latest。

    真实边界：真实 Registry（PG）+ active_references + Registry-backed worker provider
    （`store.recall_pinned`）+ 独立 worker 子进程（start/recover）+ DBOS + PG。
    断言：provider 解析坐标恰为 pin-flow@1（无 v2/latest）；kill+recover 后 resume 不
    再调用 provider（定义是持久化 DBOS arg）；业务 marker 为 v1；prepare 不重跑；
    run 期间持有 active ref、terminal 后释放（无残留）。
    """
    db_url = worker_db_url()
    store = await _fresh_registry_store(db_url)
    try:
        await _publish_pin_flow(store, tenant_id="tenant-s03")
        purge_stale_enqueued(db_url, DBOS_QUEUE_NAME)
        client = WorkflowTestClient(db_url)
        eid = f"s03-{uuid.uuid4().hex[:8]}"
        run_id = workflow_run_id(PIN_FLOW_ID, eid)

        worker = WorkerProcess(
            [
                "start",
                "--workflow-id",
                PIN_FLOW_ID,
                "--version",
                "1",
                "--execution-id",
                eid,
                "--tenant",
                "tenant-s03",
                "--await-timeout",
                "180",
            ],
            extra_env={"DBOS__VMID": "s03-worker"},
            bootstrap=REGISTRY_BOOTSTRAP,
        )
        try:
            worker.wait_for("STARTED", timeout=30.0)
            # provider 解析坐标：pinned v1，绝不 resolve latest/v2（RULE-P3-02）
            resolves = [ln for ln in worker.lines if ln.startswith("PROVIDER_RESOLVE")]
            assert resolves == ["PROVIDER_RESOLVE pin-flow 1"], resolves
            # run 期间 active ref 被 acquire（ref_type=workflow, ref_id=run_id）
            await _wait_active_ref(store, tenant_id="tenant-s03", run_id=run_id, timeout=15.0)
            # durable 挂起检查点（prepare 已 checkpoint、阻塞在 recv）后才可 kill
            _wait_durable_wait_checkpoint(db_url, run_id, timeout=20.0)
            worker.kill()
        finally:
            if worker.proc.poll() is None:
                worker.kill()

        recovery = WorkerProcess(
            ["recover", "--run-id", run_id, "--tenant", "tenant-s03", "--timeout", "60"],
            extra_env={"DBOS__VMID": "s03-worker"},
            bootstrap=REGISTRY_BOOTSTRAP,
        )
        try:
            time.sleep(3.0)  # 等 launch + startup recovery 重建 recv 挂起后发 signal
            client.signal(run_id, "review", {"approved": True})
            recovery.wait_for("COMPLETED", timeout=60.0)
        finally:
            if recovery.proc.poll() is None:
                recovery.kill()
        client.close()

        # resume 不 resolve latest：recover 进程无 provider 调用（定义是持久化 arg）
        assert not any("PROVIDER_RESOLVE" in ln for ln in recovery.lines), recovery.output
        # terminal 后 active ref 释放（GC 正确性，无残留行）
        await _wait_refs_empty(store, tenant_id="tenant-s03", timeout=15.0)

        # 业务 marker 为 v1（pinned 版本生效）；长 workflow 续跑到 finalize；prepare 不重跑
        records = {r["node_id"]: r for r in list_records(db_url, run_id)}
        assert "'marker': 'v1'" in records["prepare"]["payload"], records["prepare"]
        assert records["prepare"]["executions"] == 1, records["prepare"]
        assert records["finalize"]["executions"] == 1, records["finalize"]
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# S-07：运行中 workflow 引用 v1 → hard-delete 被拒；terminal 释放后可删
# ---------------------------------------------------------------------------


async def test_workflow_gate_s07_hard_delete_rejected_while_running() -> None:
    """S-07[E2E]：被 active workflow 引用的版本不得 hard delete（RULE-P3-03）。

    真实边界：真实 hard-delete API（三重 guard）+ 真实 active_references
    （ref_type=workflow）+ 运行中 worker 子进程 + tombstone（approval_id 强制）。
    断言：运行中 tombstone v1 + hard-delete v1 → `active_reference_blocked`；
    signal 完成 run 后 worker terminal 释放 → refs 无残留；再 hard-delete v1 成功。
    """
    db_url = worker_db_url()
    store = await _fresh_registry_store(db_url)
    try:
        await _publish_pin_flow(store, tenant_id="tenant-s07")
        purge_stale_enqueued(db_url, DBOS_QUEUE_NAME)
        client = WorkflowTestClient(db_url)
        eid = f"s07-{uuid.uuid4().hex[:8]}"
        run_id = workflow_run_id(PIN_FLOW_ID, eid)

        worker = WorkerProcess(
            [
                "start",
                "--workflow-id",
                PIN_FLOW_ID,
                "--version",
                "1",
                "--execution-id",
                eid,
                "--tenant",
                "tenant-s07",
                "--await-timeout",
                "180",
            ],
            extra_env={"DBOS__VMID": "s07-worker"},
            bootstrap=REGISTRY_BOOTSTRAP,
        )
        try:
            worker.wait_for("STARTED", timeout=30.0)
            await _wait_active_ref(store, tenant_id="tenant-s07", run_id=run_id, timeout=15.0)
            _wait_durable_wait_checkpoint(db_url, run_id, timeout=20.0)

            # RISK-P3-03：pinned v1 被 active workflow 引用 → tombstone 后 hard-delete 被拒
            await _tombstone_v1(store, tenant_id="tenant-s07")
            with pytest.raises(RegistryStoreError) as excinfo:
                await store.hard_delete(
                    ResourceKind.WORKFLOW,
                    PIN_FLOW_ID,
                    tenant_id="tenant-s07",
                    version="1",
                    approval_id="hd-s07",
                    retention_period=timedelta(0),
                )
            assert "active_reference_blocked" in str(excinfo.value), excinfo.value

            # signal → run 完成 → worker terminal 释放 active refs（RUN_RESULT 在释放后打印）
            client.signal(run_id, "review", {"approved": True})
            worker.wait_for("RUN_RESULT", timeout=60.0)
        finally:
            if worker.proc.poll() is None:
                worker.stop()
        client.close()

        # terminal 释放后 active_references 无残留行（GC 正确性）
        await _wait_refs_empty(store, tenant_id="tenant-s07", timeout=15.0)

        # run 结束后 hard-delete v1 成功（ref 已释放）
        result = await store.hard_delete(
            ResourceKind.WORKFLOW,
            PIN_FLOW_ID,
            tenant_id="tenant-s07",
            version="1",
            approval_id="hd-s07",
            retention_period=timedelta(0),
        )
        assert result.kind == ResourceKind.WORKFLOW and result.version == "1"
        assert result.revision >= 1
    finally:
        await store.close()
