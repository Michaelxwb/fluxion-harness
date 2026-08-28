"""TASK-006 验收（S-08 / S-09 / NFR-REL-02 + human_task 超时路径）：durable 等待原语跨重启。

真实边界（不 mock 引擎/存储/worker/DB）：
- S-08：真实 `fluxion-workflow-worker` 子进程运行 approval-flow（prepare → human_task →
  finalize）。等 DBOS durable 挂起检查点（recv 的 timeout-sleep 操作行落库，证明 prepare
  step 已 durable 提交、审批挂起）后 SIGKILL worker，新进程 `launch()` startup recovery
  续跑到 recv 挂起点，随后驱动进程经 `WorkflowTestClient.signal` → `DBOS.send`（durable
  `dbos.notifications`）唤醒 recv_async 并继续到 finalize——审批信号跨重启存活
  （NFR-REL-02）；prepare/finalize 均不重跑（executions==1）。
- S-09：wait-flow（before → wait 6s durable timer → after）。等 before step durable 提交
  + wait 的 wake-time 操作行落库后 SIGKILL worker + 重启，按**原始 wake time** 触发
  （`DBOS.sleep` 行记录的 deadline 不被重启重算；`record_sleep` replay 只返回剩余时间），
  after 落在原始 6s deadline，不因重启多等。
- human_task 超时：approval-timeout-flow（timeout_seconds=2）不 send → recv 超时 →
  按 fail policy 终态 ERROR（worker 打印 RUN_FAILED，非永久挂起）。

计时边界：SLO-WF-02（recovery P95≤60s）；S-09 wait 原始 deadline 6.0s。
"""

from __future__ import annotations

import json
import time
import uuid

import psycopg

from tests.workflow_runtime.worker_fixtures import (
    WorkerProcess,
    WorkflowTestClient,
    install_worker_bootstrap,
    list_records,
    purge_stale_enqueued,
    worker_db_url,
)

from fluxion.runtime.workflow_dbos import (
    DBOS_QUEUE_NAME,
    workflow_run_id,
)


# ---------------------------------------------------------------------------
# durable 挂起检查点：DBOS workflow_status 的 PENDING 是初始/在飞状态（workflow_status
# 建立即 PENDING，全执行期保持），不是"阻塞在 recv/sleep"的信号。唯一可信的挂起信号是
# `DBOS.sleep` 操作行（`record_sleep` 在挂起瞬间落库 wake time）——该行出现 ⇒ 前序 step
# 已返回且其 operation 已 durable 提交，kill 后 recovery 从 memo 续跑、不重放已完成 step。
# ---------------------------------------------------------------------------


def _wait_durable_wait_checkpoint(db_url: str, run_id: str, *, timeout: float) -> None:
    """等 `dbos.operation_outputs` 出现 `DBOS.sleep` 操作行（recv timeout-sleep / wait wake time）。"""
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


def _read_durable_wake_time(db_url: str, run_id: str) -> float:
    """读 `DBOS.sleep` 操作行存储的 wake time（原始 deadline，epoch 秒）。"""
    with psycopg.connect(db_url) as conn:
        row = conn.execute(
            "SELECT output FROM dbos.operation_outputs "
            "WHERE workflow_uuid = %s AND function_name = 'DBOS.sleep'",
            (run_id,),
        ).fetchone()
    assert row is not None, f"workflow {run_id} has no DBOS.sleep checkpoint row"
    return float(row[0])


# ---------------------------------------------------------------------------
# S-08：审批节点运行中 + worker 重启 → send(approve) 唤醒并继续，跨重启存活
# ---------------------------------------------------------------------------


