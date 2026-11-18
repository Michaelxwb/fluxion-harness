"""B-117 / E-03：deadline sweep 由独立 Scheduler 节拍驱动（设计 §3.2.2 / §3.5）。

真实边界：真实 Scheduler 节拍 → 真实 PostgreSQL CAS → 真实投递意图（`task_delivery`）→
按 Revision 的 `delivery_intent` 决定建不建意图。不 mock 持久化，也不把 sweep 绑在串行执行上。
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import sqlalchemy as sa
from muad_agent_worker.infrastructure.models.task import (
    TaskDelivery,
    TaskExecution,
    TaskSchedule,
    TaskScheduleRevision,
)
from muad_agent_worker.metrics import value
from muad_agent_worker.scheduler.service import DeadlineSweeper, SchedulerLoop
from muad_agent_worker.worker.claimer import TaskClaimer
from muad_agent_worker.worker.executor import SkillTaskExecutor
from muad_agent_worker.worker.service import WorkerLoop
from muad_common import SharedSettings
from muad_contracts import DeliveryRouteInput

from agent_worker.conftest import TenantContext
from agent_worker.helpers import fetch_events, fetch_task, persist_task

TASK_DEADLINE_EXCEEDED = "TASK_DEADLINE_EXCEEDED"
DEADLINE_TOTAL = "task_deadline_exceeded_total"


async def _expired_task(tenant: TenantContext, *, status: str, **overrides: Any) -> Any:
    now = datetime.now(UTC)
    values: dict[str, Any] = {
        "status": status,
        "deadline_at": now - timedelta(minutes=1),
        "not_before": now - timedelta(hours=1),
    }
    if status == "RUNNING":
        values.update(
            lease_owner="stuck-worker",
            lease_until=now + timedelta(minutes=5),
            started_at=now - timedelta(minutes=30),
        )
    if status == "WAITING_EXTERNAL":
        values.update(
            lease_owner=None,
            lease_until=None,
            wait_kind="EXTERNAL_POLL",
            wait_payload_json={"external_ref": {"external_task_id": "ext-1"}},
        )
    values.update(overrides)
    return await persist_task(tenant, **values)


async def test_b117_sweep_fails_all_expired_non_terminal_tasks(tenant: TenantContext) -> None:
    """QUEUED/RUNNING/WAITING_EXTERNAL 过期都 CAS FAILED；终态与未到期不动。"""
    queued = await _expired_task(tenant, status="QUEUED")
    running = await _expired_task(tenant, status="RUNNING")
    waiting = await _expired_task(tenant, status="WAITING_EXTERNAL")
    terminal = await _expired_task(tenant, status="COMPLETED", finished_at=datetime.now(UTC))
    future = await persist_task(
        tenant,
        status="QUEUED",
        deadline_at=datetime.now(UTC) + timedelta(hours=1),
    )

    sweeper = DeadlineSweeper(tenant.session_factory, tenant.settings)
    assert await sweeper.sweep(now=datetime.now(UTC)) == 3

    for task in (queued, running, waiting):
        refreshed = await fetch_task(tenant, task.id)
        assert refreshed.status == "FAILED"
        assert refreshed.error_code == TASK_DEADLINE_EXCEEDED
        assert refreshed.finished_at is not None
        assert refreshed.lease_owner is None and refreshed.lease_until is None
        events = await fetch_events(tenant, task.id)
        assert [event.event_type for event in events] == ["DEADLINE_EXCEEDED"]

    assert (await fetch_task(tenant, terminal.id)).status == "COMPLETED"
    assert (await fetch_task(tenant, future.id)).status == "QUEUED"

    # 终态不可覆盖：再次 sweep 不改变任何行
    assert await sweeper.sweep(now=datetime.now(UTC) + timedelta(minutes=30)) == 0
    assert (await fetch_task(tenant, terminal.id)).status == "COMPLETED"


async def test_b117_sweep_is_not_blocked_by_running_skill(tenant: TenantContext) -> None:
    """阻塞中的 Skill 不阻塞 Scheduler 的 sweep 节拍。"""
    blocked = await persist_task(
        tenant,
        status="QUEUED",
        deadline_at=datetime.now(UTC) + timedelta(hours=1),
    )
    expired = await _expired_task(tenant, status="QUEUED")
    release = asyncio.Event()

    class BlockingExecutor:
        async def execute(self, task: Any) -> dict[str, Any]:
            await release.wait()
            return {"status": "SUCCEEDED", "result": {}, "stderr": "", "exit_code": 0}

    worker = WorkerLoop(
        tenant.session_factory, tenant.settings, executor=BlockingExecutor(), instance_id="w-block"
    )
    running = asyncio.create_task(worker.run_once())
    await asyncio.sleep(0.05)

    sweeper = DeadlineSweeper(tenant.session_factory, tenant.settings)
    assert await sweeper.sweep(now=datetime.now(UTC)) == 1
    assert (await fetch_task(tenant, expired.id)).status == "FAILED"

    release.set()
    assert await running == blocked.id
    assert (await fetch_task(tenant, blocked.id)).status == "COMPLETED"


async def test_b117_expired_child_fails_parent_through_fan_in(tenant: TenantContext) -> None:
    """过期的 BATCH Child 走 fan-in 结算 Parent，而不是让 Parent 永久 WAITING。"""
    parent = await persist_task(
        tenant,
        status="WAITING_EXTERNAL",
        wait_kind="BATCH_FANIN",
        task_type="BATCH",
        wait_payload_json={"batch": {"aggregate_mode": "ALL", "concurrency": 1, "item_count": 1}},
        input_json={"items": [{"customer": "A"}]},
        deadline_at=datetime.now(UTC) + timedelta(hours=1),
    )
    child = await _expired_task(
        tenant,
        status="RUNNING",
        parent_id=parent.id,
        root_id=parent.id,
        item_key="item-a",
        task_type="SKILL",
        input_json={"customer": "A"},
    )

    sweeper = DeadlineSweeper(tenant.session_factory, tenant.settings)
    assert await sweeper.sweep(now=datetime.now(UTC)) == 1

    assert (await fetch_task(tenant, child.id)).status == "FAILED"
    refreshed_parent = await fetch_task(tenant, parent.id)
    assert refreshed_parent.status == "FAILED"
    assert refreshed_parent.result_json is not None
    assert refreshed_parent.result_json["total"] == 1
    assert refreshed_parent.result_json["failed"] == 1


async def test_b117_deadline_metric_is_incremented(tenant: TenantContext) -> None:
    before = value(DEADLINE_TOTAL)
    await _expired_task(tenant, status="QUEUED")
    await _expired_task(tenant, status="RUNNING")
    sweeper = DeadlineSweeper(tenant.session_factory, tenant.settings)
    assert await sweeper.sweep(now=datetime.now(UTC)) == 2
    assert value(DEADLINE_TOTAL) == before + 2


async def test_e03_deadline_sweep_cas_failed_and_delivery_by_intent(
    tenant: TenantContext,
) -> None:
    """E-03：sweep CAS FAILED(TASK_DEADLINE_EXCEEDED) 后仍按 Revision 的 `delivery_intent` 建意图。"""
    push_revision_id = await _push_revision(tenant)
    pushed = await _expired_task(tenant, status="RUNNING", schedule_revision_id=push_revision_id)
    silent = await _expired_task(tenant, status="QUEUED")

    sweeper = DeadlineSweeper(tenant.session_factory, tenant.settings)
    assert await sweeper.sweep(now=datetime.now(UTC)) == 2

    for task in (pushed, silent):
        refreshed = await fetch_task(tenant, task.id)
        assert refreshed.status == "FAILED"
        assert refreshed.error_code == TASK_DEADLINE_EXCEEDED

    async with tenant.session_factory() as session:
        deliveries = list(
            (
                await session.scalars(
                    sa.select(TaskDelivery).where(
                        TaskDelivery.execution_id.in_([pushed.id, silent.id])
                    )
                )
            ).all()
        )
    assert len(deliveries) == 1, "只有 PUSH Revision 产生投递意图"
    delivery = deliveries[0]
    assert delivery.execution_id == pushed.id
    assert delivery.delivery_kind == "PUSH" and delivery.status == "PENDING"
    assert delivery.delivery_key == f"execution:{pushed.id}:final"


async def _push_revision(tenant: TenantContext) -> uuid.UUID:
    """种一条 PUSH Revision（含投递 route）：终态事务据此建投递意图。"""
    from muad_agent_worker.application.delivery_routes import upsert_delivery_route

    async with tenant.session_factory() as session, session.begin():
        route_id = await upsert_delivery_route(
            session,
            tenant_id=tenant.tenant_id,
            platform_user_id=uuid.uuid4(),
            route=DeliveryRouteInput(
                channel="WECOM", bot_id="bot-demo", external_user_id="wotv-001"
            ),
        )
        schedule = TaskSchedule(
            id=uuid.uuid4(),
            tenant_id=tenant.tenant_id,
            owner_user_id=uuid.uuid4(),
            agent_id=uuid.uuid4(),
            name="deadline push",
            status="ACTIVE",
            config_version=1,
            created_by=uuid.uuid4(),
        )
        session.add(schedule)
        await session.flush()
        revision = TaskScheduleRevision(
            id=uuid.uuid4(),
            tenant_id=tenant.tenant_id,
            schedule_id=schedule.id,
            revision_no=1,
            mode="REMINDER",
            instruction_raw="提醒我开会",
            normalized_goal="提醒开会",
            recurrence_kind="CRON",
            cron_expr="0 9 * * *",
            timezone="Asia/Shanghai",
            schedule_policy={"concurrency_policy": "QUEUE", "max_concurrency": 1},
            payload_kind="REMINDER", payload_json={"kind": "REMINDER", "text": "开会"},
            delivery_intent="PUSH",
            delivery_route_id=route_id,
            created_by=uuid.uuid4(),
        )
        session.add(revision)
        await session.flush()
        return revision.id


async def test_e03_scheduler_loop_runs_sweep_on_its_own_cadence(tenant: TenantContext) -> None:
    """E-03：独立 Scheduler 节拍按 30s 间隔执行 sweep，且不依赖 Worker 串行执行。"""
    settings = SharedSettings(scheduler_poll_interval_sec=1, task_deadline_sweep_interval_sec=30)
    expired = await _expired_task(tenant, status="QUEUED")
    scheduler = SchedulerLoop(tenant.session_factory, _NoopResolver(), settings)
    t0 = datetime.now(UTC)

    assert await scheduler.sweep_deadlines_if_due(now=t0) == 1
    assert (await fetch_task(tenant, expired.id)).status == "FAILED"

    second = await _expired_task(tenant, status="QUEUED")
    assert await scheduler.sweep_deadlines_if_due(now=t0 + timedelta(seconds=10)) == 0
    assert (await fetch_task(tenant, second.id)).status == "QUEUED"

    assert await scheduler.sweep_deadlines_if_due(now=t0 + timedelta(seconds=31)) == 1
    assert (await fetch_task(tenant, second.id)).status == "FAILED"


class _NoopResolver:
    async def resolve(self, agent_id: Any, actor_user_id: Any, tenant_id: str) -> Any:
        raise AssertionError("sweep 测试不应触发 resolve")


# ---------------------------------------------------- 评审回归（2026-10-06）


class _SlowExecutor:
    """执行得比 deadline 慢，但**会**正常返回结果。"""

    def __init__(self, seconds: float) -> None:
        self._seconds = seconds

    async def execute(self, task: Any) -> dict[str, Any]:
        await asyncio.sleep(self._seconds)
        return {"status": "SUCCEEDED", "result": {"done": True}, "stderr": "", "exit_code": 0}


async def test_expired_task_is_never_claimed(tenant: TenantContext) -> None:
    """过了 deadline 的任务不得被领取（评审 #8）。

    此前 claim 条件只有 status/not_before/cancel：已过期的任务照样能被领走、跑完、写成
    COMPLETED——超时的业务副作用真的发生了，只是稍后被 sweep 判成失败而已。
    """
    await persist_task(tenant, deadline_at=datetime.now(UTC) - timedelta(seconds=1))

    claimer = TaskClaimer(tenant.session_factory, tenant.settings)
    assert await claimer.claim_one("worker-a") is None


async def test_run_past_the_deadline_is_recorded_as_deadline_exceeded(
    tenant: TenantContext,
) -> None:
    """执行跨过 deadline 不得写 COMPLETED（评审 #8）。"""
    task = await persist_task(tenant, deadline_at=datetime.now(UTC) + timedelta(seconds=0.3))
    worker = WorkerLoop(
        tenant.session_factory,
        tenant.settings,
        executor=_SlowExecutor(0.8),
        instance_id="worker-a",
    )

    assert await worker.run_once() == task.id

    refreshed = await fetch_task(tenant, task.id)
    assert refreshed.status == "FAILED"
    assert refreshed.error_code == TASK_DEADLINE_EXCEEDED
    assert refreshed.result_json is None, "超时的结果不得被当成成功结论"


