"""TASK-005 验收（S-01 / S-02 / S-05 / S-06 / RULE-P3-05）：worker 部署 + durable start / crash recovery E2E。

真实边界（不 mock 引擎/存储/worker/DB）：
- S-01/S-05：真实 `WorkflowAdapter` → `ResilientWorkflowEngine` → `DbosWorkflowEngine`
  → DBOS 2.31 → 本地 PostgreSQL（`fluxion_workflow` 库，含 DBOS sys schema）；
- S-02：真实独立 worker 子进程（`python -m fluxion.cli.workflow_worker`）+ SIGKILL
  + 新进程 `launch()` startup recovery 续跑；已完成 step 不重跑（业务表 executions）；
- S-05：真实 SetWorkflowID 幂等（同 execution 二次 start 返回既有 run、step 不重跑、
  业务记录恰 1 行，SLO-WF-03）；
- S-06：2 个真实 worker 子进程 + database-backed queue（executor_id 显示双 worker 分摊）。

计时断言：SLO-WF-01（durable start P95≤1s）/ SLO-WF-02（recovery P95≤60s）。
"""

from __future__ import annotations

import asyncio
import time
import uuid

from dbos import DBOS
from tests.workflow_runtime.worker_fixtures import (
    WorkerProcess,
    install_worker_bootstrap,
    list_records,
    purge_stale_enqueued,
    wait_for_records,
    worker_db_url,
)

from fluxion.runtime.workflow import (
    ResilientWorkflowEngine,
    WorkflowAdapter,
    WorkflowPinnedRef,
    WorkflowStartRequest,
)
from fluxion.runtime.workflow_dbos import (
    DBOS_QUEUE_NAME,
    DbosWorkflowEngine,
    workflow_run_id,
)


def _db_url() -> str:
    return worker_db_url()


# ---------------------------------------------------------------------------
# S-01：durable start 同步持久化，P95≤1s
# ---------------------------------------------------------------------------


async def test_workflow_gate_s01_durable_start_p95() -> None:
    """S-01[E2E]：`WorkflowAdapter.execute` → 真实 DbosWorkflowEngine → DBOS → PG。

    断言：返回 run_id；start 返回后 DBOS 可查状态（同步持久化）；连续 start 计时
    P95≤1s（SLO-WF-01）；run_id 由 execution_id 确定性派生 + 业务记录 tenant 关联
    （RULE-backend-logging-001 全链路关联）。
    """
    from tests.runtime_helpers import runtime_context

    db_url = _db_url()
    install_worker_bootstrap(db_url)  # 驱动进程装配 provider + executor（Registry 读路径）
    engine = DbosWorkflowEngine(database_url=db_url, listen_queues=[])
    resilient = ResilientWorkflowEngine(delegate=engine)
    adapter = WorkflowAdapter(workflow_id="quick-flow", version="1", engine=resilient)

    latencies_ms: list[float] = []
    run_ids: list[str] = []
    for _ in range(8):
        context, _runtime = await runtime_context()
        started = time.monotonic()
        result = await adapter.execute(context, {"greeting": "hi"})
        latencies_ms.append((time.monotonic() - started) * 1000)
        run_ids.append(result.run_id)
        # durable：start 返回后 DBOS 可查状态（不丢）
        status = await engine.get_status(result.run_id)
        assert status.status in {"PENDING", "SUCCESS", "ERROR"}, status
        # execution_id → run_id 确定性关联（全链路可追踪）
        assert result.run_id == workflow_run_id("quick-flow", context.snapshot.execution_id)
        # 等完成（防堆积）+ 业务记录 tenant 关联
        await engine.await_result(result.run_id, timeout=30.0)
        rows = [
            r for r in list_records(db_url, result.run_id) if r["node_id"] == "echo"
        ]
        assert len(rows) == 1, rows
        assert rows[0]["node_id"] == "echo"

    p95 = sorted(latencies_ms)[max(0, int(0.95 * len(latencies_ms)) - 1)]
    assert p95 <= 1000.0, f"SLO-WF-01: durable start P95={p95:.1f}ms > 1s; latencies={latencies_ms}"
    assert len(run_ids) == 8


