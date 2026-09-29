"""[E-04][E-05][B-03] 租约回收、deadline sweep 与 Schedule 边界（integration）。

真实边界：真实 PostgreSQL（迁移到 head）+ 真实 Redis + 真实 uvicorn 子进程（Console / Worker /
IM Gateway / 渠道探针）。崩溃与终态的判定**一律取自真实 PG 盘面**（租约行、状态、事件、
`finished_at`）与真实进程表，不用退出码或日志推断；等待一律以**绝对时刻**为界，不做无界 sleep。

- **E-04 `lease → Reaper → CAS`**：真实 `SIGKILL` 掉持有租约的 Worker 子进程，租约过期后由
  另一个真实 Worker reclaim（`RECLAIMED` 事件 + `attempt` 递增 + 新 `lease_owner`）；并显式构造
  「过期执行者晚到写入」的对照样本——走生产 `WorkerLoop.run_once` 的终态写路径（注入式
  claimer/executor，行本身由生产 `TaskClaimer` 领取），断言租约守卫**拒绝**覆盖盘面，
  同一路径在有效租约下写成功（正对照，证明断言非空）。Run 侧（`FAILED(RUN_ABANDONED)`
  与会话释放）由既有真实套件 `tests/acceptance/runtime/test_multipod_recovery.py` 承接
  （引用不复制，argv 见 TASK-005 契约表）。
- **E-05 `Scheduler sweep → PG`**：真实 Scheduler 节拍把超过 `deadline_at` 的 Task CAS 成
  `FAILED(TASK_DEADLINE_EXCEEDED)`；终态**仍按 `delivery_mode` 投递**——`FINAL_ONLY` 经真实
  IM Gateway `/internal/deliveries` 投到真实渠道探针并置 `SENT`（投递事实落库 + 探针收到），
  `NONE` 在同一窗口内不产生任何投递事实（同一投递循环在窗口内确实投递了 A 臂 → 负例非空）。
- **B-03 `Scheduler → Schedule`**：misfire 只 SKIP 不补发（无 Task、记原因码与跳过时刻、
  `scheduled_misfire_total` 经真实 `GET /metrics` 可见）；ONCE 成功后 `COMPLETED`、
  `completed_at` 非空、`next_fire_at` 为空。

**为什么不停断言「只有本用例的 Worker 动了手」**：claim 与 reap 在实现上是**全局**的
（`claimer.py::_claimable_conditions` 与 `worker/service.py::_requeue_expired` 都没有 tenant
谓词），环境里任何存活 Worker（别的套件、上一次被杀的运行留下的孤儿进程）都能领取/回收本租户的行。
因此本文件只断言与环境无关的不变式：行**被回收过**、接手者的 `lease_owner` **不等于**被杀者、
接手发生在租约过期**之后**、以及晚到的终态写入被 CAS 守卫拒绝。为使「谁领走」可控，
E-04/E-05 在基座 Worker 的**同一端口**上换入显式配置的 Worker（先停掉基座 Worker，收尾再起回来，
与 TASK-004 的 `_degraded_worker` 同口径）；即便如此，接手者是谁仍不断言。
"""

from __future__ import annotations

import os
import re
import signal
import socket
import subprocess
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import sqlalchemy as sa
from muad_agent_worker.worker.claimer import TaskClaimer
from muad_agent_worker.worker.service import WorkerLoop
from muad_common import SharedSettings
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.acceptance.task_schedule.environment import ServiceProcess

from .environment import (
    TENANT,
    DfxStack,
    TaskSpec,
    await_readyz,
    count_rows,
    count_task_events,
    distinct_free_ports,
    read_task_row,
    resolve_task_spec,
    run_async,
    run_db,
    start_service,
    submit_task,
)

pytestmark = pytest.mark.integration

POLL_INTERVAL_SEC = 0.2
# 负载下（整跑 `tests/acceptance` 时同机并发大量服务进程）服务启动与收敛都会变慢：
# 就绪、终态与进程收敛一律等到上限再失败，而不是启动后立刻断言。
READY_TIMEOUT_SEC = 60.0
RUNNING_TIMEOUT_SEC = 60.0
TERMINAL_TIMEOUT_SEC = 120.0
PROCESS_WAIT_SEC = 30.0

# E-04：被 SIGKILL 的 Worker 用**显式短租约**（基座默认 30s，见 `environment.TASK_LEASE_SEC`），
# 使「租约过期 → 被接手」成为可在用例内等待的事件；心跳仍是 1s（远小于租约）。
RECOVERY_LEASE_SEC = 6
RECOVERY_HEARTBEAT_SEC = 1
# 被杀 Worker 正在执行的时长：必须显著长于「观察到 RUNNING → SIGKILL」的窗口，否则它会自己跑完。
VICTIM_SLEEP_SEC = 12

# 「过期执行者晚到写入」对照样本：stale 行用 2s 租约（很快过期），fresh 行用长租约（正对照必须写成功）。
STALE_OWNER = "dfx-stale-executor"
STALE_LEASE_SEC = 2
FRESH_OWNER = "dfx-fresh-executor"
FRESH_LEASE_SEC = 300
# 占住唯一存活 Worker 的时长：必须覆盖「领两行 → 等租约过期 → 晚到写入」这段窗口。
BLOCKER_SLEEP_SEC = 20

