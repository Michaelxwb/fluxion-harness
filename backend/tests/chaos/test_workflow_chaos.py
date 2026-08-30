"""TASK-002（Phase 6）Chaos——Workflow 组（FEAT-P6-02，design §3.2 套件布局）。

S-03[E2E] + E-02/E-03/E-06（RULE-P6-02：不得 mock Workflow Engine / durable store /
外部 activity）。

- S-03：worker 子进程运行中 SIGKILL → 新进程 recovery 续跑：恢复 P95≤60s
  （NFR-P6-REC-02）、已提交 durable state 无丢失、无重复 side effect
  （NFR-P6-REL-02：irreversible duplicate=0——已完成 step executions==1）；
- E-02：永久失败 capability（activity timeout 等价）→ DBOS step 有界重试
  （3 attempts）后显式 ERROR，不悬挂（fail policy 有界）；
- E-03：同 execution 二次投递 → 幂等（业务记录恰 1 行，side effect 仅一次）；
- E-06：审批长时无人处理 → 无死锁（run 可查询/可取消），signal 唤醒可恢复。

复用 phase3 worker fixtures（真实 DBOS sysdb fluxion_workflow + 真实 worker
子进程 + psycopg 直写业务记录表，非 mock）。
"""

from __future__ import annotations

import asyncio
import time
import uuid

import pytest

from fluxion.runtime.workflow import WorkflowPinnedRef, WorkflowStartRequest
from fluxion.runtime.workflow_dbos import DBOS_QUEUE_NAME, DbosWorkflowEngine
from tests.workflow_runtime.worker_fixtures import (
    WorkerProcess,
    install_worker_bootstrap,
    list_records,
    purge_stale_enqueued,
    purge_stale_workflows,
    wait_for_records,
    worker_db_url,
)

pytestmark = pytest.mark.chaos_workflow


def _db_url() -> str:
    return worker_db_url()


def _wait_queue_drained(database_url: str, *, timeout: float = 8.0) -> None:
    """轮询 DBOS 队列直至 ENQUEUED 清空（消费者完成 poll 且队列空）。"""
    import psycopg
    import time as _time

    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline:
        with psycopg.connect(database_url, autocommit=True) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM dbos.workflow_status WHERE status = 'ENQUEUED'"
            ).fetchone()
        if int(row[0]) == 0:
            return
        _time.sleep(0.5)
    raise AssertionError(f"队列 {timeout}s 内未清空（消费者未完成轮询）")


async def _start(engine: DbosWorkflowEngine, workflow_id: str, execution_id: str):
    request = WorkflowStartRequest(
        workflow_id=workflow_id,
        tenant_id="tenant-chaos-wf",
        user_id="user-chaos",
        execution_id=execution_id,
        trace_id=f"trace-{execution_id}",
        arguments={"greeting": "chaos"},
        pinned=(WorkflowPinnedRef(kind="workflow", id=workflow_id, version="1"),),
    )
    return await engine.start(request)