def test_workflow_gate_s08_approval_survives_restart() -> None:
    """S-08[E2E]：审批信号跨 worker SIGKILL + 重启存活，唤醒 recv_async 并继续。

    真实边界：独立 worker 子进程（`fluxion.cli.workflow_worker start/recover`）、
    DBOS `recv_async`/`send`（durable `dbos.notifications`）、本地 PG。断言：
    prepare 在 kill 前 durable 提交（`DBOS.sleep` 检查点出现 ⇒ 进入审批挂起）→ SIGKILL
    → 新进程 startup recovery 续跑到 recv 挂起 → 驱动进程 `signal(run_id, "approve")`
    唤醒 → COMPLETED；finalize 跨重启执行且 executions==1（断点续跑不重放）。
    """
    db_url = worker_db_url()
    install_worker_bootstrap(db_url)  # 驱动进程装配 provider + executor（Registry 读路径）
    purge_stale_enqueued(db_url, DBOS_QUEUE_NAME)  # 清残留 ENQUEUED，防误认领
    # 驱动侧纯 client（DBOSClient，不 launch/不消费 queue）：signal/status 用。launched 的
    # DBOS 无条件消费 `_dbos_internal_queue`，recovery re-enqueue 的 PENDING workflow 会被
    # 驱动进程抢走（executor 被抢成 local，kill/recover 失效）；纯 client 让 recover worker
    #（s08-worker）成为唯一可恢复方。
    client = WorkflowTestClient(db_url)
    eid = f"s08-{uuid.uuid4().hex[:8]}"
    run_id = workflow_run_id("approval-flow", eid)

    worker = WorkerProcess(
        [
            "start",
            "--workflow-id",
            "approval-flow",
            "--version",
            "1",
            "--execution-id",
            eid,
            "--tenant",
            "tenant-s08",
            "--await-timeout",
            "180",
        ],
        extra_env={"DBOS__VMID": "s08-worker"},
    )
    try:
        worker.wait_for("STARTED", timeout=30.0)
        # durable 挂起检查点：recv 的 timeout-sleep 行落库 ⇒ prepare step 已 durable
        # 提交、workflow 阻塞在审批 recv。此信号才可 kill——PENDING 是 DBOS 初始状态，
        # 不代表 prepare 已 checkpoint（详见 `_wait_durable_wait_checkpoint`）。
        _wait_durable_wait_checkpoint(db_url, run_id, timeout=20.0)
        worker.kill()
    finally:
        if worker.proc.poll() is None:
            worker.kill()

    recovery = WorkerProcess(
        ["recover", "--run-id", run_id, "--timeout", "60"],
        extra_env={"DBOS__VMID": "s08-worker"},
    )
    try:
        # 等 launch + startup recovery 重建 recv 挂起后发 signal（topic 契约
        # `{approve}:{run_id}` 与 DbosWorkflowEngine.signal 一致）。
        time.sleep(3.0)
        client.signal(run_id, "approve", {"approved": True})
        recovery.wait_for("COMPLETED", timeout=60.0)
    finally:
        if recovery.proc.poll() is None:
            recovery.kill()

    status = client.get_status(run_id)
    client.close()
    assert status.status == "SUCCESS", status
    records = {r["node_id"]: r for r in list_records(db_url, run_id)}
    assert records["finalize"]["finished_at"] is not None, "审批后节点跨重启必须执行"
    assert records["prepare"]["executions"] == 1, "prepare 已完成不重跑"
    assert records["finalize"]["executions"] == 1, "finalize 恰执行一次"


# ---------------------------------------------------------------------------
# S-09：wait 节点运行中 kill + 重启 worker → 按原始 wake time 触发
# ---------------------------------------------------------------------------