# E-05：deadline sweep 的真实节拍（生产默认 30s 间隔 + 10s 轮询粒度）。
DEADLINE_SWEEP_SEC = 30
SCHEDULER_POLL_SEC = 10
# 「30s 内」的可观测上界：sweep 间隔 30s，但 sweep 只在 Scheduler 轮询拍点上执行，
# 因此从 deadline 到终态的真实上界是「一个 sweep 间隔 + 一个轮询拍点」。
DEADLINE_BUDGET_SEC = float(DEADLINE_SWEEP_SEC + SCHEDULER_POLL_SEC)
DELIVERY_TIMEOUT_SEC = 90.0
# 负例的安静窗口：≥ 2 个投递轮询拍点（生产默认 5s），窗口内 A 臂确实投递成功（非空对照）。
DELIVERY_QUIET_SEC = 12.0
# 本用例自造的投递路由：真实 IM Gateway → 真实渠道探针，不经第三方实网。
DELIVERY_ROUTE: dict[str, Any] = {
    "channel": "WECOM",
    "bot_id": "dfx-recovery-bot",
    "external_user_id": "dfx-recovery-user",
    "external_conversation_id": "dfx-recovery-conversation",
}
SCHEDULE_ROUTE: dict[str, Any] = {
    "channel": "WECOM",
    "bot_id": "dfx-recovery-schedule-bot",
    "external_user_id": "dfx-recovery-user",
    "external_conversation_id": "dfx-recovery-conversation",
}
SKIP_MISFIRE = "SCHEDULE_MISFIRE_SKIPPED"
TASK_DEADLINE_EXCEEDED = "TASK_DEADLINE_EXCEEDED"
MISFIRE_METRIC = "scheduled_misfire_total"

# 用例内换入/新起的服务进程：模块收尾必须全部停掉，否则留下孤儿进程共用同一测试库。
_SPAWNED_SERVICES: list[ServiceProcess] = []
# 基座端口上「此刻真正活着」的 Worker：基座对象，或用例内换入/还原的那个。
# 只停基座对象不够：上一个用例还原的 Worker 仍占着同一端口，新进程会绑定失败而 `/healthz`
# 由旧进程应答，于是用例以为换入成功、实际跑的是没有注入配置的旧 Worker（E-05 曾因此误判）。
_LIVE_WORKER: list[ServiceProcess] = []


@pytest.fixture(scope="module", autouse=True)
def _stop_spawned_services(live_stack: DfxStack) -> Iterator[None]:
    """模块收尾：停掉用例内换入/新起的服务（依赖 `live_stack` 以保证先于其清理执行）。"""
    yield
    for process in _SPAWNED_SERVICES:
        process.stop()
    _SPAWNED_SERVICES.clear()


def _database_url() -> str:
    return SharedSettings().require_database_url()