# ---------------------------------------------------------------------------
# S-02：SIGKILL worker → 新进程 startup recovery 续跑，P95≤60s
# ---------------------------------------------------------------------------


def test_workflow_gate_s02_crash_recovery() -> None:
    """S-02[E2E]：真实 SIGKILL 独立 worker 进程 → 新进程 startup recovery。

    断言：recovery P95≤60s（SLO-WF-02）；step_a/step_b（kill 前已 durable 提交）
    不重跑（executions==1）；workflow 恢复至 SUCCESS。
    """
    db_url = _db_url()
    install_worker_bootstrap(db_url)  # 驱动进程：provider（供断言侧解析 run_id）
    purge_stale_enqueued(db_url, DBOS_QUEUE_NAME)  # 清残留 ENQUEUED，防本 worker（listen_queues=None）误认领
    eid = f"s02-{uuid.uuid4().hex[:8]}"
    worker = WorkerProcess(
        [
            "start",
            "--workflow-id",
            "crash-flow",
            "--version",
            "1",
            "--execution-id",
            eid,
            "--tenant",
            "tenant-s02",
            "--await-timeout",
            "180",
        ]
    )
    run_id = f"crash-flow:{eid}"
    try:
        worker.wait_for("STARTED", timeout=30.0)
        # 等 step_a + step_b durable 提交、step_c 已启动（step_c 开始 ⇒ a/b 输出已进 scope）
        wait_for_records(
            db_url,
            run_id,
            lambda r: (
                r.get("step_a", {}).get("finished_at") is not None
                and r.get("step_b", {}).get("finished_at") is not None
                and "step_c" in r
            ),
            timeout=30.0,
            description="step_a/step_b committed + step_c started",
        )
        worker.kill()
    finally:
        if worker.proc.poll() is None:
            worker.kill()

    killed_at = time.monotonic()
    recovery = WorkerProcess(
        ["recover", "--run-id", run_id, "--timeout", "60"]
    )
    try:
        recovery.wait_for("COMPLETED", timeout=70.0)
    finally:
        if recovery.proc.poll() is None:
            recovery.kill()
    recovery_elapsed = time.monotonic() - killed_at

    assert recovery_elapsed <= 60.0, f"SLO-WF-02: recovery {recovery_elapsed:.1f}s > 60s"
    records = {r["node_id"]: r for r in list_records(db_url, run_id)}
    assert records["step_a"]["executions"] == 1, "已完成 step_a 不重跑（DBOS 断点续跑）"
    assert records["step_b"]["executions"] == 1, "已完成 step_b 不重跑"
    assert records["step_c"]["finished_at"] is not None, "step_c 恢复后必须续跑完成"


# ---------------------------------------------------------------------------
# S-05：同 execution 二次 start 幂等（SetWorkflowID）
# ---------------------------------------------------------------------------


async def test_workflow_gate_s05_same_execution_second_start_idempotent() -> None:
    """S-05[E2E]：同 execution 二次 start → 返回既有 run、step 不重跑、业务记录恰 1 条。

    断言：二次 start 返回相同 run_id（SetWorkflowID 幂等）；业务记录恰 1 行且
    executions 仍为 1（SLO-WF-03：committed 不可逆副作用重复=0）。
    """
    from tests.runtime_helpers import runtime_context

    db_url = _db_url()
    install_worker_bootstrap(db_url)
    engine = DbosWorkflowEngine(database_url=db_url, listen_queues=[])
    adapter = WorkflowAdapter(
        workflow_id="quick-flow",
        version="1",
        engine=ResilientWorkflowEngine(delegate=engine),
    )

    context, _runtime = await runtime_context()  # 固定 execution_id
    first = await adapter.execute(context, {"greeting": "hi"})
    await engine.await_result(first.run_id, timeout=30.0)

    second = await adapter.execute(context, {"greeting": "hi"})
    await engine.await_result(second.run_id, timeout=30.0)

    assert second.run_id == first.run_id, "同 execution 二次 start 必须返回既有 run"
    status = await engine.get_status(first.run_id)
    assert status.status == "SUCCESS", status
    records = list_records(db_url, first.run_id)
    assert len(records) == 1, f"业务记录必须恰 1 条（SLO-WF-03），实测 {len(records)} 条"
    assert records[0]["executions"] == 1, "step 不重跑：executions 必须仍为 1"


