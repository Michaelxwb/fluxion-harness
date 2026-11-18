"""B-05 / B-06 / B-12 / E-03：Schedule 策略下的 claim（FIFO、槽位、三类外部等待、两段式领取）。

真实边界：真实双 Worker → 真实 PostgreSQL 行锁/`SKIP LOCKED`/原子 UPDATE。不 mock 数据库，
也不 mock Schedule 行锁；唯一被替换的是执行器（它返回什么与 claim 无关）。

两套集合必须分开（design §3.5.3）：**阻断集**含 QUEUED（Fire 创建 / `SKIP_IF_RUNNING`），
**占槽集**不含 QUEUED（claim 的 `QUEUE`/`ALLOW_PARALLEL`）——本文件按后者断言。
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from muad_agent_worker.application.batch_fanout import PARKED_NOT_BEFORE
from muad_agent_worker.infrastructure.models.task import (
    TaskExecution,
    TaskSchedule,
    TaskScheduleRevision,
)
from muad_agent_worker.worker.claimer import ClaimCandidate, TaskClaimer
from muad_agent_worker.worker.service import WorkerLoop
from muad_common import SharedSettings

from agent_worker.conftest import TenantContext
from agent_worker.helpers import fetch_events, fetch_task, persist_task

QUEUE = "QUEUE"
ALLOW_PARALLEL = "ALLOW_PARALLEL"
QUEUE_POLICY: dict[str, Any] = {"concurrency_policy": QUEUE, "max_concurrency": 1}


async def _schedule(
    tenant: TenantContext,
    *,
    policy: dict[str, Any] | None = None,
) -> tuple[TaskSchedule, TaskScheduleRevision]:
    """一条 ACTIVE Schedule + 当前 Revision（并发上限的唯一来源是 Revision `schedule_policy`）。"""
    async with tenant.session_factory() as session, session.begin():
        schedule = TaskSchedule(
            id=uuid.uuid4(),
            tenant_id=tenant.tenant_id,
            owner_user_id=uuid.uuid4(),
            agent_id=uuid.uuid4(),
            name="hourly report",
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
            mode="SKILL",
            instruction_raw="每小时跑一次报表",
            normalized_goal="跑报表",
            recurrence_kind="CRON",
            cron_expr="0 * * * *",
            timezone="Asia/Shanghai",
            schedule_policy=dict(policy or QUEUE_POLICY),
            payload_kind="SKILL_PLAN",
            payload_json={
                "kind": "SKILL_PLAN",
                "skill_id": str(uuid.uuid4()),
                "input": {},
                "argv": [],
                "window": {"kind": "DAY", "offset": 0},
            },
            delivery_intent="NONE",
            created_by=uuid.uuid4(),
        )
        session.add(revision)
        await session.flush()
        schedule.current_revision_id = revision.id
        await session.flush()
    return schedule, revision


async def _queued(
    tenant: TenantContext,
    schedule: TaskSchedule,
    revision: TaskScheduleRevision,
    *,
    scheduled_at: datetime,
    create_time: datetime | None = None,
    **overrides: Any,
) -> TaskExecution:
    values: dict[str, Any] = {
        "schedule_id": schedule.id,
        "schedule_revision_id": revision.id,
        "scheduled_at": scheduled_at,
        "trigger_type": "SCHEDULED",
    }
    if create_time is not None:
        values["create_time"] = create_time
    values.update(overrides)
    return await persist_task(tenant, **values)


async def _finish(tenant: TenantContext, task_id: uuid.UUID, status: str = "COMPLETED") -> None:
    """把一条执行收成终态（腾出 Schedule 槽位）；不经过 Worker 的收尾事务。"""
    moment = datetime.now(UTC)
    async with tenant.session_factory() as session, session.begin():
        await session.execute(
            sa.update(TaskExecution)
            .where(TaskExecution.id == task_id)
            .values(
                status=status,
                finished_at=moment,
                lease_owner=None,
                lease_until=None,
                update_time=moment,
            )
        )


async def _state(tenant: TenantContext, task_id: uuid.UUID) -> dict[str, Any]:
    task = await fetch_task(tenant, task_id)
    return {
        "status": task.status,
        "not_before": task.not_before,
        "attempt": task.attempt,
        "resume_count": task.resume_count,
        "lease_epoch": task.lease_epoch,
        "wait_kind": task.wait_kind,
    }


# --------------------------------------------------------------- QUEUE 的 FIFO 与槽位


async def test_queue_claims_fifo_by_scheduled_at_then_create_time(tenant: TenantContext) -> None:
    """QUEUE 按 `(scheduled_at, create_time)` FIFO 领取，且同一 Schedule 不并行（B-05）。"""
    schedule, revision = await _schedule(tenant)
    now = datetime.now(UTC)
    # `create_time` 故意与计划顺序相反：FIFO 必须由 `scheduled_at` 决定。
    oldest = await _queued(
        tenant, schedule, revision,
        scheduled_at=now - timedelta(minutes=3), create_time=now - timedelta(seconds=1),
    )
    middle = await _queued(
        tenant, schedule, revision,
        scheduled_at=now - timedelta(minutes=2), create_time=now - timedelta(seconds=2),
    )
    newest = await _queued(
        tenant, schedule, revision,
        scheduled_at=now - timedelta(minutes=1), create_time=now - timedelta(seconds=3),
    )
    claimer = TaskClaimer(tenant.session_factory, tenant.settings)

    first = await claimer.claim_one("worker-a")
    assert first is not None and first.id == oldest.id, "最早的计划时间必须先跑"

    # 同一 Schedule 已有占槽执行：其余两条保持 QUEUED，且**不写** not_before 退避。
    not_before = (await _state(tenant, middle.id))["not_before"]
    assert await claimer.claim_one("worker-b") is None
    assert (await _state(tenant, middle.id))["status"] == "QUEUED"
    assert (await _state(tenant, middle.id))["not_before"] == not_before, (
        "被挡住不是未到期，不得伪造 not_before 退避"
    )
    assert await fetch_events(tenant, middle.id) == [], "被挡住的候选不留任何领取事件"

    await _finish(tenant, oldest.id)
    second = await claimer.claim_one("worker-b")
    assert second is not None and second.id == middle.id

    await _finish(tenant, middle.id)
    third = await claimer.claim_one("worker-a")
    assert third is not None and third.id == newest.id


async def test_queued_sibling_never_blocks_the_candidate_itself(tenant: TenantContext) -> None:
    """QUEUED 不在占槽集里：队列里排着另一条 QUEUED，不影响第一条被领取。"""
    schedule, revision = await _schedule(tenant)
    now = datetime.now(UTC)
    head = await _queued(tenant, schedule, revision, scheduled_at=now - timedelta(minutes=2))
    await _queued(tenant, schedule, revision, scheduled_at=now - timedelta(minutes=1))

    claimed = await TaskClaimer(tenant.session_factory, tenant.settings).claim_one("worker-a")

    assert claimed is not None and claimed.id == head.id, "QUEUED 兄弟不得把候选自己挡住"


async def test_allow_parallel_uses_the_configured_slot_count(tenant: TenantContext) -> None:
    """ALLOW_PARALLEL 按 `max_concurrency` 算真实槽位，不会被"永不允许并行"降级（B-06）。"""
    schedule, revision = await _schedule(
        tenant, policy={"concurrency_policy": ALLOW_PARALLEL, "max_concurrency": 2}
    )
    now = datetime.now(UTC)
    first = await _queued(tenant, schedule, revision, scheduled_at=now - timedelta(minutes=3))
    second = await _queued(tenant, schedule, revision, scheduled_at=now - timedelta(minutes=2))
    third = await _queued(tenant, schedule, revision, scheduled_at=now - timedelta(minutes=1))
    claimer = TaskClaimer(tenant.session_factory, tenant.settings)

    claimed = [await claimer.claim_one("worker-a"), await claimer.claim_one("worker-b")]
    assert {task.id for task in claimed if task is not None} == {first.id, second.id}, (
        "max_concurrency=2 时两个槽位必须都开"
    )
    assert await claimer.claim_one("worker-c") is None, "第三个必须保持排队，不丢"

    await _finish(tenant, first.id)
    released = await claimer.claim_one("worker-c")
    assert released is not None and released.id == third.id, "腾出槽位后按 FIFO 放行"


async def test_waiting_execution_occupies_the_schedule_slot(tenant: TenantContext) -> None:
    """WAITING_* 视为占槽（B-05/B-06）：外部/人工等待期间同一 Schedule 不再开新执行。"""
    schedule, revision = await _schedule(tenant)
    now = datetime.now(UTC)
    waiting = await _queued(
        tenant, schedule, revision,
        scheduled_at=now - timedelta(minutes=2),
        status="WAITING_EXTERNAL",
        wait_kind="EXTERNAL_POLL",
        wait_payload_json={"external_ref": {"external_task_id": "ext-1"}},
        not_before=now + timedelta(minutes=30),
    )
    queued = await _queued(tenant, schedule, revision, scheduled_at=now - timedelta(minutes=1))
    claimer = TaskClaimer(tenant.session_factory, tenant.settings)

    assert await claimer.claim_one("worker-a") is None, "等待中的执行仍占着 Schedule 槽"
    assert (await _state(tenant, queued.id))["status"] == "QUEUED"

    await _finish(tenant, waiting.id)
    claimed = await claimer.claim_one("worker-b")
    assert claimed is not None and claimed.id == queued.id


async def test_a_blocked_schedule_head_does_not_starve_another_schedule(
    tenant: TenantContext,
) -> None:
    """被挡住的队头不空转、也不挤掉别的 Schedule（预筛必须把占槽状态写进 SQL）。"""
    blocked_schedule, blocked_revision = await _schedule(tenant)
    other_schedule, other_revision = await _schedule(tenant)
    now = datetime.now(UTC)
    running = await _queued(
        tenant, blocked_schedule, blocked_revision,
        scheduled_at=now - timedelta(minutes=5),
        status="RUNNING",
        lease_owner="worker-z",
        lease_until=now + timedelta(minutes=5),
    )
    blocked_head = await _queued(
        tenant, blocked_schedule, blocked_revision, scheduled_at=now - timedelta(minutes=4)
    )
    assert running.id != blocked_head.id
    other = await _queued(
        tenant, other_schedule, other_revision, scheduled_at=now - timedelta(minutes=1)
    )

    claimed = await TaskClaimer(tenant.session_factory, tenant.settings).claim_one("worker-a")

    assert claimed is not None and claimed.id == other.id, (
        "另一个 Schedule 的到期执行必须能领到，而不是被挡住的队头挤掉"
    )
    assert (await _state(tenant, blocked_head.id))["status"] == "QUEUED"


# --------------------------------------------------------------- 两段式领取


async def test_slot_recheck_under_the_schedule_lock_rejects_a_stale_candidate(
    tenant: TenantContext, monkeypatch: Any
) -> None:
    """预筛单独挡不住并发：锁内复检必须把预筛放过来的陈旧候选拒绝掉（design §3.5.3）。"""
    schedule, revision = await _schedule(tenant)
    now = datetime.now(UTC)
    first = await _queued(tenant, schedule, revision, scheduled_at=now - timedelta(minutes=2))
    second = await _queued(tenant, schedule, revision, scheduled_at=now - timedelta(minutes=1))
    claimer = TaskClaimer(tenant.session_factory, tenant.settings)

    held = await claimer.claim_one("worker-a")
    assert held is not None and held.id == first.id
    assert (await _state(tenant, first.id))["status"] == "RUNNING"

    # 预筛本身已经不会再选 second（占槽数已满）——这一层只减少空转。
    async with tenant.session_factory() as session, session.begin():
        assert await claimer._prefilter(session, now) is None

    # 把预筛结果换成"陈旧候选"（模拟两个 Worker 都通过了预筛）：锁内复检必须拒绝。
    async def stale(self: TaskClaimer, session: Any, moment: datetime) -> ClaimCandidate:
        return ClaimCandidate(id=second.id, tenant_id=tenant.tenant_id, schedule_id=schedule.id)

    monkeypatch.setattr(TaskClaimer, "_prefilter", stale)
    assert await claimer.claim_one("worker-b") is None, "锁内复检放过了超发"
    assert (await _state(tenant, second.id))["status"] == "QUEUED"


async def test_two_workers_prefiltering_together_claim_exactly_one(
    tenant: TenantContext, monkeypatch: Any
) -> None:
    """两个 Worker 同时通过预筛：Schedule 行锁把领取串行化，只有一条执行被领走（E-03）。"""
    schedule, revision = await _schedule(tenant)
    now = datetime.now(UTC)
    task = await _queued(tenant, schedule, revision, scheduled_at=now - timedelta(minutes=1))
    claimer_a = TaskClaimer(tenant.session_factory, tenant.settings)
    claimer_b = TaskClaimer(tenant.session_factory, tenant.settings)
    barrier = asyncio.Barrier(2)
    original = TaskClaimer._prefilter

    async def gated(self: TaskClaimer, session: Any, moment: datetime) -> ClaimCandidate | None:
        candidate = await original(self, session, moment)
        await barrier.wait()
        return candidate

    monkeypatch.setattr(TaskClaimer, "_prefilter", gated)
    results = await asyncio.gather(
        claimer_a.claim_one("worker-a"), claimer_b.claim_one("worker-b")
    )

    claimed = [item for item in results if item is not None]
    assert len(claimed) == 1, f"同一执行被 {len(claimed)} 个 Worker 同时领走"
    assert claimed[0].id == task.id


async def test_concurrent_workers_claim_each_execution_exactly_once(
    tenant: TenantContext,
) -> None:
    """多 Worker 并发 claim：每条执行恰好被领一次，不出现重复领取或死锁。"""
    tasks = [await persist_task(tenant) for _ in range(5)]
    claimers = [TaskClaimer(tenant.session_factory, tenant.settings) for _ in range(3)]
    claimed: list[uuid.UUID] = []
    for _ in range(6):
        results = await asyncio.gather(
            *(claimer.claim_one(f"worker-{index}") for index, claimer in enumerate(claimers))
        )
        claimed.extend(task.id for task in results if task is not None)

    assert len(claimed) == len(set(claimed)), "同一条执行被领取了两次"
    assert set(claimed) == {task.id for task in tasks}
    for task in tasks:
        assert (await _state(tenant, task.id))["lease_epoch"] == 1


async def test_claim_takes_the_schedule_lock_first(tenant: TenantContext) -> None:
    """锁序第一段：claim 先等 Schedule 行锁（design §3.3.12），不是先锁执行行。"""
    schedule, revision = await _schedule(tenant)
    now = datetime.now(UTC)
    await _queued(tenant, schedule, revision, scheduled_at=now - timedelta(minutes=1))
    claimer = TaskClaimer(tenant.session_factory, tenant.settings)

    async with tenant.session_factory() as blocker:
        async with blocker.begin():
            await blocker.execute(
                sa.select(TaskSchedule.id)
                .where(TaskSchedule.id == schedule.id)
                .with_for_update()
            )
            claiming = asyncio.create_task(claimer.claim_one("worker-a"))
            await asyncio.sleep(0.3)
            assert not claiming.done(), "claim 没有先取 Schedule 行锁"
        claimed = await asyncio.wait_for(claiming, timeout=10)
    assert claimed is not None


async def test_claim_skips_an_execution_held_by_another_transaction(
    tenant: TenantContext,
) -> None:
    """执行行用 `SKIP LOCKED`：被别的会话锁住时立即放弃，不阻塞领取循环。"""
    task = await persist_task(tenant)
    claimer = TaskClaimer(tenant.session_factory, tenant.settings)

    async with tenant.session_factory() as blocker:
        async with blocker.begin():
            await blocker.execute(
                sa.select(TaskExecution.id)
                .where(TaskExecution.id == task.id)
                .with_for_update()
            )
            assert await asyncio.wait_for(claimer.claim_one("worker-a"), timeout=5) is None
        claimed = await claimer.claim_one("worker-b")
    assert claimed is not None and claimed.id == task.id


# --------------------------------------------------------------- 三类外部等待


async def test_external_poll_resumes_only_after_not_before(tenant: TenantContext) -> None:
    """EXTERNAL_POLL：到 `not_before` 才续接，并把 `external_ref` 留给执行器。"""
    now = datetime.now(UTC)
    task = await persist_task(
        tenant,
        status="WAITING_EXTERNAL",
        wait_kind="EXTERNAL_POLL",
        wait_payload_json={"external_ref": {"external_task_id": "ext-9"}},
        not_before=now + timedelta(minutes=5),
        attempt=1,
        started_at=now - timedelta(minutes=10),
    )
    claimer = TaskClaimer(tenant.session_factory, tenant.settings)

    assert await claimer.claim_one("worker-a") is None, "未到 not_before 不得续接"

    claimed = await claimer.claim_one("worker-a", now=now + timedelta(minutes=6))
    assert claimed is not None and claimed.id == task.id
    state = await fetch_task(tenant, task.id)
    assert state.status == "RUNNING"
    assert state.resume_count == 1 and state.attempt == 1
    assert state.wait_kind is None
    assert state.wait_payload_json == {"external_ref": {"external_task_id": "ext-9"}}


async def test_async_operation_resumes_only_when_every_operation_is_terminal(
    tenant: TenantContext,
) -> None:
    """ASYNC_OPERATION：`operations[]` 全部终态才续接；定时器不得在结果没齐时叫醒执行。"""
    now = datetime.now(UTC)

    async def waiting(operations: list[dict[str, Any]], **overrides: Any) -> TaskExecution:
        return await persist_task(
            tenant,
            status="WAITING_EXTERNAL",
            wait_kind="ASYNC_OPERATION",
            wait_payload_json={"operations": operations},
            not_before=now - timedelta(minutes=1),
            **overrides,
        )

    partial = await waiting(
        [
            {"operation_id": str(uuid.uuid4()), "call_id": "call-1", "status": "COMPLETED"},
            {"operation_id": str(uuid.uuid4()), "call_id": "call-2", "status": "RUNNING"},
        ]
    )
    claimer = TaskClaimer(tenant.session_factory, tenant.settings)
    assert await claimer.claim_one("worker-a") is None, "还有 operation 没终态就不得续接"

    ready = await waiting(
        [
            {"operation_id": str(uuid.uuid4()), "call_id": "call-1", "status": "COMPLETED"},
            {"operation_id": str(uuid.uuid4()), "call_id": "call-2", "status": "FAILED"},
        ]
    )
    claimed = await claimer.claim_one("worker-b")
    assert claimed is not None and claimed.id == ready.id, "全部终态（含部分失败）必须续接"
    assert (await _state(tenant, ready.id))["resume_count"] == 1
    assert (await _state(tenant, partial.id))["status"] == "WAITING_EXTERNAL"


async def test_batch_fanin_parent_is_never_claimed(tenant: TenantContext) -> None:
    """BATCH_FANIN：Parent 由 Child 终态在 fan-in 事务里收尾，claim 永不领取它。"""
    now = datetime.now(UTC)
    parent = await persist_task(
        tenant,
        status="WAITING_EXTERNAL",
        wait_kind="BATCH_FANIN",
        task_type="BATCH",
        wait_payload_json={"batch": {"aggregate_mode": "ALL", "concurrency": 2}},
        not_before=PARKED_NOT_BEFORE,
    )
    # 即使停放时刻被写成过去（历史数据/时钟漂移），等待类型本身也禁止 claim。
    drifted = await persist_task(
        tenant,
        status="WAITING_EXTERNAL",
        wait_kind="BATCH_FANIN",
        task_type="BATCH",
        wait_payload_json={"batch": {"aggregate_mode": "ALL", "concurrency": 2}},
        not_before=now - timedelta(minutes=1),
    )

    claimer = TaskClaimer(tenant.session_factory, tenant.settings)
    assert await claimer.claim_one("worker-a") is None
    assert (await _state(tenant, parent.id))["status"] == "WAITING_EXTERNAL"
    assert (await _state(tenant, drifted.id))["status"] == "WAITING_EXTERNAL"


async def test_an_untyped_waiting_execution_is_not_claimed(tenant: TenantContext) -> None:
    """`WAITING_EXTERNAL` 必须声明 `wait_kind`：缺了类型就没人知道该由谁唤醒，不得盲领。"""
    await persist_task(
        tenant,
        status="WAITING_EXTERNAL",
        not_before=datetime.now(UTC) - timedelta(minutes=1),
    )

    assert await TaskClaimer(tenant.session_factory, tenant.settings).claim_one("worker-a") is None


async def test_waiting_does_not_hold_a_local_worker_slot(tenant: TenantContext) -> None:
    """外部等待释放执行 lease，也不占本地执行槽：单槽 Worker 能连续推进两条等待中的执行。"""
    settings = SharedSettings(worker_execution_concurrency=1)
    first = await persist_task(tenant, status="WAITING_EXTERNAL", wait_kind="EXTERNAL_POLL",
                               wait_payload_json={"external_ref": {}},
                               not_before=datetime.now(UTC) - timedelta(minutes=1))
    second = await persist_task(tenant, status="WAITING_EXTERNAL", wait_kind="EXTERNAL_POLL",
                                wait_payload_json={"external_ref": {}},
                                not_before=datetime.now(UTC) - timedelta(minutes=1))

    class _WaitingExecutor:
        async def execute(self, task: TaskExecution) -> dict[str, Any]:
            return {
                "status": "SUCCEEDED",
                "result": {
                    "wait": {
                        "wait_kind": "EXTERNAL_POLL",
                        "external_ref": {"poll": str(task.id)},
                        "not_before": (datetime.now(UTC) + timedelta(minutes=30)).isoformat(),
                    }
                },
                "stderr": "",
                "exit_code": 0,
            }

    worker = WorkerLoop(
        tenant.session_factory, settings, executor=_WaitingExecutor(), instance_id="worker-a"
    )

    ran = {await worker.run_once(), await worker.run_once()}

    assert ran == {first.id, second.id}, "等待中的执行不得占住本地执行槽"
    for task in (first, second):
        assert (await _state(tenant, task.id))["status"] == "WAITING_EXTERNAL"


async def test_immediate_tasks_keep_priority_order_and_go_before_the_queue(
    tenant: TenantContext,
) -> None:
    """立即任务保持 `(priority, create_time)` 且先于排队的调度执行（有人当场在等）。"""
    schedule, revision = await _schedule(tenant)
    now = datetime.now(UTC)
    scheduled = await _queued(tenant, schedule, revision, scheduled_at=now - timedelta(hours=1))
    low = await persist_task(tenant, priority=200)
    high = await persist_task(tenant, priority=10)
    claimer = TaskClaimer(tenant.session_factory, tenant.settings)

    order = []
    for instance in ("worker-a", "worker-b", "worker-c"):
        claimed = await claimer.claim_one(instance)
        assert claimed is not None
        order.append(claimed.id)

    assert order == [high.id, low.id, scheduled.id]
