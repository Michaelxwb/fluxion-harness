"""B-119：原子 fan-in 与 Parent 终态（设计 §3.2.4）。

真实边界：真实 Worker 终态事务（CAS + 事件）→ 真实 PostgreSQL 兄弟终态计数 →
同事务 CAS Parent。不依赖 Redis 唤醒顺序，不 mock 持久化。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
import uuid
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import sqlalchemy as sa
from muad_agent_worker.application.batch_fanin import BATCH_CHILD_FAILED, settle_child
from muad_agent_worker.application.batch_fanout import (
    AGGREGATE_ALL,
    AGGREGATE_BEST_EFFORT,
    PARKED_NOT_BEFORE,
    BatchFanoutService,
)
from muad_agent_worker.infrastructure.models.task import TaskEvent, TaskExecution
from muad_agent_worker.worker.executor import SkillTaskExecutor, TaskExecutionError
from muad_agent_worker.worker.service import WorkerLoop
from muad_artifact_store import NfsArtifactStore, SkillArtifactCache
from sqlalchemy import func, select

from agent_worker.conftest import TenantContext
from agent_worker.helpers import fetch_task, persist_task

STORAGE_KEY = "skills/batch_fanin.zip"
ARTIFACT_ID = "7f4a3c1e-0000-4000-8000-000000000003"
ARTIFACT_UUID = uuid.UUID(ARTIFACT_ID)


def _plan_script(customers: tuple[str, ...], mode: str, concurrency: int) -> str:
    plan = {
        "items": [{"customer": customer} for customer in customers],
        "max_concurrency": concurrency,
        "aggregate_mode": mode,
    }
    return (
        "import json, sys\n"
        "json.loads(sys.stdin.read() or '{}')\n"
        f"print(json.dumps({{'batch': {json.dumps(plan)}}}))\n"
    )


def _build_skill_zip(root: Path, body: str) -> str:
    with tempfile.TemporaryDirectory() as staging:
        package = Path(staging)
        (package / "scripts").mkdir()
        (package / "scripts" / "main.py").write_text(body, encoding="utf-8")
        archive_path = root / STORAGE_KEY
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.write(package / "scripts" / "main.py", "scripts/main.py")
    return "sha256:" + hashlib.sha256((root / STORAGE_KEY).read_bytes()).hexdigest()


def _snapshot(checksum: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "agent": {"key": "agent"},
        "model": {"key": "model"},
        "skills": [
            {
                "skill_id": "skill-1",
                "artifact_id": ARTIFACT_ID,
                "key": "policy_check",
                "version": "1",
                "checksum": checksum,
                "storage_key": STORAGE_KEY,
                "execution_mode": "ASYNC",
            }
        ],
        "mcp": [],
        "prompt_template_version": "v1",
        "budget": {"max_tokens": 1024},
    }


class _ChildExecutor:
    """Parent 走真实 Skill 执行返回 plan；Child 直接给终态，避免子进程退避拖慢用例。"""

    def __init__(
        self,
        real: SkillTaskExecutor,
        *,
        fail_customers: tuple[str, ...] = (),
        gate: asyncio.Barrier | None = None,
    ) -> None:
        self._real = real
        self._fail = set(fail_customers)
        self._gate = gate

    async def execute(self, task: TaskExecution) -> dict[str, object]:
        if task.parent_id is None:
            return await self._real.execute(task)
        if self._gate is not None:
            await self._gate.wait()
        customer = (task.input_json or {}).get("customer")
        if customer in self._fail:
            raise TaskExecutionError("SKILL_EXECUTION_FAILED", f"child {customer} failed")
        return {
            "status": "SUCCEEDED",
            "result": {"checked": customer},
            "stderr": "",
            "exit_code": 0,
        }


async def _stack(
    tenant: TenantContext,
    *,
    customers: tuple[str, ...],
    mode: str = AGGREGATE_ALL,
    concurrency: int = 2,
) -> tuple[tempfile.TemporaryDirectory[str], SkillArtifactCache, TaskExecution]:
    tmpdir = tempfile.TemporaryDirectory()
    tmp = Path(tmpdir.name)
    store = NfsArtifactStore(tmp / "nfs")
    store.root.mkdir(parents=True, exist_ok=True)
    cache = SkillArtifactCache(store, tmp / "cache")
    checksum = _build_skill_zip(store.root, _plan_script(customers, mode, concurrency))
    parent = await persist_task(
        tenant,
        skill_artifact_id=ARTIFACT_UUID,
        execution_snapshot_json=_snapshot(checksum),
        input_json={"customers": list(customers)},
        max_attempts=1,
    )
    return tmpdir, cache, parent


def _worker(
    tenant: TenantContext,
    cache: SkillArtifactCache,
    *,
    instance_id: str,
    fail_customers: tuple[str, ...] = (),
    gate: asyncio.Barrier | None = None,
) -> WorkerLoop:
    executor = _ChildExecutor(
        SkillTaskExecutor(cache, fanout=BatchFanoutService(tenant.session_factory, tenant.settings)),
        fail_customers=fail_customers,
        gate=gate,
    )
    return WorkerLoop(tenant.session_factory, tenant.settings, executor=executor, instance_id=instance_id)


async def _children(tenant: TenantContext, parent_id: uuid.UUID) -> list[TaskExecution]:
    async with tenant.session_factory() as session:
        return list(
            (
                await session.execute(
                    select(TaskExecution)
                    .where(TaskExecution.parent_id == parent_id)
                    .order_by(TaskExecution.item_key.asc())
                )
            )
            .scalars()
            .all()
        )


async def _claimable_count(tenant: TenantContext, parent_id: uuid.UUID, moment: datetime) -> int:
    async with tenant.session_factory() as session:
        return int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(TaskExecution)
                    .where(
                        TaskExecution.parent_id == parent_id,
                        TaskExecution.status == "QUEUED",
                        TaskExecution.not_before <= moment,
                    )
                )
            ).scalar_one()
        )


async def _fan_in_events(tenant: TenantContext, parent_id: uuid.UUID) -> int:
    async with tenant.session_factory() as session:
        return int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(TaskEvent)
                    .where(TaskEvent.task_id == parent_id, TaskEvent.event_type == "FAN_IN")
                )
            ).scalar_one()
        )


async def _drain(worker: WorkerLoop, *, now: datetime, limit: int = 12) -> list[uuid.UUID]:
    ran: list[uuid.UUID] = []
    moment = now
    for _ in range(limit):
        task_id = await worker.run_once(now=moment)
        if task_id is None:
            break
        ran.append(task_id)
        moment += timedelta(seconds=1)
    return ran


async def test_b119_partial_terminal_keeps_parent_waiting_and_releases_next_child(
    tenant: TenantContext,
) -> None:
    """未全部终态不提前完成；一个 Child 终态释放一个停放 Child。"""
    tmpdir, cache, parent = await _stack(
        tenant, customers=("A", "B", "C", "D"), mode=AGGREGATE_ALL, concurrency=2
    )
    worker = _worker(tenant, cache, instance_id="w-partial")
    now = datetime.now(UTC)

    assert await worker.run_once(now=now) == parent.id
    assert (await fetch_task(tenant, parent.id)).status == "WAITING"
    assert len(await _children(tenant, parent.id)) == 4
    assert await _claimable_count(tenant, parent.id, now + timedelta(seconds=2)) == 2
    assert await _fan_in_events(tenant, parent.id) == 0

    completed = await worker.run_once(now=now + timedelta(seconds=1))
    assert completed is not None and completed != parent.id

    refreshed = await fetch_task(tenant, parent.id)
    assert refreshed.status == "WAITING", "未全部终态不得提前完成"
    assert refreshed.result_json is None
    assert await _fan_in_events(tenant, parent.id) == 0
    assert await _claimable_count(tenant, parent.id, now + timedelta(seconds=2)) == 2, (
        "一个 Child 终态后应释放一个停放 Child"
    )
    parked = [
        child
        for child in await _children(tenant, parent.id)
        if child.not_before == PARKED_NOT_BEFORE
    ]
    assert len(parked) == 1
    tmpdir.cleanup()


async def test_b119_all_mode_propagates_child_failure(tenant: TenantContext) -> None:
    """ALL：任一 Child 失败则 Parent FAILED，并带成功/失败统计。"""
    tmpdir, cache, parent = await _stack(
        tenant, customers=("A", "B"), mode=AGGREGATE_ALL, concurrency=2
    )
    worker = _worker(tenant, cache, instance_id="w-all", fail_customers=("B",))
    now = datetime.now(UTC)

    await _drain(worker, now=now)

    refreshed = await fetch_task(tenant, parent.id)
    assert refreshed.status == "FAILED"
    assert refreshed.error_code == BATCH_CHILD_FAILED
    assert refreshed.error_message
    assert refreshed.result_json == {
        "mode": "ALL",
        "total": 2,
        "succeeded": 1,
        "failed": 1,
        "cancelled": 0,
    }
    assert refreshed.finished_at is not None
    assert refreshed.lease_owner is None and refreshed.lease_until is None
    assert await _fan_in_events(tenant, parent.id) == 1
    tmpdir.cleanup()


async def test_b119_best_effort_completes_with_success_and_failure_counts(
    tenant: TenantContext,
) -> None:
    """BEST_EFFORT：所有 Child 终态后 Parent 仍 COMPLETED，结果标注成功/失败数。"""
    tmpdir, cache, parent = await _stack(
        tenant, customers=("A", "B", "C"), mode=AGGREGATE_BEST_EFFORT, concurrency=3
    )
    worker = _worker(tenant, cache, instance_id="w-best", fail_customers=("B",))
    now = datetime.now(UTC)

    await _drain(worker, now=now)

    refreshed = await fetch_task(tenant, parent.id)
    assert refreshed.status == "COMPLETED"
    assert refreshed.error_code is None
    assert refreshed.result_json == {
        "mode": "BEST_EFFORT",
        "total": 3,
        "succeeded": 2,
        "failed": 1,
        "cancelled": 0,
    }
    assert await _fan_in_events(tenant, parent.id) == 1
    tmpdir.cleanup()


async def test_b119_last_two_children_race_aggregates_once(tenant: TenantContext) -> None:
    """最后两个 Child 并发终态：只聚合一次，Parent 终态唯一。"""
    tmpdir, cache, parent = await _stack(
        tenant, customers=("A", "B"), mode=AGGREGATE_ALL, concurrency=2
    )
    gate = asyncio.Barrier(2)
    first = _worker(tenant, cache, instance_id="w-race-1", gate=gate)
    second = _worker(tenant, cache, instance_id="w-race-2", gate=gate)
    now = datetime.now(UTC)

    assert await first.run_once(now=now) == parent.id
    raced = await asyncio.gather(
        first.run_once(now=now + timedelta(seconds=1)),
        second.run_once(now=now + timedelta(seconds=1)),
    )
    assert all(task_id is not None for task_id in raced)

    refreshed = await fetch_task(tenant, parent.id)
    assert refreshed.status == "COMPLETED"
    assert refreshed.result_json == {
        "mode": "ALL",
        "total": 2,
        "succeeded": 2,
        "failed": 0,
        "cancelled": 0,
    }
    assert await _fan_in_events(tenant, parent.id) == 1, "并发终态只允许聚合一次"
    tmpdir.cleanup()


async def test_b119_parent_terminal_is_not_overwritten(tenant: TenantContext) -> None:
    """Parent 已终态后，迟到的 Child 终态不得覆写 Parent。"""
    tmpdir, cache, parent = await _stack(
        tenant, customers=("A", "B"), mode=AGGREGATE_ALL, concurrency=2
    )
    worker = _worker(tenant, cache, instance_id="w-terminal")
    now = datetime.now(UTC)
    await _drain(worker, now=now)
    terminal = await fetch_task(tenant, parent.id)
    assert terminal.status == "COMPLETED"
    events_before = await _fan_in_events(tenant, parent.id)

    children = await _children(tenant, parent.id)
    async with tenant.session_factory() as session:
        async with session.begin():
            child = await session.get(TaskExecution, children[0].id)
            assert child is not None
            outcome = await settle_child(session, child, now + timedelta(minutes=1))

    assert outcome.aggregated is False
    unchanged = await fetch_task(tenant, parent.id)
    assert unchanged.status == terminal.status
    assert unchanged.result_json == terminal.result_json
    assert await _fan_in_events(tenant, parent.id) == events_before
    tmpdir.cleanup()


async def test_b119_release_counts_backoff_and_waiting_children_as_active(
    tenant: TenantContext,
) -> None:
    """重试退避中的 QUEUED、外部等待的 WAITING 都占并发名额；释放时不能把退避 Child 当停放。"""
    now = datetime.now(UTC)
    parent = await persist_task(
        tenant,
        status="WAITING",
        task_type="BATCH",
        not_before=PARKED_NOT_BEFORE,
        external_ref_json={"batch": {"aggregate_mode": AGGREGATE_ALL, "concurrency": 2}},
    )

    async def child(item_key: str, **values: object) -> TaskExecution:
        return await persist_task(
            tenant, parent_id=parent.id, root_id=parent.id, item_key=item_key, **values
        )

    backoff = await child("a", not_before=now + timedelta(minutes=5), attempt=1)
    waiting = await child("b", status="WAITING", not_before=now + timedelta(minutes=5), attempt=1)
    parked = await child("c", not_before=PARKED_NOT_BEFORE)
    finished = await child("d", status="COMPLETED")

    async with tenant.session_factory() as session, session.begin():
        outcome = await settle_child(session, finished, now)

    assert outcome.released == 0, "两个名额都被退避/等待中的 Child 占着"
    assert (await fetch_task(tenant, backoff.id)).not_before == now + timedelta(minutes=5), (
        "退避中的 Child 不得被当成停放提前放行"
    )
    assert (await fetch_task(tenant, waiting.id)).status == "WAITING"
    assert (await fetch_task(tenant, parked.id)).not_before == PARKED_NOT_BEFORE


# ---------------------------------------------------- 评审回归（2026-10-06）


async def test_last_child_finalizes_a_parent_that_was_reclaimed(tenant: TenantContext) -> None:
    """父任务被回收成 QUEUED 时，最后一个 Child 终态仍要聚合（评审 #7）。

    `reclaim` 会把崩溃的父任务置回 QUEUED（或它被重新领取），而 `_cas_parent` 此前只认
    WAITING/RUNNING：此时最后一个 Child 终态无法聚合，父任务又再没有下一个事件可以触发
    fan-in，只能一路停到 deadline。
    """
    parent = await persist_task(
        tenant,
        status="QUEUED",
        task_type="BATCH",
        external_ref_json={
            "batch": {"concurrency": 2, "aggregate_mode": AGGREGATE_ALL, "item_count": 1}
        },
    )
    child = await persist_task(
        tenant,
        parent_id=parent.id,
        status="RUNNING",
        delivery_mode="NONE",
        lease_owner="worker-a",
        lease_until=datetime.now(UTC) + timedelta(minutes=5),
    )
    worker = WorkerLoop(tenant.session_factory, tenant.settings, instance_id="worker-a")

    await worker._handle_success(child, {"ok": True}, now=datetime.now(UTC))

    refreshed = await fetch_task(tenant, parent.id)
    assert refreshed.status == "COMPLETED", "Child 全部终态却没人聚合父任务"
    assert refreshed.result_json == {
        "mode": AGGREGATE_ALL,
        "total": 1,
        "succeeded": 1,
        "failed": 0,
        "cancelled": 0,
    }


async def test_child_completion_takes_the_parent_lock_before_the_child_row(
    tenant: TenantContext,
) -> None:
    """子任务终态与父任务取消的加锁顺序一致，不再互相等成死锁（评审 #6）。

    取消路径先锁 Parent 再动 Child；子任务终态此前先 UPDATE Child（持子锁）再在
    `settle_child` 里锁 Parent —— 两条交错就是 Parent→Child 与 Child→Parent 的循环等待，
    PostgreSQL 实测判死锁并回滚一方。这里用真实并发事务固定那个交错：持父锁的一方随后
    要动 Child 行，若子任务终态事务已经握住 Child 锁，旧实现必然死锁。
    """
    parent = await persist_task(
        tenant,
        status="RUNNING",
        task_type="BATCH",
        lease_owner="worker-a",
        lease_until=datetime.now(UTC) + timedelta(minutes=5),
        external_ref_json={
            "batch": {"concurrency": 2, "aggregate_mode": AGGREGATE_ALL, "item_count": 1}
        },
    )
    child = await persist_task(
        tenant,
        parent_id=parent.id,
        status="RUNNING",
        delivery_mode="NONE",
        lease_owner="worker-a",
        lease_until=datetime.now(UTC) + timedelta(minutes=5),
    )
    worker = WorkerLoop(tenant.session_factory, tenant.settings, instance_id="worker-a")

    async with tenant.session_factory() as blocker:
        async with blocker.begin():
            # 取消路径的第一步：锁住父行
            await blocker.execute(
                sa.select(TaskExecution.id)
                .where(TaskExecution.id == parent.id)
                .with_for_update()
            )
            completing = asyncio.create_task(
                worker._handle_success(child, {"ok": True}, now=datetime.now(UTC))
            )
            await asyncio.sleep(0.3)
            # 取消路径的第二步：动子行。旧实现里子任务终态事务已经握住这一行 → 死锁。
            await blocker.execute(
                sa.update(TaskExecution)
                .where(TaskExecution.id == child.id)
                .values(update_time=datetime.now(UTC))
            )
        await asyncio.wait_for(completing, timeout=10)

    assert (await fetch_task(tenant, child.id)).status == "COMPLETED"
    assert (await fetch_task(tenant, parent.id)).status == "COMPLETED"