class TestS03WorkflowChaos:
    def test_s03_backend_restart_recovery_no_loss_no_duplicate(self) -> None:
        """S-03[E2E]：kill workflow worker → recovery 续跑 ≤60s + durable 无丢失 +
        无重复 side effect（crash-flow：3 chained stamp，断点后 a/b 不重跑）。"""
        db_url = _db_url()
        install_worker_bootstrap(db_url)
        purge_stale_enqueued(db_url, DBOS_QUEUE_NAME)
        # review P2：PENDING 残留也清（recover worker 会恢复共享库全部 PENDING
        # run——前序遗留 run 混入计时/语义）
        purge_stale_workflows(db_url)
        eid = f"s03-chaos-{uuid.uuid4().hex[:8]}"
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
                "tenant-chaos-wf",
                "--await-timeout",
                "180",
            ]
        )
        run_id = f"crash-flow:{eid}"
        try:
            worker.wait_for("STARTED", timeout=30.0)
            # step_a/step_b durable 提交 + step_c 已启动（kill 断点）
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
        recovery = WorkerProcess(["recover", "--run-id", run_id, "--timeout", "60"])
        try:
            recovery.wait_for("COMPLETED", timeout=70.0)
        finally:
            if recovery.proc.poll() is None:
                recovery.kill()
        elapsed = time.monotonic() - killed_at

        # NFR-P6-REC-02：恢复 P95≤60s（单样本以全额预算断言）
        assert elapsed <= 60.0, f"S-03: recovery {elapsed:.1f}s > 60s"

        # durable 无丢失 + 无重复 side effect（NFR-P6-REL-02）
        # step_c 在断点处重执行是 at-least-once step 语义（业务写入幂等 upsert，
        # (tenant, run, node) 主键保证恰 1 行）——「无重复 side effect」断言行数
        # 而非重试次数；已完成 step（a/b）durable 提交不重跑（executions==1）。
        records = {r["node_id"]: r for r in list_records(db_url, run_id)}
        assert records["step_a"]["executions"] == 1, "已完成 step_a 不得重跑"
        assert records["step_b"]["executions"] == 1, "已完成 step_b 不得重跑"
        assert records["step_c"]["finished_at"] is not None, "step_c 恢复后必须续跑完成"
        # 每步业务记录恰 1 行（无重复不可逆 side effect）
        assert len(records) == 3, f"业务记录每步恰 1 行，实际 {len(records)} 行"
        assert set(records) == {"step_a", "step_b", "step_c"}


class TestE02ActivityTimeout:
    def test_e02_activity_timeout_fail_policy(self) -> None:
        """E-02[integration]：永久失败 activity → 有界重试（3 attempts）后显式
        ERROR 终态，不悬挂（fail policy 有界，任务标记失败可重试）。"""
        db_url = _db_url()
        install_worker_bootstrap(db_url)
        purge_stale_enqueued(db_url, DBOS_QUEUE_NAME)
        eid = f"e02-chaos-{uuid.uuid4().hex[:8]}"

        started = time.monotonic()
        worker = WorkerProcess(
            [
                "start",
                "--workflow-id",
                "fail-flow",
                "--version",
                "1",
                "--execution-id",
                eid,
                "--tenant",
                "tenant-chaos-wf",
                "--await-timeout",
                "60",
            ]
        )
        try:
            # RUN_FAILED = 显式失败退出（非悬挂超时）
            worker.wait_for("RUN_FAILED", timeout=60.0)
        finally:
            if worker.proc.poll() is None:
                worker.kill()
        elapsed = time.monotonic() - started

        # 有界：3 attempts × 0.2s 间隔 + 启动开销——远小于 await 预算即显式失败
        assert elapsed < 30.0, f"fail policy 应有界快速显式失败（{elapsed:.1f}s）"
        # side effect 每次尝试都有业务留痕——review P2：断言重试真实发生
        # （executions==3 = DBOS step 有界重试 3 attempts 全部尝试后显式失败）
        run_id = f"fail-flow:{eid}"
        records = list_records(db_url, run_id)
        assert records, "失败 activity 应有尝试留痕（明确失败，不静默）"
        assert records[0]["executions"] == 3, (
            f"应有界重试 3 attempts 后显式失败，实际尝试 {records[0]['executions']} 次"
        )


