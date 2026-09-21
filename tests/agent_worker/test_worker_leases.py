"""claim / heartbeat / reclaim 的租约语义（E-01 / B-111 / RULE-worker-001）。

真实边界：双 Worker → 真实 PostgreSQL 行锁与 CAS。不 mock 数据库。
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from conftest import TenantContext
from helpers import persist_task
from muad_agent_worker.infrastructure.models.task import TaskExecution
from muad_agent_worker.worker.claimer import TaskClaimer
from muad_agent_worker.worker.service import WorkerLoop


def _now() -> datetime:
    return datetime.now(UTC)


async def _status(tenant: TenantContext, task_id: uuid.UUID) -> dict[str, Any]:
    async with tenant.session_factory() as session:
        row = (
            await session.execute(
                sa.select(
                    TaskExecution.status,
                    TaskExecution.lease_owner,
                    TaskExecution.lease_until,
                    TaskExecution.attempt,
                ).where(TaskExecution.id == task_id)
            )
        ).one()
    return {"status": row[0], "lease_owner": row[1], "lease_until": row[2], "attempt": row[3]}


async def test_only_one_worker_holds_the_lease(tenant: TenantContext) -> None:
    """两个 Worker 并发 claim 同一 Task，只有一个拿到租约。"""
    task = await persist_task(tenant)
    claimer = TaskClaimer(tenant.session_factory, tenant.settings)

    results = await asyncio.gather(
        claimer.claim_one("worker-a"),
        claimer.claim_one("worker-b"),
        return_exceptions=True,
    )

    claimed = [item for item in results if isinstance(item, TaskExecution)]
    assert len(claimed) == 1, f"同一 Task 被 {len(claimed)} 个 Worker 同时持有"
    state = await _status(tenant, task.id)
    assert state["status"] == "RUNNING"
    assert state["lease_owner"] in {"worker-a", "worker-b"}


async def test_not_before_and_cancelled_are_not_claimable(tenant: TenantContext) -> None:
    """未到 not_before、已请求取消的 Task 都不可 claim。"""
    claimer = TaskClaimer(tenant.session_factory, tenant.settings)
    future = await persist_task(tenant, not_before=_now() + timedelta(hours=1))
    cancelled = await persist_task(tenant, cancel_requested=True)

    assert await claimer.claim_one("worker-a") is None
    assert (await _status(tenant, future.id))["status"] == "QUEUED"
    assert (await _status(tenant, cancelled.id))["status"] == "QUEUED"


async def test_waiting_tasks_are_not_reclaimed(tenant: TenantContext) -> None:
    """WAITING 是主动让出 lease，不该被 reclaim 抢回。"""
    waiting = await persist_task(
        tenant,
        status="WAITING",
        lease_owner=None,
        lease_until=None,
        not_before=_now() + timedelta(hours=1),
    )
    worker = WorkerLoop(tenant.session_factory, tenant.settings, instance_id="worker-a")

    reclaimed = await worker.reclaim_expired()

    assert reclaimed == 0
    assert (await _status(tenant, waiting.id))["status"] == "WAITING"


async def test_reclaim_preserves_attempt_and_clears_lease(tenant: TenantContext) -> None:
    """crash 的 RUNNING Task 被回收：attempt 保留，lease 清空，可再次 claim。"""
    task = await persist_task(
        tenant,
        status="RUNNING",
        attempt=2,
        lease_owner="dead-worker",
        lease_until=_now() - timedelta(minutes=1),
    )
    worker = WorkerLoop(tenant.session_factory, tenant.settings, instance_id="worker-a")

    reclaimed = await worker.reclaim_expired()

    assert reclaimed == 1
    state = await _status(tenant, task.id)
    assert state["status"] == "QUEUED"
    assert state["attempt"] == 2, "reclaim 不应重置 attempt"
    assert state["lease_owner"] is None

    claimer = TaskClaimer(tenant.session_factory, tenant.settings)
    re_claimed = await claimer.claim_one("worker-b")
    assert re_claimed is not None and re_claimed.id == task.id
    assert (await _status(tenant, task.id))["lease_owner"] == "worker-b"


async def test_reclaimed_task_rejects_previous_owner(tenant: TenantContext) -> None:
    """被回收后，旧持有者不能再写终态。"""
    task = await persist_task(
        tenant,
        status="RUNNING",
        lease_owner="worker-a",
        lease_until=_now() - timedelta(minutes=1),
    )
    worker_a = WorkerLoop(tenant.session_factory, tenant.settings, instance_id="worker-a")
    await worker_a.reclaim_expired()

    claimed = await TaskClaimer(tenant.session_factory, tenant.settings).claim_one("worker-b")
    assert claimed is not None and claimed.id == task.id

    # worker-a 拿着过期的内存对象尝试写完成
    await worker_a._handle_success(task, {"ok": True}, now=_now())

    state = await _status(tenant, task.id)
    assert state["status"] == "RUNNING", "旧持有者覆写了新持有者的 Task"
    assert state["lease_owner"] == "worker-b"


class _ExpireLeaseDuringExecution:
    """执行期间让租约过期（模拟心跳没能续上），随后正常返回结果。"""

    def __init__(self, tenant: TenantContext) -> None:
        self._tenant = tenant

    async def execute(self, task: TaskExecution) -> dict[str, Any]:
        async with self._tenant.session_factory() as session:
            await session.execute(
                sa.update(TaskExecution)
                .where(TaskExecution.id == task.id)
                .values(lease_until=_now() - timedelta(minutes=1))
            )
            await session.commit()
        return {"ok": True}


async def test_expired_lease_owner_cannot_write_terminal_state(
    tenant: TenantContext,
) -> None:
    """失约（lease 已过期但尚未被回收）的 Worker 不能写终态。"""
    await persist_task(tenant)
    worker = WorkerLoop(
        tenant.session_factory,
        tenant.settings,
        executor=_ExpireLeaseDuringExecution(tenant),
        instance_id="worker-a",
    )

    task_id = await worker.run_once()

    assert task_id is not None
    state = await _status(tenant, task_id)
    assert state["status"] == "RUNNING", "租约失效后仍写入了终态"
    assert state["lease_owner"] == "worker-a"


async def test_e01_crash_recovery_across_instances(tenant: TenantContext) -> None:
    """E-01：Worker crash 失约后由另一实例接管，attempt 保留且旧持有者不能覆写。"""
    task = await persist_task(tenant)
    crashed = WorkerLoop(tenant.session_factory, tenant.settings, instance_id="worker-a")
    claimed = await crashed._claimer.claim_one("worker-a")
    assert claimed is not None and claimed.id == task.id
    assert (await _status(tenant, task.id))["attempt"] == 1

    # a 失约（心跳断了），b 回收并接管
    async with tenant.session_factory() as session:
        await session.execute(
            sa.update(TaskExecution)
            .where(TaskExecution.id == task.id)
            .values(lease_until=_now() - timedelta(minutes=1))
        )
        await session.commit()
    supervisor = WorkerLoop(tenant.session_factory, tenant.settings, instance_id="worker-b")
    assert await supervisor.reclaim_expired() == 1

    re_claimed = await supervisor._claimer.claim_one("worker-b")
    assert re_claimed is not None and re_claimed.id == task.id
    state = await _status(tenant, task.id)
    assert state["attempt"] == 2, "重试计数必须跨实例保留"
    assert state["lease_owner"] == "worker-b"

    # 旧持有者拿着过期结果尝试写完成 —— 必须失败
    await crashed._handle_success(task, {"stale": True}, now=_now())
    state = await _status(tenant, task.id)
    assert state["status"] == "RUNNING"
    async with tenant.session_factory() as session:
        result = (
            await session.execute(
                sa.text("SELECT result_json FROM task.task_execution WHERE id = :id"),
                {"id": task.id},
            )
        ).scalar()
    assert result is None, "旧持有者的过期结果被写了进去"

    # 新持有者正常完成
    await supervisor._handle_success(re_claimed, {"fresh": True}, now=_now())
    state = await _status(tenant, task.id)
    assert state["status"] == "COMPLETED"