class _HaltingExecutor:
    """一直不返回的执行器：只有被叫停才会结束（模拟跑到期限还没跑完的 Agentic 执行）。"""

    def __init__(self) -> None:
        self.stopped = False

    async def execute(self, task: Any) -> dict[str, Any]:
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            self.stopped = True
            raise
        return {"status": "SUCCEEDED", "result": {"done": True}, "stderr": "", "exit_code": 0}


async def test_heartbeat_stops_a_run_that_crosses_the_deadline(tenant: TenantContext) -> None:
    """心跳侧也执行绝对期限：跑过 deadline 的本地执行必须被叫停，并以 `TASK_DEADLINE_EXCEEDED` 收尾。

    只靠"执行结束后再看一眼 deadline"是不够的：一个永不返回的 Agentic 执行会把 Worker 槽一直
    占在期限之外，外部副作用也一直在发生。
    """
    task = await persist_task(tenant, deadline_at=datetime.now(UTC) + timedelta(seconds=1.5))
    executor = _HaltingExecutor()
    settings = SharedSettings(
        task_heartbeat_sec=1, task_cancel_check_sec=1, worker_poll_interval_sec=1
    )
    worker = WorkerLoop(
        tenant.session_factory, settings, executor=executor, instance_id="worker-a"
    )

    assert await worker.run_once() == task.id

    assert executor.stopped is True, "跨过期限的本地执行没有被叫停"
    refreshed = await fetch_task(tenant, task.id)
    assert refreshed.status == "FAILED"
    assert refreshed.error_code == TASK_DEADLINE_EXCEEDED
    assert refreshed.result_json is None