class TestE03DuplicateDelivery:
    async def test_e03_duplicate_delivery_idempotent(self) -> None:
        """E-03[E2E]：同 execution 二次投递 → 幂等执行，仅一次 side effect。"""
        db_url = _db_url()
        install_worker_bootstrap(db_url)
        purge_stale_enqueued(db_url, DBOS_QUEUE_NAME)
        eid = f"e03-chaos-{uuid.uuid4().hex[:8]}"
        run_id = f"quick-flow:{eid}"

        worker = WorkerProcess(
            [
                "start",
                "--workflow-id",
                "quick-flow",
                "--version",
                "1",
                "--execution-id",
                eid,
                "--tenant",
                "tenant-chaos-wf",
                "--args-json",
                '{"greeting": "chaos"}',
                "--await-timeout",
                "60",
            ]
        )
        try:
            # start 模式完成标记是 RUN_RESULT（COMPLETED 是 recover 模式标记）
            worker.wait_for("RUN_RESULT", timeout=60.0)
        finally:
            if worker.proc.poll() is None:
                worker.kill()

        # 二次投递（同 execution_id → 同 run_id）：幂等，不产生新 side effect
        engine = DbosWorkflowEngine(
            database_url=db_url, listen_queues=[], enqueue_start=True
        )
        second = await _start(engine, "quick-flow", eid)
        assert second.run_id == run_id, "同 execution 二次投递应解析到同一 run"

        # 真实消费者在场（review P1-4 空转修复：起 worker serve 轮询队列消费，
        # 若重复投递产生可消费的重复任务/重复执行，业务记录将 >1 行）
        consumer = WorkerProcess(
            ["serve", "--index", "9", "--idle-seconds", "8"],
            extra_env={"DBOS__VMID": "worker-e03"},
        )
        try:
            consumer.wait_for("READY-9", timeout=30.0)
            # 确定性等待（review 残留修复：替代固定 sleep）——轮询 DBOS 队列直至
            # ENQUEUED 清空（消费者至少完成一轮 poll 且队列无可消费任务），
            # 上限 8s
            _wait_queue_drained(db_url, timeout=8.0)
        finally:
            if consumer.proc.poll() is None:
                consumer.kill()

        records = list_records(db_url, run_id)
        assert len(records) == 1, f"幂等：业务记录恰 1 行，实际 {len(records)}"
        assert records[0]["executions"] == 1, (
            f"side effect 仅一次（消费者在场重投递不得重复执行），实际 "
            f"executions={records[0]['executions']}"
        )


class TestE06ApprovalLongWait:
    async def test_e06_approval_long_wait_no_deadlock_recoverable(self) -> None:
        """E-06[E2E]：审批长时无人处理 → 无死锁（run 可查询、等待期状态稳定），
        signal 唤醒后可恢复完成。"""
        db_url = _db_url()
        install_worker_bootstrap(db_url)
        purge_stale_enqueued(db_url, DBOS_QUEUE_NAME)
        eid = f"e06-chaos-{uuid.uuid4().hex[:8]}"
        run_id = f"approval-flow:{eid}"

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
                "tenant-chaos-wf",
                "--await-timeout",
                "90",
            ]
        )
        try:
            worker.wait_for("STARTED", timeout=30.0)
            # prepare 完成后 approval 挂起（无人处理）
            wait_for_records(
                db_url,
                run_id,
                lambda r: r.get("prepare", {}).get("finished_at") is not None,
                timeout=30.0,
                description="prepare committed，approval 挂起",
            )
            # 长时等待：3s 无人处理——期间 run 可查询（无死锁）、worker 进程存活
            await asyncio.sleep(3.0)
            engine = DbosWorkflowEngine(
                database_url=db_url, listen_queues=[], enqueue_start=True
            )
            status = await engine.get_status(run_id)
            assert status is not None, "审批等待期 run 状态必须可查询（无死锁）"
            assert worker.proc.poll() is None, "worker 不得因等待死锁退出"

            # signal 唤醒（可恢复：approve 后 finalize 完成）
            await engine.signal(run_id, "approve", {"approved": True})
            wait_for_records(
                db_url,
                run_id,
                lambda r: r.get("finalize", {}).get("finished_at") is not None,
                timeout=30.0,
                description="approve signal 后 finalize 完成",
            )
        finally:
            if worker.proc.poll() is None:
                worker.kill()

        records = {r["node_id"]: r for r in list_records(db_url, run_id)}
        assert records["finalize"]["executions"] == 1