def _new_key(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def _submit(
    stack: DfxStack,
    http: httpx.Client,
    spec: TaskSpec,
    *,
    delivery_mode: str = "NONE",
    delivery_route: dict[str, Any] | None = None,
    **input_data: Any,
) -> uuid.UUID:
    """经真实 HTTP 提交一个 Task，返回 PG 里的真实 task id。"""
    submitted = submit_task(
        stack,
        http,
        spec,
        input_data=input_data,
        idempotency_key=_new_key("dfx-recovery"),
        delivery_mode=delivery_mode,
        delivery_route=delivery_route,
    )
    return uuid.UUID(str(submitted["task_id"]))


def _await_row(
    task_id: uuid.UUID, predicate: Callable[[dict[str, Any]], bool], *, what: str, timeout_sec: float
) -> dict[str, Any]:
    """有界轮询真实 PG：以绝对时刻为界，条件不成立就带着最后盘面失败。"""
    deadline = time.monotonic() + timeout_sec
    row: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        row = read_task_row(task_id)
        assert row is not None, f"PG 中不存在 task {task_id}"
        if predicate(row):
            return row
        time.sleep(POLL_INTERVAL_SEC)
    raise AssertionError(f"task {task_id} 未在 {timeout_sec}s 内{what}，最后盘面：{row}")


def _await_status(task_id: uuid.UUID, status: str, *, timeout_sec: float) -> dict[str, Any]:
    return _await_row(
        task_id, lambda row: row["status"] == status, what=f"到达 {status}", timeout_sec=timeout_sec
    )


def _await_lease_expired(task_id: uuid.UUID, *, timeout_sec: float) -> dict[str, Any]:
    """等到行上的租约**真的过期**（绝对时刻比较：`lease_until < now`），不做无界 sleep。"""
    return _await_row(
        task_id,
        lambda row: row["lease_until"] is not None and row["lease_until"] < datetime.now(UTC),
        what="租约过期",
        timeout_sec=timeout_sec,
    )


def _read_events(task_id: uuid.UUID) -> list[dict[str, Any]]:
    """逐行回读真实事件流（`seq` 升序）：租约与终态的事实以事件流为准。"""

    async def query(factory: Any) -> list[dict[str, Any]]:
        async with factory() as session:
            rows = (
                await session.execute(
                    sa.text(
                        "SELECT seq, event_type, payload_json, create_time FROM task.task_event"
                        " WHERE tenant_id = :t AND task_id = :id ORDER BY seq"
                    ),
                    {"t": TENANT, "id": task_id},
                )
            ).all()
        return [
            {
                "seq": row[0],
                "event_type": row[1],
                "payload": row[2],
                "create_time": row[3],
            }
            for row in rows
        ]

    return cast(list[dict[str, Any]], run_db(query))


def _claim_events(task_id: uuid.UUID) -> list[dict[str, Any]]:
    """`CLAIMED` 事件的真实载荷（含 `lease_owner`/`attempt`）：接手者是谁看这里。"""
    return [event for event in _read_events(task_id) if event["event_type"] == "CLAIMED"]


def _claimable_task_ids() -> list[uuid.UUID]:
    """全局可领行（claim 无 tenant 谓词）：对照样本要求此刻没有本用例之外的可领残留。"""

    async def query(factory: Any) -> list[uuid.UUID]:
        async with factory() as session:
            rows = (
                await session.execute(
                    sa.text(
                        "SELECT id FROM task.task_execution WHERE status IN ('QUEUED','WAITING')"
                        " AND not_before <= now() AND cancel_requested IS FALSE"
                        " AND is_deleted IS FALSE"
                    )
                )
            ).all()
        return [row[0] for row in rows]

    return cast(list[uuid.UUID], run_db(query))


def _instance_id(process: ServiceProcess) -> str:
    """Worker 子进程的 lease 实例标识（与 `worker/service.py::default_instance_id` 同口径）。"""
    handle = process._process
    assert handle is not None and handle.pid is not None, f"{process.name} 未启动"
    return f"{socket.gethostname()}:{handle.pid}"


def _sigkill(process: ServiceProcess) -> None:
    """真实 `SIGKILL`（不是 terminate、不是异常退出）：崩溃注入点。"""
    handle = process._process
    assert handle is not None, f"{process.name} 未启动"
    handle.send_signal(signal.SIGKILL)
    handle.wait(timeout=10)
    assert handle.poll() is not None, f"{process.name} 未被杀死"


def _service_commands() -> list[str]:
    """当前在跑的**真实服务进程**命令行（孤儿进程判据）。

    判据是「可执行文件是 Python 且命令行为 `-m uvicorn muad_*`」：真实服务的启动形式就是
    `python -m uvicorn muad_*.main:app`。不能只按「命令行里出现 uvicorn/muad_」过滤——
    调用方 shell 自己的命令行只要提到这两个子串（例如 `grep -E "uvicorn|muad_"`、
    `grep -c "-m uvicorn muad_"`），那个 shell 就会被算成多余服务进程，把「无多余进程」的断言
    变成假失败（2026-09-29 整跑 `tests/acceptance` 时实测到 5 例；改用 `comm` 判定后消除）。
    """
    completed = subprocess.run(
        ["ps", "-Aww", "-o", "ucomm=,command="], capture_output=True, text=True, check=True
    )
    services: list[str] = []
    for line in completed.stdout.splitlines():
        # `ucomm` 是 argv[0] 的名字（`python3.13`）；`comm` 在 macOS 上被截断成 16 字符，
        # 形如 `/Users/jahan/wor`，用它做判据会把真实服务漏掉。
        ucomm, _, command = line.strip().partition(" ")
        if "python" not in ucomm.lower():
            continue
        if "-m uvicorn muad_" in command:
            services.append(command.strip())
    return services


def _await_service_processes(expected: dict[str, int]) -> list[str]:
    """等进程收敛到期望分布（停进程后进程表有极短抖动窗口）。"""
    deadline = time.monotonic() + PROCESS_WAIT_SEC
    commands: list[str] = []
    while time.monotonic() < deadline:
        commands = _service_commands()
        if {module: sum(module in line for line in commands) for module in expected} == expected:
            return commands
        time.sleep(POLL_INTERVAL_SEC)
    raise AssertionError(f"服务进程未收敛到 {expected}：{commands}")


def _await_no_extra_services() -> None:
    """本用例起的临时服务（Gateway/探针/换入 Worker）必须全部停掉，只留基座一对。"""
    commands = _await_service_processes(
        {"muad_console_platform.main": 1, "muad_agent_worker.main": 1}
    )
    assert len(commands) == 2, f"本用例仍有多余服务进程：{commands}"


def _await_port_free(port: int, *, timeout_sec: float = 15.0) -> None:
    """换入前确认端口真的空出来：否则新进程绑定失败、`/healthz` 由旧进程应答（静默换不成功）。"""
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        with socket.socket() as probe:
            probe.settimeout(1.0)
            if probe.connect_ex(("127.0.0.1", port)) != 0:
                return
        time.sleep(POLL_INTERVAL_SEC)
    raise AssertionError(f"端口 {port} 在 {timeout_sec}s 内仍被占用：换入的 Worker 不会真的接管")


def _stop_live_worker(live_stack: DfxStack) -> None:
    """停掉基座端口上此刻真正活着的 Worker（基座对象或用例内还原的那个）。"""
    for process in [*_LIVE_WORKER, live_stack.processes["worker"]]:
        process.stop()
    _LIVE_WORKER.clear()


@contextmanager
def _swapped_workers(live_stack: DfxStack, log_dir: Path) -> Iterator[Callable[..., ServiceProcess]]:
    """停掉基座端口上的 Worker，用**同一端口 + 同一基座环境**起替代 Worker（可叠加显式配置）。

    claim/reap 是全局的：只要还有一个存活 Worker，「谁领走这一行」就不可控。用例内只让自己的
    Worker 参与，收尾按基座环境把 Worker 起回来（同一端口 + 同一 env，与 TASK-004 的
    `_degraded_worker` 同口径，不另造第二套进程管理）。
    """
    base = live_stack.processes["worker"]
    _stop_live_worker(live_stack)
    started: list[ServiceProcess] = []

    def start(name: str, **overrides: str) -> ServiceProcess:
        process = ServiceProcess(
            name=name,
            module="muad_agent_worker.main",
            port=base.port,
            env={**base.env, **overrides},
            log_path=log_dir / f"{name}.log",
        )
        _await_port_free(base.port)
        start_service(process)
        started.append(process)
        return process

    try:
        yield start
    finally:
        for process in started:
            process.stop()
        _LIVE_WORKER.clear()
        restored = ServiceProcess(
            name="worker",
            module="muad_agent_worker.main",
            port=base.port,
            env=base.env,
            log_path=log_dir / "worker-restored.log",
        )
        _await_port_free(base.port)
        start_service(restored)
        _SPAWNED_SERVICES.append(restored)
        _LIVE_WORKER.append(restored)


class _FixedClaimer:
    """把「已由生产 `TaskClaimer` 领到的行」交给 `run_once`：不重复实现 claim 语义。"""

    def __init__(self, task: Any) -> None:
        self._task = task

    async def claim_one(self, instance_id: str, *, now: datetime | None = None) -> Any:
        return self._task


class _ResultExecutor:
    """执行器替身（本层允许：纯函数替身）：把给定结果原样交回 `interpret_execution`。"""

    def __init__(self, result: dict[str, Any]) -> None:
        self._result = result
        self.calls = 0

    async def execute(self, task: Any) -> dict[str, Any]:
        self.calls += 1
        return {"status": "SUCCEEDED", "result": self._result, "stderr": "", "exit_code": 0}


def _claim_in_process(task_id: uuid.UUID, instance_id: str, *, lease_sec: int) -> Any:
    """用生产 `TaskClaimer` 单次领取指定行；领到别的行即失败（共享库里有本用例之外的可领残留）。"""

    async def claim() -> Any:
        engine = create_async_engine(_database_url())
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            claimer = TaskClaimer(factory, SharedSettings(task_lease_sec=lease_sec))
            task = await claimer.claim_one(instance_id)
        finally:
            await engine.dispose()
        assert task is not None, f"没有可领的行（{task_id} 应仍在队列里）"
        assert task.id == task_id, f"claim 领到了别的行（{task.id}）：共享库有其它可领残留"
        return task

    return run_async(claim)


def _run_terminal_write(
    task: Any, *, instance_id: str, result: dict[str, Any], lease_sec: int
) -> Any:
    """走生产 `WorkerLoop.run_once` 的终态写路径（`_handle_success`/`_mark_cancelled` 同一条 CAS 守卫）。

    返回 `run_once` 的返回值（被处理行的 id）：非 None 说明「执行结束 → 写终态」整条路径确实
    跑完了，而不是被提前跳过；执行器调用次数同批断言。
    """

    async def run() -> Any:
        engine = create_async_engine(_database_url())
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            executor = _ResultExecutor(result)
            loop = WorkerLoop(
                factory,
                SharedSettings(task_lease_sec=lease_sec),
                executor=executor,
                claimer=cast(Any, _FixedClaimer(task)),
                instance_id=instance_id,
            )
            outcome = await loop.run_once()
        finally:
            await engine.dispose()
        assert executor.calls == 1, "执行器没有被调用：终态写路径没有真正跑起来"
        return outcome

    return run_async(run)


def _assert_sigkill_reclaim(
    task_id: uuid.UUID, *, victim_owner: str, last_heartbeat_at: datetime
) -> dict[str, Any]:
    """E-04 主链的不变式：被回收过、接手者不是被杀者、接手发生在租约过期之后。

    断言的是**与环境无关的不变式**而不是「恰好一次」：claim 与 reap 都是全局的，负载下接手者
    可能被别的存活 Worker 抢先（甚至多接手一次），所以只要求「被杀者的首次 claim → 租约过期后
    至少一次由**别人**完成的接手」，不要求接手恰好一次、也不要求接手者是本用例的哪个进程。
    """
    row = _await_status(task_id, "COMPLETED", timeout_sec=TERMINAL_TIMEOUT_SEC)
    claims = _claim_events(task_id)
    assert len(claims) >= 2, f"claim 次数不足（期望被杀者 + 至少一个接手者）：{claims}"
    assert claims[0]["payload"]["lease_owner"] == victim_owner, (
        f"首次 claim 的租约归属不是被杀者：{claims[0]['payload']}"
    )
    handovers = claims[1:]
    assert all(event["payload"]["lease_owner"] != victim_owner for event in handovers), (
        "被杀者死后又重新领到了这一行（进程未真的停止）"
    )
    # 绝对时刻：首次接手必须发生在被杀者的租约过期之后（心跳已随进程停止，租约不会再被续）。
    expired_after = last_heartbeat_at + timedelta(seconds=RECOVERY_LEASE_SEC)
    assert handovers[0]["create_time"] > expired_after, (
        f"接手发生在租约过期之前（{handovers[0]['create_time']} <= {expired_after}）"
    )
    assert row["attempt"] >= 2, f"接手必须保留并递增 attempt：{row['attempt']}"
    assert row["result_json"] is not None and row["result_json"].get("probe") == "killed"
    assert row["lease_owner"] is None and row["lease_until"] is None, "终态必须释放租约"
    assert row["finished_at"] is not None
    assert count_task_events(task_id, "RECLAIMED") >= 1, "租约过期必须留下 RECLAIMED 事件"
    return row


def _sigkill_reclaim_arm(
    live_stack: DfxStack,
    http: httpx.Client,
    tmp_path: Path,
    start: Callable[..., ServiceProcess],
    spec: TaskSpec,
) -> uuid.UUID:
    """E-04 主链：真实 SIGKILL 一个持有租约的 Worker → 租约过期 → 另一个真实 Worker 接手。"""
    victim = start(
        "worker-victim",
        TASK_LEASE_SEC=str(RECOVERY_LEASE_SEC),
        TASK_HEARTBEAT_SEC=str(RECOVERY_HEARTBEAT_SEC),
    )
    victim_owner = _instance_id(victim)
    task_id = _submit(live_stack, http, spec, sleep_sec=VICTIM_SLEEP_SEC, probe="killed")
    # 前置：这一行必须真的被**被杀进程**领走（判据是 PG 的 lease_owner，不是进程状态）。
    held = _await_row(
        task_id,
        lambda row: row["status"] == "RUNNING" and row["lease_owner"] == victim_owner,
        what=f"被 {victim_owner} 领取并执行",
        timeout_sec=RUNNING_TIMEOUT_SEC,
    )
    last_heartbeat_at = cast(datetime, held["heartbeat_at"])
    assert held["lease_until"] is not None and held["attempt"] == 1

    _sigkill(victim)  # 真实进程终止
    # 租约还没过期：此刻谁都不能合法接手（接手只能来自 reclaim 之后的重新 claim）。
    before_expiry = cast(dict[str, Any], read_task_row(task_id))
    assert before_expiry["lease_owner"] == victim_owner, "被杀后租约不应立即易主"
    # 接手者用基座默认租约（30s/心跳 1s，30x 余量）：它要长期持有这一行，租约太短会被负载惊扰。
    start("worker-survivor")
    _await_lease_expired(task_id, timeout_sec=RECOVERY_LEASE_SEC + 30.0)
    _assert_sigkill_reclaim(task_id, victim_owner=victim_owner, last_heartbeat_at=last_heartbeat_at)
    return task_id


def _stale_write_control_sample(
    live_stack: DfxStack,
    http: httpx.Client,
    spec: TaskSpec,
    start: Callable[..., ServiceProcess],
) -> None:
    """「过期执行者晚到写入」对照样本：同一终态写路径 × 有效租约（正例）/ 过期租约（负例）。"""
    # 让唯一存活的 Worker 忙于一条长任务，使样本的 claim 在窗口内可控（claim 本身仍走生产实现）。
    blocker_id = _submit(live_stack, http, spec, sleep_sec=BLOCKER_SLEEP_SEC, probe="blocker")
    _await_row(
        blocker_id,
        lambda row: row["status"] == "RUNNING",
        what="被存活 Worker 领取（占住单循环）",
        timeout_sec=RUNNING_TIMEOUT_SEC,
    )
    stale_id = _submit(live_stack, http, spec, sleep_sec=0, probe="stale")
    fresh_id = _submit(live_stack, http, spec, sleep_sec=0, probe="fresh")
    claimable = _claimable_task_ids()
    assert set(claimable) == {stale_id, fresh_id}, (
        f"对照样本要求此刻只有本用例的两行可领（claim 是全局的）：{claimable}"
    )
    stale_task = _claim_in_process(stale_id, STALE_OWNER, lease_sec=STALE_LEASE_SEC)
    fresh_task = _claim_in_process(fresh_id, FRESH_OWNER, lease_sec=FRESH_LEASE_SEC)

    # 正对照：同一条终态写路径在有效租约下**确实会写**（否则下面的拒绝可能是空断言）。
    assert (
        _run_terminal_write(
            fresh_task, instance_id=FRESH_OWNER, result={"probe": "fresh"}, lease_sec=FRESH_LEASE_SEC
        )
        == fresh_id
    )
    fresh_row = _await_status(fresh_id, "COMPLETED", timeout_sec=TERMINAL_TIMEOUT_SEC)
    assert fresh_row["result_json"] == {"probe": "fresh"}
    assert fresh_row["attempt"] == 1 and fresh_row["lease_owner"] is None

    # 负例一：租约过期但行仍 RUNNING 仍归属过期执行者 —— 唯一失效的条件就是租约到期。
    stale_row = _await_lease_expired(stale_id, timeout_sec=STALE_LEASE_SEC + 30.0)
    assert stale_row["status"] == "RUNNING" and stale_row["lease_owner"] == STALE_OWNER, (
        f"样本前置换状态不成立（唯一失效的条件应是租约到期）：{stale_row}"
    )
    assert (
        _run_terminal_write(
            stale_task, instance_id=STALE_OWNER, result={"probe": "stale"}, lease_sec=STALE_LEASE_SEC
        )
        == stale_id
    )
    after = cast(dict[str, Any], read_task_row(stale_id))
    assert after == stale_row, f"租约守卫失效：过期执行者覆盖了盘面 {stale_row} → {after}"
    assert count_task_events(stale_id, "COMPLETED") == 0
    assert count_task_events(stale_id, "CANCELLED") == 0

    # 存活 Worker 空出手来后按 lease 过期 reclaim 这一行并重新领取（产生新终态）。
    _await_status(blocker_id, "COMPLETED", timeout_sec=TERMINAL_TIMEOUT_SEC)
    revived = _await_status(stale_id, "COMPLETED", timeout_sec=TERMINAL_TIMEOUT_SEC)
    assert revived["attempt"] == 2 and revived["result_json"]["probe"] == "stale"
    assert count_task_events(stale_id, "RECLAIMED") == 1

    # 负例二（终态守卫）：过期执行者的晚到写入不得覆盖接手者写下的新终态。
    assert (
        _run_terminal_write(
            stale_task,
            instance_id=STALE_OWNER,
            result={"probe": "stale-overwrite"},
            lease_sec=STALE_LEASE_SEC,
        )
        == stale_id
    )
    final = cast(dict[str, Any], read_task_row(stale_id))
    assert final == revived, f"新终态被过期执行者覆盖：{revived} → {final}"
    assert final["result_json"]["probe"] == "stale", "接手者的终态结果被替换"


def test_e04_sigkill_worker_is_reclaimed_and_stale_write_is_refused(
    live_stack: DfxStack, http: httpx.Client, tmp_path: Path
) -> None:
    """[E-04] 真实 SIGKILL → 租约过期 → 另一 Worker reclaim；过期执行者的晚到写入被 CAS 拒绝。"""
    spec = resolve_task_spec(live_stack, http)
    with _swapped_workers(live_stack, tmp_path) as start:
        killed_id = _sigkill_reclaim_arm(live_stack, http, tmp_path, start, spec)
        _stale_write_control_sample(live_stack, http, spec, start)
    _await_no_extra_services()
    assert count_rows("task.task_execution", live_stack.tenant_id, "id = :id", {"id": killed_id}) == 1


@contextmanager
def _delivery_pair(
    live_stack: DfxStack, tmp_path: Path
) -> Iterator[tuple[str, str]]:
    """真实 IM Gateway + 真实渠道探针（外部渠道由本地真实 HTTP 探针承载，不是替身）。

    退出时两个进程都停掉：它们是用例自起的临时服务，收尾不得留下孤儿。
    """
    probe_port, gateway_port = distinct_free_ports(2)
    probe_url = f"http://127.0.0.1:{probe_port}"
    gateway_url = f"http://127.0.0.1:{gateway_port}"
    settings = SharedSettings()
    probe = ServiceProcess(
        name="dfx-channel-probe",
        module="tests.acceptance.task_schedule.channel_probe",
        port=probe_port,
        env={**os.environ},
        log_path=tmp_path / "channel-probe.log",
    )
    gateway = ServiceProcess(
        name="dfx-im-gateway",
        module="muad_im_gateway.main",
        port=gateway_port,
        env={
            **os.environ,
            "DATABASE_URL": settings.require_database_url(),
            "REDIS_URL": settings.require_redis_url(),
            "CONSOLE_PLATFORM_URL": live_stack.console_url,
            "CHANNEL_PROBE_URL": f"{probe_url}/probe/deliveries",
            "DEFAULT_TENANT_ID": live_stack.tenant_id,
            "INTERNAL_SERVICE_TOKEN": live_stack.service_headers()["X-Internal-Service"],
        },
        log_path=tmp_path / "im-gateway.log",
    )
    start_service(probe)
    start_service(gateway)
    try:
        await_readyz(gateway_url, timeout_sec=READY_TIMEOUT_SEC)
        yield gateway_url, probe_url
    finally:
        gateway.stop()
        probe.stop()


def _probe_deliveries(probe_url: str, bot_id: str) -> list[dict[str, Any]]:
    """渠道探针真实收到的投递记录（按本用例的 bot_id 归因）。"""
    response = httpx.get(f"{probe_url}/probe/deliveries", timeout=5.0)
    assert response.status_code == 200, response.text
    records = cast(list[dict[str, Any]], response.json()["deliveries"])
    return [record for record in records if record.get("bot_id") == bot_id]


def _expire_deadline(task_ids: list[uuid.UUID], *, seconds: int = 2) -> dict[uuid.UUID, datetime]:
    """把真实 `deadline_at` 列推到即将到期（真实列写入）：返回逐行回读的截止时刻。"""

    async def update(factory: Any) -> dict[uuid.UUID, datetime]:
        deadlines: dict[uuid.UUID, datetime] = {}
        async with factory() as session:
            for task_id in task_ids:
                row = (
                    await session.execute(
                        sa.text(
                            "UPDATE task.task_execution SET deadline_at = now()"
                            " + make_interval(secs => :secs)"
                            " WHERE tenant_id = :t AND id = :id RETURNING deadline_at"
                        ),
                        {"t": TENANT, "id": task_id, "secs": seconds},
                    )
                ).one()
                deadlines[task_id] = row[0]
            await session.commit()
        return deadlines

    deadlines = cast(dict[uuid.UUID, datetime], run_db(update))
    assert set(deadlines) == set(task_ids), f"deadline_at 未作用到全部样本：{deadlines}"
    return deadlines


def _assert_deadline_swept(
    task_id: uuid.UUID, deadline_at: datetime, *, label: str
) -> dict[str, Any]:
    """E-05 主链：deadline 到期后由 Scheduler sweep CAS 失败终态，且量级符合 30s 承诺。"""
    row = _await_row(
        task_id,
        lambda item: item["status"] == "FAILED",
        what="被 sweep CAS 成失败终态",
        timeout_sec=DEADLINE_BUDGET_SEC + 30.0,
    )
    assert row["error_code"] == TASK_DEADLINE_EXCEEDED, f"{label} 错误码不符：{row}"
    assert row["finished_at"] is not None and row["lease_owner"] is None
    assert row["lease_until"] is None, "终态必须释放租约"
    assert count_task_events(task_id, "DEADLINE_EXCEEDED") == 1
    elapsed = cast(datetime, row["finished_at"]) - deadline_at
    assert elapsed >= timedelta(0), f"{label} 在 deadline 之前就被判失败：{elapsed}"
    assert elapsed <= timedelta(seconds=DEADLINE_BUDGET_SEC), (
        f"{label} 超过 {DEADLINE_BUDGET_SEC}s 才被 sweep（实测 {elapsed}）"
    )
    return row


def _assert_delivery_by_mode(
    deliverable_id: uuid.UUID, silent_id: uuid.UUID, silent: dict[str, Any], probe_url: str
) -> None:
    """终态仍按 delivery_mode 投递：FINAL_ONLY 真实送达；NONE 在同一窗口内零投递事实。"""
    sent = _await_row(
        deliverable_id,
        lambda row: row["delivery_status"] == "SENT",
        what="按 delivery_mode=FINAL_ONLY 投递成功",
        timeout_sec=DELIVERY_TIMEOUT_SEC,
    )
    assert sent["delivery_attempts"] >= 1 and sent["delivered_at"] is not None
    assert count_task_events(deliverable_id, "DELIVERY_SENT") == 1
    records = _probe_deliveries(probe_url, DELIVERY_ROUTE["bot_id"])
    assert len(records) >= 1, "真实渠道探针没有收到投递（Gateway→探针链路未打通）"
    assert all("超过截止时间" in str(record.get("text")) for record in records), records

    # 同窗口负例：delivery_mode=NONE 不产生任何投递事实；A 臂已证明投递循环在跑（非空对照）。
    quiet_deadline = time.monotonic() + DELIVERY_QUIET_SEC
    while time.monotonic() < quiet_deadline:
        assert _probe_deliveries(probe_url, DELIVERY_ROUTE["bot_id"]) == records, (
            "安静窗口内又出现了新的投递记录"
        )
        time.sleep(POLL_INTERVAL_SEC)
    assert silent["delivery_status"] == "NONE" and silent["delivery_attempts"] == 0
    assert silent["delivered_at"] is None
    for event_type in ("DELIVERY_SENT", "DELIVERY_FAILED", "DELIVERY_RETRY"):
        assert count_task_events(silent_id, event_type) == 0


def test_e05_deadline_sweep_cas_and_delivery_by_mode(
    live_stack: DfxStack, http: httpx.Client, tmp_path: Path
) -> None:
    """[E-05] 超过 deadline_at 的 Task 被 CAS `FAILED(TASK_DEADLINE_EXCEEDED)`，并仍按 delivery_mode 投递。"""
    spec = resolve_task_spec(live_stack, http)
    with _delivery_pair(live_stack, tmp_path) as (gateway_url, probe_url):
        with _swapped_workers(live_stack, tmp_path) as start:
            start("worker-delivery", IM_GATEWAY_URL=gateway_url)
            deliverable_id = _submit(
                live_stack,
                http,
                spec,
                sleep_sec=60,
                probe="deadline-delivery",
                delivery_mode="FINAL_ONLY",
                delivery_route=DELIVERY_ROUTE,
            )
            silent_id = _submit(live_stack, http, spec, sleep_sec=60, probe="deadline-silent")
            deadlines = _expire_deadline([deliverable_id, silent_id])
            _assert_deadline_swept(
                deliverable_id, deadlines[deliverable_id], label="FINAL_ONLY 样本"
            )
            silent = _assert_deadline_swept(silent_id, deadlines[silent_id], label="NONE 样本")
            _assert_delivery_by_mode(deliverable_id, silent_id, silent, probe_url)
    _await_no_extra_services()


def _create_schedule(
    http: httpx.Client,
    stack: DfxStack,
    *,
    name: str,
    schedule: dict[str, Any],
    route: dict[str, Any],
) -> dict[str, Any]:
    """真实 HTTP 创建 Schedule（Worker 内部接口，Runtime 调用的同一入口）。"""
    response = http.post(
        f"{stack.worker_url}/internal/schedules",
        json={
            "name": name,
            "agent_id": str(stack.agent_id),
            "actor_user_id": str(stack.platform_user_id),
            "intent_key": "dfx_recovery_probe",
            "skill_id": str(stack.skill_id),
            "input_template": {},
            "schedule": schedule,
            "delivery_route": route,
        },
        headers={**stack.service_headers(), "Idempotency-Key": _new_key("dfx-recovery-schedule")},
    )
    assert response.status_code == 200, f"创建 Schedule 失败：{response.status_code} {response.text}"
    return cast(dict[str, Any], response.json()["data"])


def _read_schedule(schedule_id: uuid.UUID) -> dict[str, Any] | None:
    """从真实 PG 逐行回读 Schedule 盘面。"""

    async def query(factory: Any) -> dict[str, Any] | None:
        async with factory() as session:
            row = (
                await session.execute(
                    sa.text(
                        "SELECT status, next_fire_at, last_fire_at, completed_at, last_error_code,"
                        " last_error_message, last_skipped_at, revision FROM task.task_schedule"
                        " WHERE tenant_id = :t AND id = :id"
                    ),
                    {"t": TENANT, "id": schedule_id},
                )
            ).one_or_none()
        if row is None:
            return None
        keys = (
            "status",
            "next_fire_at",
            "last_fire_at",
            "completed_at",
            "last_error_code",
            "last_error_message",
            "last_skipped_at",
            "revision",
        )
        return dict(zip(keys, row, strict=True))

    return cast("dict[str, Any] | None", run_db(query))


def _await_schedule(
    schedule_id: uuid.UUID, predicate: Callable[[dict[str, Any]], bool], *, what: str
) -> dict[str, Any]:
    """有界轮询真实 PG 的 Schedule 行（Scheduler 轮询拍点默认 10s）。"""
    deadline = time.monotonic() + 3 * SCHEDULER_POLL_SEC + 30.0
    row = _read_schedule(schedule_id)
    while time.monotonic() < deadline:
        assert row is not None, f"PG 中不存在 schedule {schedule_id}"
        if predicate(row):
            return row
        time.sleep(POLL_INTERVAL_SEC)
        row = _read_schedule(schedule_id)
    raise AssertionError(f"schedule {schedule_id} 未在窗口内{what}，最后盘面：{row}")


def _miss_fire(schedule_id: uuid.UUID, *, ago: timedelta) -> None:
    """把 `next_fire_at` 推回到过去（真实列写入）：制造「错过触发」。"""

    async def update(factory: Any) -> None:
        async with factory() as session:
            await session.execute(
                sa.text(
                    "UPDATE task.task_schedule SET next_fire_at = now() - make_interval(secs => :secs)"
                    " WHERE tenant_id = :t AND id = :id"
                ),
                {"t": TENANT, "id": schedule_id, "secs": ago.total_seconds()},
            )
            await session.commit()

    run_db(update)


def _schedule_tasks(schedule_id: uuid.UUID) -> list[dict[str, Any]]:
    """该 Schedule 真实创建出来的 Task（补发与否的判据）。"""

    async def query(factory: Any) -> list[dict[str, Any]]:
        async with factory() as session:
            rows = (
                await session.execute(
                    sa.text(
                        "SELECT id, status, task_type, trigger_type, attempt FROM task.task_execution"
                        " WHERE tenant_id = :t AND schedule_id = :id ORDER BY create_time"
                    ),
                    {"t": TENANT, "id": schedule_id},
                )
            ).all()
        return [
            {"id": row[0], "status": row[1], "task_type": row[2], "trigger_type": row[3]}
            for row in rows
        ]

    return cast(list[dict[str, Any]], run_db(query))


def _metric_value(worker_url: str, name: str) -> float:
    """真实 `GET /metrics`（Prometheus 文本）里的指标总和：计数可见性取自真实进程出口。"""
    response = httpx.get(f"{worker_url}/metrics", timeout=5.0)
    assert response.status_code == 200, response.text
    pattern = re.compile(rf"^{re.escape(name)}(?:\{{[^}}]*\}})?\s+([0-9eE+.\-]+)$")
    total = 0.0
    for line in response.text.splitlines():
        match = pattern.match(line.strip())
        if match:
            total += float(match.group(1))
    return total


def test_b03_schedule_misfire_skip_and_once_terminal(
    live_stack: DfxStack, http: httpx.Client
) -> None:
    """[B-03] misfire 只 SKIP 不补发并计数；ONCE 成功后 COMPLETED、completed_at 非空、next_fire_at 为空。"""
    misfire_before = _metric_value(live_stack.worker_url, MISFIRE_METRIC)

    cron = _create_schedule(
        http,
        live_stack,
        name=f"dfx-recovery-cron-{uuid.uuid4().hex[:8]}",
        schedule={"type": "CRON", "cron": "*/5 * * * *", "timezone": "UTC"},
        route=SCHEDULE_ROUTE,
    )
    cron_id = uuid.UUID(str(cron["schedule_id"]))
    _miss_fire(cron_id, ago=timedelta(days=1))
    skipped = _await_schedule(
        cron_id, lambda row: row["last_error_code"] == SKIP_MISFIRE, what="记下错过触发"
    )
    assert skipped["status"] == "ACTIVE", "CRON 跳过不是终态"
    assert skipped["last_skipped_at"] is not None and skipped["last_error_message"]
    assert skipped["next_fire_at"] is not None and skipped["next_fire_at"] > datetime.now(UTC)
    assert skipped["last_fire_at"] is None
    assert _schedule_tasks(cron_id) == [], "错过触发不得补发 Task"
    assert _metric_value(live_stack.worker_url, MISFIRE_METRIC) == misfire_before + 1, (
        "跳过必须累加 scheduled_misfire_total"
    )

    run_at = datetime.now(UTC) - timedelta(seconds=2)
    once = _create_schedule(
        http,
        live_stack,
        name=f"dfx-recovery-once-{uuid.uuid4().hex[:8]}",
        schedule={"type": "ONCE", "run_at": run_at.isoformat(), "timezone": "UTC"},
        route=SCHEDULE_ROUTE,
    )
    once_id = uuid.UUID(str(once["schedule_id"]))
    fired = _await_schedule(once_id, lambda row: row["status"] == "COMPLETED", what="触发完成")
    assert fired["completed_at"] is not None, "ONCE 成功必须有 completed_at"
    assert fired["next_fire_at"] is None, "ONCE 成功后 next_fire_at 必须为空"
    assert fired["last_fire_at"] == run_at and fired["last_error_code"] is None

    tasks = _schedule_tasks(once_id)
    assert len(tasks) == 1, f"ONCE 必须恰好创建一个 Task：{tasks}"
    assert tasks[0]["trigger_type"] == "SCHEDULED" and tasks[0]["task_type"] == "SKILL"

    # 恰好一次：再等一个 Scheduler 拍点，不得补出第二个 Task（终态 Schedule 不再被 claim）。
    quiet_deadline = time.monotonic() + SCHEDULER_POLL_SEC + 5.0
    while time.monotonic() < quiet_deadline:
        assert len(_schedule_tasks(once_id)) == 1
        time.sleep(POLL_INTERVAL_SEC)
    assert _read_schedule(once_id) == fired
