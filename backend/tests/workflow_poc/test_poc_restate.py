"""Restate PoC 验收测试（ADR-WF-001 TASK-004）。

覆盖 Acceptance Contract 8 场景（S-01/S-02/S-03/S-04/S-05/S-06/B-02/RPO）
+ P-TIMER/P-SIGNAL 专项 + SLO-OBS-01 trace 关联断言；
结果写入 `evidence/restate.json`（TASK-005 矩阵回填数据源）。

真实边界：
- 真实 Restate server（restate-poc 容器，journal/调度/恢复在 server 侧）+ 真实 worker
  子进程（hypercorn SDK 端点，注册 deployment）；
- S-02/RPO/P-TIMER 用真实子进程 SIGKILL（非 mock 故障注入）；恢复在 server 侧，
  新 worker 注册即被续跑；
- S-06 用 2 个真实 worker 进程；workflow 幂等写 step 记录 worker_id（poc_worker_handled）
  作为"2nd worker 拉取到 work"的真实证据。
"""

from __future__ import annotations

import asyncio
import time
import uuid

import pytest

# restate 是 ADR-WF-001 供应商评估 PoC 的可选依赖（评估已定 DBOS，见
# docs/development/DBOS测试探索与踩坑记录.md）；未安装时跳过而非中断收集。
pytest.importorskip("restate", reason="restate PoC dependency not installed")

from fluxion.runtime.workflow import WorkflowStartRequest
from tests.workflow_poc.poc_workflow import (
    MockRetentionGuard,
    RetentionBlockedError,
    TraceCorrelator,
)
from tests.workflow_poc.restate_app import (
    RestateWorkflowEngine,
    attach_correlator,
    reset_business_tables,
    resolve_db_url,
    run_id_for,
)
from tests.workflow_poc.restate_testing import (
    WorkerProcess,
    record_evidence,
    wait_for_condition,
    write_restate_evidence,
)


def _eid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