def test_workflow_gate_s09_wait_survives_restart() -> None:
    """S-09[E2E]：wait（sleep_async）跨 SIGKILL + 重启按原始 wake time 触发。

    真实边界：独立 worker 子进程 + DBOS `sleep_async`（durable wake time）+ 本地 PG。
    wait-flow：before(0.2s) → hold(wait 6.0s) → after(0.2s)。等 before durable 提交 +
    wait 的 `DBOS.sleep` 行（wake time）落库后 SIGKILL，1.5s downtime，重启 recovery。
    断言：`DBOS.sleep` 行记录的 wake time 仍是 before.finished + 6.0s（不因重启重算）；
    after 按该 wake time 触发（不提前、也不因重启多等 6s）；before/after 不重跑。
    """
    db_url = worker_db_url()
    install_worker_bootstrap(db_url)
    purge_stale_enqueued(db_url, DBOS_QUEUE_NAME)
    eid = f"s09-{uuid.uuid4().hex[:8]}"
    run_id = workflow_run_id("wait-flow", eid)

    worker = WorkerProcess(
        [
            "start",
            "--workflow-id",
            "wait-flow",
            "--version",
            "1",
            "--execution-id",
            eid,
            "--tenant",
            "tenant-s09",
            "--await-timeout",
            "180",
        ],
        extra_env={"DBOS__VMID": "s09-worker"},
    )
    try:
        worker.wait_for("STARTED", timeout=30.0)
        # before step durable 提交 + wait 的 wake-time 行落库（⇒ before 的 operation 已
        # checkpoint、workflow 进入 durable sleep）。此信号才可 kill。
        _wait_durable_wait_checkpoint(db_url, run_id, timeout=20.0)
        worker.kill()
    finally:
        if worker.proc.poll() is None:
            worker.kill()

    time.sleep(1.5)  # 停机时间流逝（进程已死，原始 wake time 仍在推进）

    recovery = WorkerProcess(
        ["recover", "--run-id", run_id, "--timeout", "60"],
        extra_env={"DBOS__VMID": "s09-worker"},
    )
    try:
        recovery.wait_for("COMPLETED", timeout=70.0)
    finally:
        if recovery.proc.poll() is None:
            recovery.kill()

    records = {r["node_id"]: r for r in list_records(db_url, run_id)}
    before_finish = records["before"]["finished_at"]
    after_start = records["after"]["started_at"]
    wake_time = _read_durable_wake_time(db_url, run_id)

    # 原始 deadline：wake_time = before.finished + 6.0（epoch 秒）。重算 sleep 则 wake_time
    # 会被 recovery 时刻重写（≈ before.finished + 停机 + 恢复开销 + 6s），远超 0.5s 窗口。
    assert abs((wake_time - before_finish) - 6.0) <= 0.5, (
        f"S-09: wait 的 durable wake time 被重启重算 wake={wake_time:.3f} "
        f"before_finish={before_finish:.3f}"
    )
    # after 按原始 wake time 触发：不提前（sleep 尊重 deadline）、不因重启多等
    #（`record_sleep` replay 只返回剩余时间）。
    assert wake_time - 0.5 <= after_start <= wake_time + 3.0, (
        f"S-09: after 未按原始 wake time 触发 after_start={after_start:.3f} "
        f"wake_time={wake_time:.3f}"
    )
    assert records["before"]["executions"] == 1, "before 已完成不重跑"
    assert records["after"]["executions"] == 1, "after 恰执行一次"


# ---------------------------------------------------------------------------
# human_task 超时路径：timeout_seconds 到期 → fail policy 终态 ERROR（非永久挂起）
# ---------------------------------------------------------------------------


def test_workflow_gate_s08_human_task_timeout_terminal() -> None:
    """human_task 超时路径：timeout_seconds=2 且无 signal → recv 超时 → 终态 ERROR。

    真实边界：独立 worker 子进程 + DBOS `recv_async` timeout（durable sleep checkpoint）。
    approval-timeout-flow 单 human_task(timeout_seconds=2)。断言：worker 在边界内打印
    RUN_FAILED（status=ERROR，超时原因带 timeout），非永久挂起——elapsed 落在
    [1.5, 25]s（≥1.5 证明超时确实到期，≤25 证明有界终态）。
    """
    db_url = worker_db_url()
    install_worker_bootstrap(db_url)
    purge_stale_enqueued(db_url, DBOS_QUEUE_NAME)
    eid = f"s08t-{uuid.uuid4().hex[:8]}"

    worker = WorkerProcess(
        [
            "start",
            "--workflow-id",
            "approval-timeout-flow",
            "--version",
            "1",
            "--execution-id",
            eid,
            "--tenant",
            "tenant-s08t",
            "--await-timeout",
            "30",
        ]
    )
    try:
        worker.wait_for("STARTED", timeout=30.0)
        started_at = time.monotonic()
        worker.wait_for("RUN_FAILED", timeout=30.0)
    finally:
        if worker.proc.poll() is None:
            worker.stop()
    elapsed = time.monotonic() - started_at

    assert elapsed >= 1.5, f"human_task 超时过早触发：elapsed={elapsed:.1f}s"
    assert elapsed <= 25.0, f"human_task 未在边界内终态（非永久挂起）：elapsed={elapsed:.1f}s"
    failed = next(
        line for line in worker.lines if line.startswith("RUN_FAILED")
    )
    payload = json.loads(failed[len("RUN_FAILED ") :])
    assert payload["status"] == "ERROR", f"超时应按 fail policy 终态 ERROR: {payload}"
    assert "timeout" in payload["error"].lower() or "timed out" in payload["error"].lower(), (
        f"超时原因应含 timeout 语义: {payload['error']}"
    )
