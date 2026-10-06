"""claim / heartbeat / reclaim 的租约语义（E-01 / B-111 / RULE-worker-001）。

真实边界：双 Worker → 真实 PostgreSQL 行锁与 CAS。不 mock 数据库。
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from muad_agent_worker.infrastructure.models.task import TaskExecution
from muad_agent_worker.metrics import TASK_LEASE_EXPIRED_METRIC, value
from muad_agent_worker.worker import service as worker_service
from muad_agent_worker.worker.claimer import TaskClaimer
from muad_agent_worker.worker.service import TASK_ATTEMPTS_EXHAUSTED, WorkerLoop
from muad_common import SharedSettings

from agent_worker.conftest import TenantContext
from agent_worker.helpers import fetch_events, persist_task


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
                    TaskExecution.error_code,
                ).where(TaskExecution.id == task_id)
            )
        ).one()
    return {
        "status": row[0],
        "lease_owner": row[1],
        "lease_until": row[2],
        "attempt": row[3],
        "error_code": row[4],
    }


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
    # 回收带退避（评审 #13）：attempt=2 ⇒ 5 * 2**1 = 10s 之后才可再领。
    re_claimed = await claimer.claim_one("worker-b", now=_now() + timedelta(seconds=11))
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

    claimed = await TaskClaimer(tenant.session_factory, tenant.settings).claim_one(
        "worker-b", now=_now() + timedelta(seconds=11)
    )
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

    # 回收带退避（评审 #13）：attempt=1 ⇒ 5 * 2**0 = 5s。
    re_claimed = await supervisor._claimer.claim_one("worker-b", now=_now() + timedelta(seconds=6))
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


# ---------------------------------------------------- 评审回归（2026-10-06）


async def test_expired_lease_cannot_be_renewed(tenant: TenantContext) -> None:
    """已经失约的实例不能靠心跳把执行权续到未来（评审 #2）。

    续租条件此前只看 status/owner、不看 `lease_until`：进程或 DB 卡顿超过租期之后，一次
    心跳就能"复活"已经失效的租约，让旧执行的结果随后通过终态 CAS 写成 COMPLETED。
    """
    task = await persist_task(
        tenant,
        status="RUNNING",
        attempt=1,
        lease_owner="worker-a",
        lease_until=_now() - timedelta(minutes=1),
    )
    worker = WorkerLoop(tenant.session_factory, tenant.settings, instance_id="worker-a")

    owned, _requested = await worker._renew_lease(task.id)

    assert owned is False, "过期租约不得被续租"
    assert (await _status(tenant, task.id))["lease_until"] < _now(), "续租把失效的执行权写回了未来"


async def test_reclaim_gives_up_after_max_attempts(tenant: TenantContext) -> None:
    """回收也要过重试预算：预算耗尽的崩溃任务终态失败，不再无限重启（评审 #13）。

    此前 reclaim 无条件把行置回 QUEUED，而 claim 只递增 attempt、从不看上限——一个反复崩溃
    的任务可以在 deadline 之前无限重启，反复触发外部副作用。
    """
    task = await persist_task(
        tenant,
        status="RUNNING",
        attempt=1,
        max_attempts=1,
        lease_owner="dead-worker",
        lease_until=_now() - timedelta(minutes=1),
    )
    worker = WorkerLoop(tenant.session_factory, tenant.settings, instance_id="worker-b")
    expired_before = value(TASK_LEASE_EXPIRED_METRIC)

    assert await worker.reclaim_expired() == 0, "预算耗尽的行不再回队列"

    assert value(TASK_LEASE_EXPIRED_METRIC) == expired_before + 1, (
        "预算耗尽而终态失败的也是一条被观察到的过期租约，不能从指标面消失"
    )
    state = await _status(tenant, task.id)
    assert state["status"] == "FAILED"
    assert state["error_code"] == TASK_ATTEMPTS_EXHAUSTED
    events = await fetch_events(tenant, task.id)
    assert [event.event_type for event in events] == ["FAILED"]


async def test_reclaim_backs_off_before_the_row_is_claimable_again(
    tenant: TenantContext,
) -> None:
    """回收带退避：反复崩溃的任务不会被立刻再次领走（评审 #13）。"""
    task = await persist_task(
        tenant,
        status="RUNNING",
        attempt=2,
        lease_owner="dead-worker",
        lease_until=_now() - timedelta(minutes=1),
    )
    worker = WorkerLoop(tenant.session_factory, tenant.settings, instance_id="worker-b")
    assert await worker.reclaim_expired() == 1
    state = await _status(tenant, task.id)
    assert state["status"] == "QUEUED"
    assert state["attempt"] == 2, "回收不重置 attempt"

    claimer = TaskClaimer(tenant.session_factory, tenant.settings)
    assert await claimer.claim_one("worker-b") is None, "退避期内不得再领"
    assert (
        await claimer.claim_one("worker-b", now=_now() + timedelta(seconds=11))
    ) is not None, "退避过后应能重领"


class _BlockingExecutor:
    """一直不返回的执行器，直到被取消——模拟「心跳死了，但 Skill 还在跑」。"""

    def __init__(self) -> None:
        self.stopped = False

    async def execute(self, task: TaskExecution) -> dict[str, Any]:
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            self.stopped = True
            raise
        return {"status": "SUCCEEDED", "result": {"ok": True}}


async def test_heartbeat_failure_stops_the_local_execution(
    tenant: TenantContext, monkeypatch: Any
) -> None:
    """心跳退出就必须叫停本地执行（评审 #3）。

    此前 `run_once` 只 `await execution`，心跳任务因 DB 错误死掉没人管：执行器继续跑完并
    产生外部副作用，直到 `_stop_heartbeat` 才把异常读出来——而终态 CAS 拦得住写库，
    拦不住已经发生的事。
    """
    monkeypatch.setattr(worker_service, "HEARTBEAT_RENEW_RETRY_SEC", 0.01)
    task = await persist_task(tenant)
    executor = _BlockingExecutor()
    settings = SharedSettings(
        task_heartbeat_sec=1, task_cancel_check_sec=1, worker_poll_interval_sec=1
    )
    worker = WorkerLoop(
        tenant.session_factory, settings, executor=executor, instance_id="worker-a"
    )

    async def failing_renew(task_id: uuid.UUID) -> tuple[bool, bool]:
        raise RuntimeError("database is down")

    monkeypatch.setattr(worker, "_renew_lease", failing_renew)

    assert await worker.run_once() == task.id

    assert executor.stopped is True, "租约失联后本地执行仍在跑"
    state = await _status(tenant, task.id)
    assert state["status"] == "QUEUED", "失联应按可重试失败退回队列，而不是留在 RUNNING"
