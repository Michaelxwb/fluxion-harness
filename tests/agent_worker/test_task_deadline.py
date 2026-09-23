"""B-117 / E-03：deadline sweep 由独立 Scheduler 节拍驱动（设计 §3.2.2 / §3.5）。

真实边界：真实 Scheduler 节拍 → 真实 PostgreSQL CAS → 真实投递循环（HTTP）→
按 delivery_mode 决定是否发送。不 mock 持久化，也不把 sweep 绑在串行执行上。
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from conftest import TenantContext
from helpers import fetch_events, fetch_task, persist_task, sample_route
from muad_agent_worker.delivery.client import HttpDeliveryClient
from muad_agent_worker.delivery.service import DeliveryLoop
from muad_agent_worker.metrics import value
from muad_agent_worker.scheduler.service import DeadlineSweeper, SchedulerLoop
from muad_agent_worker.worker.service import WorkerLoop
from muad_common import SharedSettings

TASK_DEADLINE_EXCEEDED = "TASK_DEADLINE_EXCEEDED"
DEADLINE_TOTAL = "task_deadline_exceeded_total"


def _handler(status_code: int, calls: list[httpx.Request]) -> Any:
    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(status_code, json={"code": "0", "data": {"accepted": True}})

    return handle


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
    if status == "WAITING":
        values.update(lease_owner=None, lease_until=None)
    values.update(overrides)
    return await persist_task(tenant, **values)


async def test_b117_sweep_fails_all_expired_non_terminal_tasks(tenant: TenantContext) -> None:
    """QUEUED/RUNNING/WAITING 过期都 CAS FAILED；终态与未到期不动。"""
    queued = await _expired_task(tenant, status="QUEUED")
    running = await _expired_task(tenant, status="RUNNING")
    waiting = await _expired_task(tenant, status="WAITING")
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
        status="WAITING",
        task_type="BATCH",
        external_ref_json={"batch": {"aggregate_mode": "ALL", "concurrency": 1, "item_count": 1}},
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


async def test_e03_deadline_sweep_cas_failed_and_delivery_by_mode(tenant: TenantContext) -> None:
    """E-03：sweep CAS FAILED(TASK_DEADLINE_EXCEEDED) 后仍按 delivery_mode 投递。"""
    final_only = await _expired_task(tenant, status="RUNNING", delivery_route=sample_route())
    silent = await _expired_task(
        tenant,
        status="QUEUED",
        delivery_mode="NONE",
        delivery_status="NONE",
    )
    calls: list[httpx.Request] = []
    transport = httpx.MockTransport(_handler(200, calls))
    async with httpx.AsyncClient(transport=transport) as client:
        loop = DeliveryLoop(
            tenant.session_factory,
            HttpDeliveryClient("http://im-gateway", client),
            tenant.settings,
        )
        sweeper = DeadlineSweeper(tenant.session_factory, tenant.settings)
        assert await sweeper.sweep(now=datetime.now(UTC)) == 2

        for task in (final_only, silent):
            refreshed = await fetch_task(tenant, task.id)
            assert refreshed.status == "FAILED"
            assert refreshed.error_code == TASK_DEADLINE_EXCEEDED

        assert await loop.run_once() is not None
        assert await loop.run_once() is None, "NONE 不得被扫描"

    assert len(calls) == 1
    body = json.loads(calls[0].content)
    assert body["task_id"] == str(final_only.id)
    assert body["delivery_key"] == f"task:{final_only.id}:final"
    assert (await fetch_task(tenant, final_only.id)).delivery_status == "SENT"
    assert (await fetch_task(tenant, silent.id)).delivery_status == "NONE"


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
