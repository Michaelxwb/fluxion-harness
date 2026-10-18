"""B-233：[integration] Worker 有界任务池及独立回收（design §3.2 / §3.6 / §4.1）。

真实边界（不 mock 存储或租约）：
- 真实 `WorkerLoop`/`TaskClaimer`/任务槽监督业务路径：领取、占槽、收尾、续租、回收全部走真实代码；
- 隔离 PostgreSQL（空库 + `alembic upgrade head`，跑完即 drop）：Task 行/事件/租约只落在本次
  独立库，不触碰共享 dev 库；
- 真实子进程协议探针：`SkillTaskExecutor` + `ScriptSkillExecutor` 拉起真实 Skill 子进程与孙进程
  （失约时验证整组回收）；
- 真实 PG 行事实驱动心跳/回收：`_renew_lease`/`reclaim_expired`，不替换存储或租约。

关键断言（B-233 契约）：
1. 本地有槽才 PG claim：槽满时 `run_pending`/`run_once` **不调用** claim（排队行保持 QUEUED/
   无租约）；槽释放后按同一真实领取路径立即补齐；两实例并发领取不重复拿到同一行；
2. 并发任务独立续租：两个占槽任务各自心跳续租，互不等待；
3. 单任务卡死/异常/取消只占/释放自己的槽：卡死槽不阻塞其它槽收尾、补齐与回收；
4. 回收独立：槽忙碌时 `reclaim_expired` 照常按 PG 事实回收过期租约；异常/取消路径同样释放槽；
5. 失约停止整组子进程：租约失效后真实 Skill 子进程与孙进程都被回收，失约实例不写成功结论，
   回收后可由新持有者接管；
6. claim/reclaim 保持既有父子锁序：池忙碌时回收父/子行不破坏树锁序、父子行都能再领取；
7. 默认并发配置注入：`WorkerLoop` 取 `WORKER_EXECUTION_CONCURRENCY`（settings 口径），装配根
   main 注入并暴露在 `app.state.worker_loop`。
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import subprocess
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from muad_agent_worker.infrastructure import db as worker_db
from muad_agent_worker.infrastructure.db import get_session_factory
from muad_agent_worker.infrastructure.models.task import TaskEvent, TaskExecution
from muad_agent_worker.worker.claimer import TaskClaimer
from muad_agent_worker.worker.executor import SkillTaskExecutor, TaskExecutionError
from muad_agent_worker.worker.service import WorkerLoop
from muad_common import SharedSettings
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_worker.helpers import retry_safe_snapshot

pytestmark = pytest.mark.integration

INSTANCE = "worker-slots-b233"
OTHER_INSTANCE = "worker-slots-other"
WAIT_TIMEOUT_SEC = 15.0
SNAPSHOT_HASH = "sha256:" + "b" * 64
TERMINAL_EVENT_TYPES = ("COMPLETED", "FAILED", "CANCELLED", "DEADLINE_EXCEEDED")


# --------------------------------------------------------------------------- #
# 隔离数据存储：本任务边界
# --------------------------------------------------------------------------- #


@pytest.fixture
async def isolated_worker(
    async_tool_datastore: dict[str, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """隔离 PostgreSQL/Redis 号位 + Worker 进程环境；跑完 drop，不触碰共享 dev 库。"""
    monkeypatch.setenv("DATABASE_URL", async_tool_datastore["database_url"])
    monkeypatch.setenv("REDIS_URL", async_tool_datastore["redis_url"])
    monkeypatch.setenv("ARTIFACT_ROOT", str(tmp_path))
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", "slots-internal-token")
    await worker_db.dispose_engine()
    try:
        yield get_session_factory()
    finally:
        # 退出时清缓存：后续套件按已还原的环境重建 engine，不指向已 drop 的库。
        await worker_db.dispose_engine()


# --------------------------------------------------------------------------- #
# 真实来源：可领取 Task 行 + 现场脚本（不 mock 存储或租约）
# --------------------------------------------------------------------------- #


def _settings(**overrides: Any) -> SharedSettings:
    values: dict[str, Any] = {
        "worker_execution_concurrency": 2,
        "task_heartbeat_sec": 1,
        "task_cancel_check_sec": 1,
    }
    values.update(overrides)
    return SharedSettings(**values)


async def _seed_task(
    factory: async_sessionmaker[AsyncSession], *, now: datetime | None = None, **overrides: Any
) -> TaskExecution:
    moment = now or datetime.now(UTC)
    task_id = uuid.uuid4()
    values: dict[str, Any] = {
        "id": task_id,
        "tenant_id": f"slots-{uuid.uuid4().hex[:12]}",
        "agent_id": uuid.uuid4(),
        "actor_user_id": uuid.uuid4(),
        "intent_key": "slots_probe",
        "skill_id": uuid.uuid4(),
        "skill_artifact_id": uuid.uuid4(),
        "trigger_type": "IMMEDIATE",
        "execution_mode": "ASYNC",
        "task_type": "SKILL",
        "status": "QUEUED",
        "input_json": {"probe": True},
        "execution_snapshot_schema_version": 1,
        "execution_snapshot_json": {"schema_version": 1},
        "snapshot_hash": SNAPSHOT_HASH,
        "idempotency_key": f"slots-{uuid.uuid4()}",
        "priority": 100,
        "attempt": 0,
        "max_attempts": 3,
        "not_before": moment,
        "deadline_at": moment + timedelta(hours=1),
        "delivery_mode": "FINAL_ONLY",
        "delivery_status": "PENDING",
        "delivery_key": f"task:{task_id}:final",
        "delivery_attempts": 0,
    }
    values.update(overrides)
    async with factory() as session, session.begin():
        task = TaskExecution(**values)
        session.add(task)
        await session.flush()
    return task


@dataclass(frozen=True, slots=True)
class _TaskState:
    status: str
    lease_owner: str | None
    lease_until: datetime | None
    attempt: int
    error_code: str | None


async def _state(factory: async_sessionmaker[AsyncSession], task_id: uuid.UUID) -> _TaskState:
    async with factory() as session:
        row = await session.get(TaskExecution, task_id)
        assert row is not None, f"task {task_id} must exist"
        return _TaskState(
            str(row.status), row.lease_owner, row.lease_until, int(row.attempt), row.error_code
        )


async def _leased_by(
    factory: async_sessionmaker[AsyncSession], task_ids: list[uuid.UUID], owner: str
) -> set[uuid.UUID]:
    async with factory() as session:
        rows = await session.scalars(
            select(TaskExecution.id).where(
                TaskExecution.id.in_(task_ids),
                TaskExecution.status == "RUNNING",
                TaskExecution.lease_owner == owner,
            )
        )
        return set(rows)


async def _claim_event_count(factory: async_sessionmaker[AsyncSession], task_id: uuid.UUID) -> int:
    async with factory() as session:
        depth = await session.scalar(
            select(func.count())
            .select_from(TaskEvent)
            .where(TaskEvent.task_id == task_id, TaskEvent.event_type == "CLAIMED")
        )
    return int(depth or 0)


async def _terminal_event_types(
    factory: async_sessionmaker[AsyncSession], task_id: uuid.UUID
) -> list[str]:
    async with factory() as session:
        rows = await session.scalars(
            select(TaskEvent.event_type)
            .where(TaskEvent.task_id == task_id, TaskEvent.event_type.in_(TERMINAL_EVENT_TYPES))
            .order_by(TaskEvent.seq.asc())
        )
    return list(rows)


async def _wait_for(predicate: Callable[[], bool], *, timeout: float = WAIT_TIMEOUT_SEC) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.02)


async def _wait_state(
    factory: async_sessionmaker[AsyncSession],
    task_id: uuid.UUID,
    predicate: Callable[[_TaskState], bool],
    *,
    timeout: float = WAIT_TIMEOUT_SEC,
) -> _TaskState:
    last: _TaskState | None = None
    try:
        async with asyncio.timeout(timeout):
            while True:
                last = await _state(factory, task_id)
                if predicate(last):
                    return last
                await asyncio.sleep(0.05)
    except TimeoutError as exc:
        raise AssertionError(f"task {task_id} never matched; last state={last}") from exc


async def _drain(worker: WorkerLoop) -> None:
    """收尾：放行后等所有槽收尾，再关闭任务池（不留下跨用例的悬挂片段）。"""
    await _wait_for(lambda: worker.execution_count == 0)
    await worker.aclose()


def _process_alive(pid: int) -> bool:
    state = subprocess.run(
        ["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True
    ).stdout.strip()
    return bool(state) and not state.startswith("Z")


# --------------------------------------------------------------------------- #
# 占槽执行器与领取包装：制造卡死/失败/取消片段，不替换存储或租约
# --------------------------------------------------------------------------- #


class _RoutingExecutor:
    """按 task_id 决定执行行为：阻塞等待放行、抛异常或返回指定信封。"""

    def __init__(self) -> None:
        self.gates: dict[uuid.UUID, asyncio.Event] = {}
        self.errors: dict[uuid.UUID, Exception] = {}
        self.envelopes: dict[uuid.UUID, dict[str, Any]] = {}

    def _gate(self, task_id: uuid.UUID) -> asyncio.Event:
        return self.gates.setdefault(task_id, asyncio.Event())

    def release(self, task_id: uuid.UUID) -> None:
        self._gate(task_id).set()

    def release_all(self) -> None:
        for gate in self.gates.values():
            gate.set()

    async def execute(self, task: TaskExecution) -> dict[str, Any]:
        error = self.errors.get(task.id)
        if error is not None:
            raise error
        envelope = self.envelopes.get(task.id)
        if envelope is not None:
            return envelope
        await self._gate(task.id).wait()
        return {"status": "SUCCEEDED", "result": {"ok": True}, "stderr": "", "exit_code": 0}


class _CountingClaimer:
    """真实 `TaskClaimer` 的计数包装：证明「没有本地槽就没有 PG 领取」。"""

    def __init__(self, factory: async_sessionmaker[AsyncSession], settings: SharedSettings) -> None:
        self._inner = TaskClaimer(factory, settings)
        self.calls: list[str] = []

    async def claim_one(self, instance_id: str, *, now: datetime | None = None) -> TaskExecution | None:
        self.calls.append(instance_id)
        return await self._inner.claim_one(instance_id, now=now)


class _StaticArtifactResolver:
    """Skill 制品缓存接缝：返回测试现场的真实脚本目录（进程边界仍是真实子进程）。"""

    def __init__(self, ready_dir: Path) -> None:
        self._ready_dir = ready_dir

    async def ensure(self, *, artifact_id: str, storage_key: str, checksum: str) -> Path:
        return self._ready_dir


# --------------------------------------------------------------------------- #
# 1. 本地有槽才 PG claim；槽释放即补齐；两实例不重复领取
# --------------------------------------------------------------------------- #


async def test_b233_no_free_slot_means_no_pg_claim_and_refill_on_release(
    isolated_worker: async_sessionmaker[AsyncSession],
) -> None:
    factory = isolated_worker
    settings = _settings(worker_execution_concurrency=2)
    first, second, third, fourth = [
        await _seed_task(factory, priority=index) for index in (1, 2, 3, 4)
    ]
    executor = _RoutingExecutor()
    claimer = _CountingClaimer(factory, settings)
    worker = WorkerLoop(factory, settings, executor=executor, claimer=claimer, instance_id=INSTANCE)
    # 另一实例槽位更小：只领到一条，验证并发领取不重复拿同一行
    other = WorkerLoop(
        factory,
        _settings(worker_execution_concurrency=1),
        executor=executor,
        instance_id=OTHER_INSTANCE,
    )
    try:
        assert worker.slot_limit == 2, "并发上限取 settings 口径（WORKER_EXECUTION_CONCURRENCY）"
        assert await worker.run_pending() == 2
        assert worker.execution_count == 2
        assert await _leased_by(factory, [item.id for item in (first, second, third, fourth)], INSTANCE) == {
            first.id,
            second.id,
        }
        # 槽已满：继续领取不得触碰 PG（claim 一次都不调用），排队行保持 QUEUED/无租约
        calls_before = len(claimer.calls)
        assert await worker.run_pending() == 0
        assert await worker.run_once() is None
        assert len(claimer.calls) == calls_before, "没有本地槽时不得发起 PG 领取"
        queued = await _state(factory, third.id)
        assert (queued.status, queued.lease_owner) == ("QUEUED", None)
        # 另一实例并发领取：只拿到未被占用的行，不重复领取同一 Task
        assert await other.run_pending() == 1
        assert (await _state(factory, third.id)).lease_owner == OTHER_INSTANCE
        # 槽释放（片段收尾）后按同一真实领取路径立即补齐
        executor.release(first.id)
        await _wait_state(factory, first.id, lambda item: item.status == "COMPLETED")
        await _wait_for(lambda: worker.execution_count == 1)
        assert await worker.run_pending() == 1
        assert (await _state(factory, fourth.id)).lease_owner == INSTANCE
        assert worker.execution_count == 2
        # 无重复领取：每条任务恰好一条 CLAIMED 事件
        for task in (first, second, third, fourth):
            assert await _claim_event_count(factory, task.id) == 1
    finally:
        executor.release_all()
        await _drain(worker)
        await _drain(other)


# --------------------------------------------------------------------------- #
# 2. 并发任务独立续租
# --------------------------------------------------------------------------- #


async def test_b233_concurrent_slots_renew_leases_independently(
    isolated_worker: async_sessionmaker[AsyncSession],
) -> None:
    factory = isolated_worker
    settings = _settings(worker_execution_concurrency=2)
    first = await _seed_task(factory, priority=1)
    second = await _seed_task(factory, priority=2)
    executor = _RoutingExecutor()
    worker = WorkerLoop(factory, settings, executor=executor, instance_id=INSTANCE)
    try:
        assert await worker.run_pending() == 2
        initial = {
            task.id: (await _state(factory, task.id)).lease_until for task in (first, second)
        }
        assert all(value is not None for value in initial.values())
        # 每个槽的心跳独立续租：两个租约都各自推进，不等待另一个槽
        for task in (first, second):
            state = await _wait_state(
                factory,
                task.id,
                lambda item, task_id=task.id: bool(
                    item.lease_until and item.lease_until > initial[task_id]
                ),
            )
            assert state.lease_owner == INSTANCE
        assert worker.execution_count == 2, "续租不依赖执行收尾，两个槽都还在执行"
        executor.release_all()
        await _wait_for(lambda: worker.execution_count == 0)
        for task in (first, second):
            assert (await _state(factory, task.id)).status == "COMPLETED"
    finally:
        executor.release_all()
        await _drain(worker)


# --------------------------------------------------------------------------- #
# 3. 卡死槽不拖累其它槽与独立回收
# --------------------------------------------------------------------------- #


async def test_b233_stuck_slot_does_not_block_other_slots_or_reclaim(
    isolated_worker: async_sessionmaker[AsyncSession],
) -> None:
    factory = isolated_worker
    settings = _settings(worker_execution_concurrency=2)
    stuck = await _seed_task(factory, priority=1)
    quick = await _seed_task(factory, priority=2)
    later = await _seed_task(factory, priority=3)
    executor = _RoutingExecutor()
    worker = WorkerLoop(factory, settings, executor=executor, instance_id=INSTANCE)
    try:
        assert await worker.run_pending() == 2
        executor.release(quick.id)
        await _wait_state(factory, quick.id, lambda item: item.status == "COMPLETED")
        await _wait_for(lambda: worker.execution_count == 1)
        assert (await _state(factory, stuck.id)).status == "RUNNING"
        # 槽忙碌不阻塞独立回收：过期租约按 PG 行事实回收，租约内的行不误伤
        now = datetime.now(UTC)
        dead = await _seed_task(
            factory,
            priority=50,
            status="RUNNING",
            attempt=1,
            lease_owner="dead-worker",
            lease_until=now - timedelta(minutes=1),
            execution_snapshot_json=retry_safe_snapshot(),
        )
        assert await worker.reclaim_expired(now=now) == 1
        assert (await _state(factory, dead.id)).status == "QUEUED"
        assert (await _state(factory, stuck.id)).status == "RUNNING"
        # 释放出的槽立即补位：later 在 stuck 卡死期间照常执行并完成
        assert await worker.run_pending() == 1
        executor.release(later.id)
        await _wait_state(factory, later.id, lambda item: item.status == "COMPLETED")
        assert worker.execution_count == 1, "卡死槽仍只占自己的槽"
        # 放行卡死槽后它照常收尾、释放槽
        executor.release(stuck.id)
        await _wait_state(factory, stuck.id, lambda item: item.status == "COMPLETED")
        await _wait_for(lambda: worker.execution_count == 0)
    finally:
        executor.release_all()
        await _drain(worker)


# --------------------------------------------------------------------------- #
# 4. 异常/取消路径同样释放槽
# --------------------------------------------------------------------------- #


async def test_b233_failure_and_cancel_paths_release_their_slots(
    isolated_worker: async_sessionmaker[AsyncSession],
) -> None:
    factory = isolated_worker
    settings = _settings(worker_execution_concurrency=2)
    failing = await _seed_task(
        factory, priority=1, execution_snapshot_json=retry_safe_snapshot()
    )
    cancelled = await _seed_task(factory, priority=2)
    executor = _RoutingExecutor()
    executor.errors[failing.id] = TaskExecutionError("BOOM", "kaboom")
    executor.envelopes[cancelled.id] = {
        "status": "CANCELLED",
        "result": None,
        "stderr": "",
        "exit_code": 0,
    }
    worker = WorkerLoop(factory, settings, executor=executor, instance_id=INSTANCE)
    try:
        assert await worker.run_pending() == 2
        failed = await _wait_state(
            factory, failing.id, lambda item: item.status == "QUEUED" and item.error_code == "BOOM"
        )
        assert failed.attempt == 1
        assert (await _state(factory, cancelled.id)).status == "CANCELLED"
        await _wait_for(lambda: worker.execution_count == 0), "异常/取消路径同样释放槽"
        # 释放出的槽立即可复用
        third = await _seed_task(factory, priority=3)
        assert await worker.run_pending() == 1
        executor.release(third.id)
        await _wait_state(factory, third.id, lambda item: item.status == "COMPLETED")
        await _wait_for(lambda: worker.execution_count == 0)
    finally:
        executor.release_all()
        await _drain(worker)


# --------------------------------------------------------------------------- #
# 5. 失约停止整组子进程（真实 Skill 子进程 + 孙进程）
# --------------------------------------------------------------------------- #


async def test_b233_lost_lease_stops_skill_process_group_and_releases_slot(
    isolated_worker: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    factory = isolated_worker
    ready = tmp_path / "skill-ready"
    parent_pid_file = tmp_path / "skill.pid"
    descendant_pid_file = tmp_path / "descendant.pid"
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    descendant = tmp_path / "descendant.py"
    descendant.write_text(
        "import os, time\n"
        f"open({str(descendant_pid_file)!r}, 'w').write(str(os.getpid()))\n"
        f"open({str(ready)!r}, 'w').write('ready')\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    (scripts / "main.py").write_text(
        "import os, subprocess, sys, time\n"
        f"open({str(parent_pid_file)!r}, 'w').write(str(os.getpid()))\n"
        f"subprocess.Popen([sys.executable, {str(descendant)!r}])\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    artifact_id = uuid.uuid4()
    task = await _seed_task(
        factory,
        priority=1,
        skill_artifact_id=artifact_id,
        execution_snapshot_json={
            "schema_version": 1,
            "budget": {"retry_safety": "IDEMPOTENT"},
            "skills": [
                {
                    "artifact_id": str(artifact_id),
                    "storage_key": "skills/probe.zip",
                    "checksum": "sha256:" + "a" * 64,
                }
            ],
        },
    )
    settings = _settings(worker_execution_concurrency=1)
    worker = WorkerLoop(
        factory,
        settings,
        executor=SkillTaskExecutor(_StaticArtifactResolver(tmp_path)),
        instance_id=INSTANCE,
    )
    try:
        assert await worker.run_pending() == 1
        await _wait_for(ready.exists)
        descendant_pid = int(descendant_pid_file.read_text())
        parent_pid = int(parent_pid_file.read_text())
        assert _process_alive(descendant_pid), "真实孙进程未起来"
        # 失约：租约过期（心跳再也续不上），本实例仍在本地跑着 Skill
        async with factory() as session, session.begin():
            await session.execute(
                update(TaskExecution)
                .where(TaskExecution.id == task.id)
                .values(lease_until=datetime.now(UTC) - timedelta(minutes=1))
            )
        # 心跳检查点发现失约 → 叫停整组子进程并释放槽
        await _wait_for(lambda: worker.execution_count == 0)
        await _wait_for(lambda: not _process_alive(descendant_pid))
        assert not _process_alive(parent_pid), "Skill 子进程未随组回收"
        state = await _state(factory, task.id)
        assert state.status == "RUNNING", "失约实例不得写成功结论"
        assert state.error_code is None
        assert await _terminal_event_types(factory, task.id) == []
        # 与租约语义衔接：回收后新持有者可以接管同一条任务（attempt 保留）
        other = WorkerLoop(factory, settings, instance_id=OTHER_INSTANCE)
        assert await other.reclaim_expired() >= 1
        requeued = await _state(factory, task.id)
        assert (requeued.status, requeued.attempt) == ("QUEUED", 1)
        claimed = await TaskClaimer(factory, settings).claim_one(
            OTHER_INSTANCE, now=datetime.now(UTC) + timedelta(seconds=6)
        )
        assert claimed is not None and claimed.id == task.id
    finally:
        for pid_file in (parent_pid_file, descendant_pid_file):
            if pid_file.exists():
                with contextlib.suppress(ProcessLookupError, ValueError):
                    os.kill(int(pid_file.read_text()), signal.SIGKILL)
        await worker.aclose()


# --------------------------------------------------------------------------- #
# 6. claim/reclaim 保持既有父子锁序
# --------------------------------------------------------------------------- #


async def test_b233_reclaim_keeps_parent_child_lock_order_while_slots_busy(
    isolated_worker: async_sessionmaker[AsyncSession],
) -> None:
    factory = isolated_worker
    settings = _settings(worker_execution_concurrency=1)
    now = datetime.now(UTC)
    busy = await _seed_task(factory, priority=1)
    parent = await _seed_task(
        factory,
        priority=10,
        status="RUNNING",
        attempt=1,
        lease_owner="dead-worker",
        lease_until=now - timedelta(minutes=1),
        execution_snapshot_json=retry_safe_snapshot(),
    )
    child = await _seed_task(
        factory,
        priority=11,
        status="RUNNING",
        attempt=1,
        parent_id=parent.id,
        root_id=parent.id,
        item_key="child-a",
        lease_owner="dead-worker",
        lease_until=now - timedelta(minutes=1),
        execution_snapshot_json=retry_safe_snapshot(),
    )
    executor = _RoutingExecutor()
    worker = WorkerLoop(factory, settings, executor=executor, instance_id=INSTANCE)
    try:
        assert await worker.run_pending() == 1
        assert worker.execution_count == 1
        # 池忙碌时回收父子树：既有 operation→root→Parent→Child 锁序在 reclaim_expired 内部保持
        assert await worker.reclaim_expired(now=now) == 2
        assert (await _state(factory, parent.id)).status == "QUEUED"
        assert (await _state(factory, child.id)).status == "QUEUED"
        assert (await _state(factory, parent.id)).attempt == 1, "回收不重置 attempt"
        assert (await _state(factory, busy.id)).status == "RUNNING", "不误伤本实例持有的行"
        # 回收出的行不破坏 claim 语义：池释放后父子行都能被真实领取
        executor.release(busy.id)
        await _wait_state(factory, busy.id, lambda item: item.status == "COMPLETED")
        await _wait_for(lambda: worker.execution_count == 0)
        reclaim_now = datetime.now(UTC) + timedelta(seconds=6)
        claimed = await TaskClaimer(factory, settings).claim_one(INSTANCE, now=reclaim_now)
        assert claimed is not None and claimed.id in {parent.id, child.id}
    finally:
        executor.release_all()
        await _drain(worker)


# --------------------------------------------------------------------------- #
# 7. 装配根注入并发预算
# --------------------------------------------------------------------------- #


async def test_b233_main_injects_slot_budget_and_keeps_loops_independent(
    isolated_worker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WORKER_EXECUTION_CONCURRENCY", "3")
    from muad_agent_worker import main

    before = asyncio.all_tasks()
    async with main.lifespan(main.app):
        worker = main.app.state.worker_loop
        assert isinstance(worker, WorkerLoop)
        assert worker.slot_limit == 3, "启动配置注入任务槽预算（§3.7 WORKER_EXECUTION_CONCURRENCY）"
        assert worker.execution_count == 0
    assert worker.slot_limit == 3
    leftover = [task for task in asyncio.all_tasks() - before if not task.done()]
    assert leftover == [], f"存在悬挂后台任务: {leftover}"


# --------------------------------------------------------------------------- #
# 8. 任务池边界：非法预算与杀不掉的片段
# --------------------------------------------------------------------------- #


def test_b233_pool_rejects_invalid_slot_budget() -> None:
    from muad_agent_worker.worker.service import TaskSlotSupervisor

    for invalid in (0, 65, True):
        with pytest.raises(ValueError):
            TaskSlotSupervisor(max_concurrency=invalid)  # type: ignore[arg-type]


async def test_b233_pool_close_reports_slots_that_do_not_stop() -> None:
    """杀不掉的片段让 close 显式失败（不静默吞掉终止错误）。"""
    from muad_agent_worker.worker.service import TaskSlotSupervisor

    pool = TaskSlotSupervisor(max_concurrency=1, close_timeout_sec=0.2)
    stop = asyncio.Event()
    started = asyncio.Event()

    class _DummyTask:
        id = uuid.uuid4()

    async def claim() -> _DummyTask:
        return _DummyTask()

    async def stubborn(_task: Any) -> None:
        started.set()
        while not stop.is_set():
            try:
                await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                continue

    admitted = await pool.claim_and_start(claim, stubborn)
    assert admitted is not None and pool.execution_count == 1
    await asyncio.wait_for(started.wait(), timeout=5)
    with pytest.raises(TimeoutError):
        await pool.close()
    stop.set()
    await _wait_for(lambda: pool.execution_count == 0, timeout=5)