# ---------------------------------------------------------------------------
# S-06：database-backed queue + 2 worker 进程分摊
# ---------------------------------------------------------------------------


def test_workflow_gate_s06_database_queue_two_workers() -> None:
    """S-06[integration]：2 个真实 worker 子进程经 database-backed queue 分摊任务。

    断言：全部 8 任务 SUCCESS；executor_id 含 worker-0 与 worker-1（第 2 个 worker
    生效，水平扩展）；分两批 enqueue（首批 slow step 4s 在飞时第二批仍 ENQUEUED，
    worker-0 并发已满 ⇒ worker-1 认领）使分摊确定。
    """
    db_url = _db_url()
    install_worker_bootstrap(db_url)
    purge_stale_enqueued(db_url, DBOS_QUEUE_NAME)

    workers: list[WorkerProcess] = []
    try:
        workers.append(
            WorkerProcess(
                ["serve", "--index", "0", "--idle-seconds", "300"],
                extra_env={"DBOS__VMID": "worker-0"},
                timeout=60.0,
            )
        )
        workers[0].wait_for("READY-0", timeout=60.0)
        # 错峰启动 worker-1：首批在飞后才 READY，不会与 worker-0 同瞬间争抢
        time.sleep(1.5)
        workers.append(
            WorkerProcess(
                ["serve", "--index", "1", "--idle-seconds", "300"],
                extra_env={"DBOS__VMID": "worker-1"},
                timeout=90.0,
            )
        )
        workers[1].wait_for("READY-1", timeout=90.0)

        engine = DbosWorkflowEngine(
            database_url=db_url, listen_queues=[], enqueue_start=True
        )
        tag = uuid.uuid4().hex[:6]
        run_ids = asyncio.run(_s06_driver(engine, tag))
        executors = {_status_executor(run_id) for run_id in run_ids}
        assert {"worker-0", "worker-1"} <= executors, (
            f"两个 worker 都应拉到任务（水平扩展），实测 executors={executors}"
        )
    finally:
        for worker in workers:
            if worker.proc.poll() is None:
                worker.stop()


async def _s06_driver(engine: DbosWorkflowEngine, tag: str) -> list[str]:
    """单 driver 协程：分批 enqueue + 全部 await SUCCESS，返回 run_ids（S-06 路径）。

    禁嵌套 `asyncio.run`：本协程已运行在事件循环内，阻塞式 DBOS 客户端调用统一
    `asyncio.to_thread` + `asyncio.wait_for`（有界，规则 18）。
    """

    async def _start(eid: str, index: int) -> str:
        request = WorkflowStartRequest(
            workflow_id="queue-flow",
            tenant_id="tenant-s06",
            user_id="user-s06",
            execution_id=eid,
            trace_id=f"trace-{eid}",
            arguments={"greeting": f"q-{index}"},
            pinned=(WorkflowPinnedRef(kind="workflow", id="queue-flow", version="1"),),
        )
        return (await engine.start(request)).run_id

    async def _await_result(run_id: str) -> None:
        await asyncio.wait_for(
            asyncio.to_thread(DBOS.get_result, run_id), timeout=60.0
        )

    async def _status(run_id: str) -> object:
        return await asyncio.to_thread(DBOS.get_workflow_status, run_id)

    batch1 = [await _start(f"s06-{tag}-{i:02d}", i) for i in range(4)]
    await asyncio.sleep(2.0)  # worker-0 认领并进入 slow step（4s 在飞，仍 busy）
    batch2 = [await _start(f"s06-{tag}-{i:02d}", i) for i in range(4, 8)]

    run_ids = [*batch1, *batch2]
    for run_id in run_ids:
        await _await_result(run_id)
    statuses = [await _status(run_id) for run_id in run_ids]
    assert all(s.status == "SUCCESS" for s in statuses), [
        (run_id, statuses[i].status) for i, run_id in enumerate(run_ids)
    ]
    return run_ids


def _status_executor(run_id: str) -> str:
    """DBOS `executor_id` 即认领该任务的 worker（`DBOS__VMID` → executor_id）。"""
    status = asyncio.run(asyncio.to_thread(DBOS.get_workflow_status, run_id))
    return status.executor_id or "local"