async def test_skill_timeout_is_clamped_to_the_remaining_deadline(
    tenant: TenantContext,
) -> None:
    """Skill 超时 = min(单次上限, 到 deadline 的剩余时间)（评审 #8）。"""
    task = await persist_task(tenant, deadline_at=datetime.now(UTC) + timedelta(seconds=30))
    executor = SkillTaskExecutor(cast("Any", None))

    remaining = executor._execution_timeout(task)

    assert 25 <= remaining <= 30, remaining


async def test_reclaim_does_not_requeue_a_task_past_its_deadline(tenant: TenantContext) -> None:
    """失约回收也是领取：已过 deadline 的执行不得被重排成可执行，直接 `FAILED(TASK_DEADLINE_EXCEEDED)`。"""
    now = datetime.now(UTC)
    task = await persist_task(
        tenant,
        status="RUNNING",
        attempt=1,
        lease_owner="dead-worker",
        lease_until=now - timedelta(minutes=1),
        deadline_at=now - timedelta(seconds=30),
        started_at=now - timedelta(minutes=30),
    )
    worker = WorkerLoop(tenant.session_factory, tenant.settings, instance_id="worker-b")

    assert await worker.reclaim_expired() == 0, "过期执行不得回到队列"

    refreshed = await fetch_task(tenant, task.id)
    assert refreshed.status == "FAILED"
    assert refreshed.error_code == TASK_DEADLINE_EXCEEDED
    events = await fetch_events(tenant, task.id)
    assert [event.event_type for event in events] == ["DEADLINE_EXCEEDED"]