async def _wait_status(
    engine: RestateWorkflowEngine, run_id: str, status: str, *, timeout: float
) -> None:
    """async 轮询 workflow 状态至期望终态（在测试事件循环内，非 asyncio.run）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if (await engine.get_status(run_id)).status == status:
            return
        await asyncio.sleep(0.5)
    raise AssertionError(f"timeout {timeout}s waiting for {run_id} -> {status}")


def _start_request(tenant_id: str, eid: str, **arguments: object) -> WorkflowStartRequest:
    return WorkflowStartRequest(
        workflow_id="poc-durable",
        tenant_id=tenant_id,
        user_id="user-poc",
        execution_id=eid,
        trace_id=f"trace-{eid}",
        arguments={"execution_id": eid, **arguments},
    )


@pytest.fixture(scope="module")
def engine() -> RestateWorkflowEngine:
    instance = RestateWorkflowEngine()
    yield instance
    write_restate_evidence()


@pytest.fixture(scope="module")
def worker() -> WorkerProcess:
    """模块级共享 worker：直接 start 的 workflow（S-01/P-SIGNAL/S-05/B-02/SLO）执行者。"""
    proc = WorkerProcess(["serve", "--index", "0", "--idle-seconds", "240"], extra_env={"RESTATE__WORKER_ID": "shared"})
    proc.wait_for("READY-0", timeout=60.0)
    yield proc
    if proc.proc.poll() is None:
        proc.stop()


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    reset_business_tables(resolve_db_url())
    yield


@pytest.fixture(scope="module", autouse=True)
def trace_events() -> TraceCorrelator:
    correlator = TraceCorrelator()
    attach_correlator(correlator)
    yield correlator


async def test_s01_durable_start(engine: RestateWorkflowEngine, worker: WorkerProcess) -> None:
    """S-01[E2E]: Adapter 语义→Restate→journal；start 同步持久化且单次延迟 ≤1s（SLO-WF-01）。"""
    eid = _eid("s01")
    run_id = run_id_for("poc-durable", eid)
    started = time.monotonic()
    result = await engine.start(
        _start_request("tenant-s01", eid, approval_timeout_seconds=5.0)
    )
    start_ms = (time.monotonic() - started) * 1000
    assert result.run_id == run_id

    status = await engine.get_status(run_id)  # start 返回即可查 → 同步持久化
    assert status.status in {"PENDING", "SUCCESS"}

    await engine.signal(run_id, "approve", {"approved": True, "reviewer": "r1"})
    result_val = await engine.await_result(run_id, timeout=30.0)
    assert result_val["approval"] == {"approved": True, "reviewer": "r1"}
    assert result_val["pinned_version"] == "v1"
    assert (await engine.get_status(run_id)).status == "SUCCESS"

    records = dict.fromkeys(engine.list_records("tenant-s01"))
    assert (run_id, "report") in records
    assert (run_id, "http") in records
    executions = engine.step_executions(run_id)
    assert executions["write_report_record"] == 1
    assert executions["notify_http_endpoint"] == 1
    assert start_ms <= 1000.0  # SLO-WF-01：durable start ≤1s（单样本）

    record_evidence(
        "S-01",
        passed=True,
        detail="start 经 ingress send（key=run_id）+ 回查可查；signal→结果含审批 payload",
        metrics={"start_ms": round(start_ms, 1)},
    )


async def test_s02_crash_recovery(engine: RestateWorkflowEngine) -> None:
    """S-02[E2E]: 真实 SIGKILL worker 后，server 从 journal 续跑；recovery ≤60s（SLO-WF-02）。"""
    eid = _eid("s02")
    run_id = run_id_for("poc-durable", eid)
    tenant = "tenant-s02"
    worker = WorkerProcess(["serve", "--index", "0", "--idle-seconds", "240"], extra_env={"RESTATE__WORKER_ID": "w02a"})
    try:
        worker.wait_for("READY-0", timeout=60.0)
        await engine.start(
            _start_request(tenant, eid, timer_seconds=4.0, approval_timeout_seconds=1.0)
        )
        wait_for_condition(
            lambda: engine.step_executions(run_id).get("write_report_record") == 1,
            timeout=20.0,
            description="step1 durable commit",
        )
        worker.kill()
    finally:
        if worker.proc.poll() is None:
            worker.kill()

    killed_at = time.monotonic()
    # 恢复在 server 侧：新 worker 注册即被续跑
    recovery = WorkerProcess(["serve", "--index", "1", "--idle-seconds", "240"], extra_env={"RESTATE__WORKER_ID": "w02b"})
    recovery.wait_for("READY-1", timeout=60.0)
    # 等待 workflow 完成（server 续跑；s01 的 worker 已死，需新 worker 执行剩余步骤）
    await _wait_status(engine, run_id, "SUCCESS", timeout=70.0)
    recovery_elapsed = time.monotonic() - killed_at
    if recovery.proc.poll() is None:
        recovery.stop()

    assert recovery_elapsed <= 60.0  # SLO-WF-02
    assert (await engine.get_status(run_id)).status == "SUCCESS"
    executions = engine.step_executions(run_id)
    assert executions["write_report_record"] == 1  # durable step 未重跑（从断点续跑）
    assert engine.list_records(tenant).count((run_id, "report")) == 1  # 副作用无重复

    record_evidence(
        "P-CRASH",
        passed=True,
        detail="worker SIGKILL 后新 worker 注册即被 server 续跑；已完成 step 未重执行",
        metrics={"recovery_seconds": round(recovery_elapsed, 2)},
    )


async def test_ptimer_restart_preserves_timer(engine: RestateWorkflowEngine) -> None:
    """P-TIMER[integration]: worker 重启后 durable timer 按原始 deadline 触发（不重置）。"""
    eid = _eid("ptimer")
    args = {"execution_id": eid, "timer_seconds": 6.0, "approval_timeout_seconds": 1.0}
    worker = WorkerProcess(["serve", "--index", "0", "--idle-seconds", "240"], extra_env={"RESTATE__WORKER_ID": "pt-a"})
    worker.wait_for("READY-0", timeout=60.0)
    await engine.start(_start_request("tenant-ptimer", eid, **args))
    wait_for_condition(
        lambda: engine.step_executions(run_id_for("poc-durable", eid)).get("write_report_record") == 1,
        timeout=15.0,
        description="step1 committed before kill",
    )
    t0 = time.monotonic()
    worker.kill()
    recovery = WorkerProcess(["serve", "--index", "1", "--idle-seconds", "240"], extra_env={"RESTATE__WORKER_ID": "pt-b"})
    recovery.wait_for("READY-1", timeout=60.0)
    await _wait_status(engine, run_id_for("poc-durable", eid), "SUCCESS", timeout=50.0)
    elapsed = time.monotonic() - t0
    if recovery.proc.poll() is None:
        recovery.stop()

    # timer=6s 自原始 start 起算：若重置则 ≥ 恢复点+6+1（≈10s+）；若被跳过则 <5.5s
    assert 5.5 <= elapsed <= 9.5, f"timer deadline not preserved: elapsed={elapsed:.2f}s"
    assert (await engine.get_status(run_id_for("poc-durable", eid))).status == "SUCCESS"

    record_evidence(
        "P-TIMER",
        passed=True,
        detail="kill+restart 后 timer 按原始 deadline 触发（elapsed 落在 [5.5, 9.5]s 窗口）",
        metrics={"elapsed_seconds": round(elapsed, 2), "timer_seconds": 6.0},
    )


async def test_psignal_external_approval_signal(engine: RestateWorkflowEngine, worker: WorkerProcess) -> None:
    """P-SIGNAL[integration]: 外部审批 signal 唤醒等待中的 workflow 并携带 payload 推进。"""
    eid = _eid("psignal")
    run_id = run_id_for("poc-durable", eid)
    started = await engine.start(_start_request("tenant-psignal", eid, approval_timeout_seconds=10.0))
    assert started.run_id == run_id

    await engine.signal(run_id, "approve", {"approved": True, "approver": "tenant-admin"})
    result = await engine.await_result(run_id, timeout=20.0)

    assert result["approval"] == {"approved": True, "approver": "tenant-admin"}
    assert (await engine.get_status(run_id)).status == "SUCCESS"

    record_evidence(
        "P-SIGNAL",
        passed=True,
        detail="ctx.signal('approve') 等待中被 ingress signal（lookup+resolver）唤醒，payload 完整进入结果",
    )


async def test_s03_pinned_resume(engine: RestateWorkflowEngine) -> None:
    """S-03[integration]: resume 使用 pinned version（不 resolve latest）；retention mock 交互。"""
    engine.set_resource_version("poc-wf-def", "v1", is_latest=True)
    guard = MockRetentionGuard()
    eid = _eid("s03")
    run_id = run_id_for("poc-durable", eid)
    args = {"execution_id": eid, "pinned_version": "v1", "timer_seconds": 3.0, "approval_timeout_seconds": 1.0}
    worker = WorkerProcess(["serve", "--index", "0", "--idle-seconds", "240"], extra_env={"RESTATE__WORKER_ID": "s03-a"})
    worker.wait_for("READY-0", timeout=60.0)
    await engine.start(_start_request("tenant-s03", eid, **args))
    wait_for_condition(
        lambda: engine.step_executions(run_id).get("write_report_record") == 1,
        timeout=15.0,
        description="step1 committed before kill",
    )
    guard.acquire(resource_type="workflow_definition", resource_id="poc-wf-def", version="v1", run_id=run_id)
    with pytest.raises(RetentionBlockedError):
        guard.assert_delete_allowed(
            resource_type="workflow_definition", resource_id="poc-wf-def", version="v1"
        )
    worker.kill()

    engine.set_resource_version("poc-wf-def", "v1", is_latest=False)
    engine.set_resource_version("poc-wf-def", "v2", is_latest=True)
    recovery = WorkerProcess(["serve", "--index", "1", "--idle-seconds", "240"], extra_env={"RESTATE__WORKER_ID": "s03-b"})
    recovery.wait_for("READY-1", timeout=60.0)
    await _wait_status(engine, run_id, "SUCCESS", timeout=50.0)
    if recovery.proc.poll() is None:
        recovery.stop()

    run_meta = engine.get_run(run_id)
    assert run_meta is not None and run_meta["pinned_version"] == "v1"
    result = await engine.await_result(run_id, timeout=10.0)
    assert result["pinned_version"] == "v1"  # resume 未 resolve latest(v2)

    guard.release(resource_type="workflow_definition", resource_id="poc-wf-def", version="v1", run_id=run_id)
    guard.assert_delete_allowed(
        resource_type="workflow_definition", resource_id="poc-wf-def", version="v1"
    )

    record_evidence(
        "P-PIN",
        passed=True,
        detail="恢复后仍按 start 时 pinned 的 v1 执行（latest 已漂移 v2）；retention 引用生命周期成立",
    )


async def test_s04_step_timeout(engine: RestateWorkflowEngine, worker: WorkerProcess) -> None:
    """S-04[integration]: step timeout 上限触发定义失败策略，有界返回非无限等待。"""
    eid = _eid("s04")
    run_id = run_id_for("poc-durable", eid)
    started_at = time.monotonic()
    await engine.start(
        _start_request("tenant-s04", eid, external_delay_seconds=5.0, step_timeout_seconds=0.5)
    )

    async def until_error() -> bool:
        while (await engine.get_status(run_id)).status != "ERROR":
            await asyncio.sleep(0.05)
        return True

    await asyncio.wait_for(until_error(), timeout=10.0)
    elapsed = time.monotonic() - started_at

    assert elapsed < 4.0  # timeout=0.5s 生效（若无限等待则 ≥ external_delay=5s）
    assert (await engine.get_status(run_id)).status == "ERROR"

    record_evidence(
        "P-TIMEOUT",
        passed=True,
        detail="step timeout=0.5s 触发（external_delay=5s），invocation 有界转 ERROR",
        metrics={"elapsed_seconds": round(elapsed, 2)},
    )


async def test_s05_idempotency(engine: RestateWorkflowEngine, worker: WorkerProcess) -> None:
    """S-05[integration]: 同 run_id 重放为 no-op，不可逆写副作用重复次数=0（P-IDEMP）。"""
    eid = _eid("s05")
    request = _start_request("tenant-s05", eid)
    first = await engine.start(request)
    result_first = await engine.await_result(first.run_id, timeout=20.0)

    second = await engine.start(request)  # 同 execution 重放（如调用方重试）
    assert second.run_id == first.run_id
    result_second = await engine.await_result(first.run_id, timeout=20.0)
    assert result_second == result_first

    assert engine.step_executions(first.run_id)["write_report_record"] == 1
    assert engine.list_records("tenant-s05").count((first.run_id, "report")) == 1

    record_evidence(
        "P-IDEMP",
        passed=True,
        detail="同 run_id 二次 start（send 返回 PreviouslyAccepted）→ 既有执行；step 未重跑、业务记录恰好 1 条",
    )


async def test_s06_scale_two_workers(engine: RestateWorkflowEngine) -> None:
    """S-06[integration]: 2 个真实 worker 进程分摊 8 个 workflow（P-SCALE / NFR-SCALE-02）。"""
    workers = [
        WorkerProcess(["serve", "--index", "0", "--idle-seconds", "240"], extra_env={"RESTATE__WORKER_ID": "worker-0"})
    ]
    try:
        workers[0].wait_for("READY-0", timeout=60.0)
        time.sleep(1.5)  # noqa: ASYNC251 - PoC：等待 worker-0 在 server 注册后再拉起 worker-1
        workers.append(
            WorkerProcess(["serve", "--index", "1", "--idle-seconds", "240"], extra_env={"RESTATE__WORKER_ID": "worker-1"})
        )
        workers[1].wait_for("READY-1", timeout=90.0)

        tag = uuid.uuid4().hex[:6]
        run_ids = [f"s06-{tag}-{i:02d}" for i in range(8)]
        for i, rid in enumerate(run_ids):
            await engine.start(
                WorkflowStartRequest(
                    workflow_id="poc-durable",
                    tenant_id="tenant-s06",
                    user_id="user-poc",
                    execution_id=rid,
                    trace_id=f"trace-{rid}",
                    arguments={"execution_id": rid, "external_delay_seconds": 3.0},
                )
            )
        for rid in run_ids:
            await asyncio.wait_for(engine.await_result(run_id_for("poc-durable", rid), timeout=40.0), timeout=40.0)

        handled = engine.handled_workers()
        assert len(engine.list_records("tenant-s06")) >= 8  # 每个任务都有 durable 产物
        assert {"worker-0", "worker-1"} <= handled.keys()  # 两个 worker 都拉到了 work（P1：第 2 个 worker 生效）
    finally:
        for w in workers:
            if w.proc.poll() is None:
                w.stop()

    record_evidence(
        "P-SCALE",
        passed=True,
        detail="2 真实 worker 进程；8 workflow 分摊，poc_worker_handled 显示双 worker 执行",
        metrics={"workers_seen": sorted(handled.keys()), "workflows": len(run_ids)},
    )


async def test_b02_tenant_isolation(engine: RestateWorkflowEngine, worker: WorkerProcess) -> None:
    """B-02[integration]: tenant A 的 workflow run 对 tenant B 的查询不可见（NFR-SEC-01）。"""
    eid_a, eid_b = _eid("b02a"), _eid("b02b")
    run_a = run_id_for("poc-durable", eid_a)
    run_b = run_id_for("poc-durable", eid_b)
    await engine.start(_start_request("tenant-a", eid_a))
    await engine.start(_start_request("tenant-b", eid_b))
    await engine.await_result(run_a, timeout=20.0)
    await engine.await_result(run_b, timeout=20.0)

    runs_a = engine.list_run_ids("tenant-a")
    runs_b = engine.list_run_ids("tenant-b")
    assert runs_a == [run_a] and run_b not in runs_a
    assert runs_b == [run_b] and run_a not in runs_b

    records_a = engine.list_records("tenant-a")
    assert records_a and all(run_id == run_a for run_id, _kind in records_a)
    records_b = engine.list_records("tenant-b")
    assert records_b and all(run_id == run_b for run_id, _kind in records_b)

    record_evidence(
        "B-02",
        passed=True,
        detail="tenant 维度 run/record 查询隔离；跨租户互不可见（tenant scope 由 adapter 层承载）",
    )


async def test_rpo_zero_commit(engine: RestateWorkflowEngine) -> None:
    """RPO[integration]: step commit 后 SIGKILL，committed state 无丢失（RULE-backend-database-001）。"""
    eid = _eid("rpo")
    run_id = run_id_for("poc-durable", eid)
    tenant = "tenant-rpo"
    worker = WorkerProcess(["serve", "--index", "0", "--idle-seconds", "240"], extra_env={"RESTATE__WORKER_ID": "rpo-a"})
    try:
        worker.wait_for("READY-0", timeout=60.0)
        await engine.start(
            _start_request(tenant, eid, timer_seconds=5.0, approval_timeout_seconds=0.0)
        )
        wait_for_condition(
            lambda: engine.step_executions(run_id).get("write_report_record") == 1,
            timeout=20.0,
            description="step1 committed before kill",
        )
        worker.kill()
    finally:
        if worker.proc.poll() is None:
            worker.kill()

    # kill 后、restart 前：committed state 仍在（RPO=0）
    assert engine.step_executions(run_id)["write_report_record"] == 1
    assert (run_id, "report") in engine.list_records(tenant)

    recovery = WorkerProcess(["serve", "--index", "1", "--idle-seconds", "240"], extra_env={"RESTATE__WORKER_ID": "rpo-b"})
    recovery.wait_for("READY-1", timeout=60.0)
    await _wait_status(engine, run_id, "SUCCESS", timeout=70.0)
    if recovery.proc.poll() is None:
        recovery.stop()

    assert engine.list_records(tenant).count((run_id, "report")) == 1  # 无重复补偿写
    assert engine.step_executions(run_id)["write_report_record"] == 1
    assert (await engine.get_status(run_id)).status == "SUCCESS"

    record_evidence(
        "RULE-backend-database-001",
        passed=True,
        detail="durable state 在 Restate journal + 业务表；SIGKILL 零丢失、恢复后无重复副作用",
    )


async def test_slo_obs01_trace_correlation(
    engine: RestateWorkflowEngine, worker: WorkerProcess, trace_events: TraceCorrelator
) -> None:
    """SLO-OBS-01: 执行链 trace_id/run_id/tenant_id 关联完整率 ≥99%（样本自足）。"""
    for index in range(2):
        eid = _eid(f"slo{index}")
        run_id = run_id_for("poc-durable", eid)
        await engine.start(_start_request("tenant-slo", eid, approval_timeout_seconds=5.0))
        await engine.signal(run_id, "approve", {"approved": True})
        await engine.await_result(run_id, timeout=20.0)
        assert (await engine.get_status(run_id)).status == "SUCCESS"

    assert trace_events.total_events >= 8
    trace_events.assert_slo_obs01()  # ≥0.99

    record_evidence(
        "SLO-OBS-01",
        passed=True,
        detail=f"trace 关联完整率 {trace_events.completeness():.4f}（{trace_events.correlated_events}/{trace_events.total_events}）",
        metrics={
            "total_events": trace_events.total_events,
            "correlated": trace_events.correlated_events,
        },
    )