class _CancelDuringExecution:
    """执行期间用户请求取消，同时绝对期限也被跨过（真实的竞态形状）。"""

    def __init__(self, tenant: TenantContext) -> None:
        self._tenant = tenant

    async def execute(self, task: Any) -> dict[str, Any]:
        async with self._tenant.session_factory() as session:
            await session.execute(
                sa.update(TaskExecution)
                .where(TaskExecution.id == task.id)
                .values(
                    cancel_requested=True,
                    deadline_at=datetime.now(UTC) - timedelta(seconds=1),
                )
            )
            await session.commit()
        return {"status": "SUCCEEDED", "result": {"ok": True}, "stderr": "", "exit_code": 0}


async def test_explicit_cancel_wins_over_a_crossed_deadline(tenant: TenantContext) -> None:
    """显式取消优先于期限：取消已请求时，迟到的成功结果写 CANCELLED，不改写成 COMPLETED。"""
    task = await persist_task(tenant)
    worker = WorkerLoop(
        tenant.session_factory,
        tenant.settings,
        executor=_CancelDuringExecution(tenant),
        instance_id="worker-a",
    )

    assert await worker.run_once() == task.id

    refreshed = await fetch_task(tenant, task.id)
    assert refreshed.status == "CANCELLED"
    assert refreshed.result_json is None, "取消后不得把结果当成成功结论"
