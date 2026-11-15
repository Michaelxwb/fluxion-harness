"""B-116 / E-04：Misfire 聚合跳过、ONCE 终态与调度指标（设计 §3.2.3 / §3.5.11）。

真实边界：真实 SchedulerLoop → 真实 PostgreSQL `task.task_schedule` / `task.schedule_fire`
/ `task.task_execution`；跳过记录、终态与指标都经真实持久化路径观察，不 mock 存储。

v2 口径（TASK-014）：跳过是**一条** `SKIPPED(MISFIRE)` Fire 台账（`missed_count` 记额外次数），
不是 Schedule 上的 `last_error_code`；ONCE 错过触发的终态是 `ENDED(MISSED)`。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from muad_agent_worker.infrastructure.models.task import TaskExecution, TaskSchedule
from muad_agent_worker.metrics import value
from muad_contracts import Recurrence, ScheduleDraftRequest, SchedulePolicy
from muad_contracts.platform_settings import default_platform_settings

from agent_worker.conftest import TenantContext
from agent_worker.test_scheduled_v2_fire import (
    SLOT,
    MaterialsClient,
    fires,
    loop_for,
    materials_for,
    seed,
)

MISFIRE_TOTAL = "scheduled_misfire_total"
GRACE = default_platform_settings().task.misfire_grace_sec


def _missed_now(slot: datetime) -> datetime:
    return slot + timedelta(seconds=GRACE + 30)


async def test_b116_cron_misfire_writes_one_skipped_fire_and_no_execution(
    tenant: TenantContext,
) -> None:
    """CRON 错过触发：不补发、不建 Execution，Fire 台账里可查原因与额外错过数。"""
    seeded = await seed(tenant, next_fire_at=SLOT, cron="0 * * * *", timezone="UTC")
    client = MaterialsClient(materials_for())
    loop = loop_for(tenant, client)

    assert await loop.run_once(now=_missed_now(SLOT)) is None

    assert client.calls == [], "错过触发不得进入准入（不补发）"
    [fire] = await fires(tenant, seeded.schedule_id)
    assert (fire.status, fire.skip_reason) == ("SKIPPED", "MISFIRE")
    assert fire.scheduled_at == SLOT
    assert fire.missed_count == 0, "没有额外的错过槽"
    assert fire.execution_id is None
    assert await _executions(tenant, seeded.schedule_id) == []

    refreshed = await _schedule(tenant, seeded.schedule_id)
    assert refreshed.status == "ACTIVE", "CRON 跳过不是终态"
    assert refreshed.last_fire_at is None
    assert refreshed.next_fire_at == SLOT + timedelta(hours=1)
    assert refreshed.occurrence_count == 1


async def test_b116_schedule_contract_has_no_misfire_policy() -> None:
    """P0 只有 SKIP 语义：没有可配置的 misfire_policy 字段/枚举。"""
    assert "misfire_policy" not in TaskSchedule.__table__.columns
    for contract in (SchedulePolicy, Recurrence, ScheduleDraftRequest):
        assert "misfire_policy" not in contract.model_fields


async def test_b116_once_success_completes_exactly_once(tenant: TenantContext) -> None:
    """ONCE 成功触发恰好创建一条 Execution 并进入 ENDED(COMPLETED)。"""
    seeded = await seed(tenant, kind="ONCE", cron=None, run_at=SLOT, next_fire_at=SLOT)
    loop = loop_for(tenant, MaterialsClient(materials_for()))

    assert await loop.run_once(now=SLOT + timedelta(seconds=1)) is not None
    assert len(await _executions(tenant, seeded.schedule_id)) == 1

    refreshed = await _schedule(tenant, seeded.schedule_id)
    assert refreshed.status == "ENDED"
    assert refreshed.end_reason == "COMPLETED"
    assert refreshed.next_fire_at is None
    assert refreshed.last_fire_at == SLOT

    assert await loop.run_once(now=SLOT + timedelta(seconds=2)) is None
    assert len(await _executions(tenant, seeded.schedule_id)) == 1


async def test_b116_missed_once_ends_missed_not_completed(tenant: TenantContext) -> None:
    """ONCE 错过触发：终态 `ENDED(MISSED)`，不冒充完成，也不建 Execution。"""
    seeded = await seed(tenant, kind="ONCE", cron=None, run_at=SLOT, next_fire_at=SLOT)
    client = MaterialsClient(materials_for())
    loop = loop_for(tenant, client)

    assert await loop.run_once(now=_missed_now(SLOT)) is None

    assert client.calls == []
    [fire] = await fires(tenant, seeded.schedule_id)
    assert (fire.status, fire.skip_reason, fire.missed_count) == ("SKIPPED", "MISFIRE", 0)
    assert await _executions(tenant, seeded.schedule_id) == []

    refreshed = await _schedule(tenant, seeded.schedule_id)
    assert refreshed.status == "ENDED"
    assert refreshed.end_reason == "MISSED", "错过不是完成"
    assert refreshed.next_fire_at is None
    assert refreshed.last_fire_at is None

    assert await loop.run_once(now=_missed_now(SLOT) + timedelta(seconds=1)) is None
    assert await _executions(tenant, seeded.schedule_id) == []


async def test_e04_cron_misfire_stays_active_and_increments_misfire_total(
    tenant: TenantContext,
) -> None:
    """E-04：CRON 错过触发不补发，并累加 `scheduled_misfire_total`。"""
    before = value(MISFIRE_TOTAL)
    seeded = await seed(tenant, next_fire_at=SLOT, cron="0 * * * *", timezone="UTC")
    loop = loop_for(tenant, MaterialsClient(materials_for()))

    assert await loop.run_once(now=_missed_now(SLOT)) is None

    assert value(MISFIRE_TOTAL) == before + 1, "跳过必须累加 scheduled_misfire_total"
    assert await _executions(tenant, seeded.schedule_id) == []
    [fire] = await fires(tenant, seeded.schedule_id)
    assert (fire.status, fire.skip_reason) == ("SKIPPED", "MISFIRE")


async def test_e04_missed_once_is_terminal_and_increments_misfire_total(
    tenant: TenantContext,
) -> None:
    """E-04：ONCE 错过触发进入不可恢复终态 `ENDED(MISSED)`，同样累加指标。"""
    before = value(MISFIRE_TOTAL)
    seeded = await seed(tenant, kind="ONCE", cron=None, run_at=SLOT, next_fire_at=SLOT)
    loop = loop_for(tenant, MaterialsClient(materials_for()))

    assert await loop.run_once(now=_missed_now(SLOT)) is None

    assert value(MISFIRE_TOTAL) == before + 1
    refreshed = await _schedule(tenant, seeded.schedule_id)
    assert (refreshed.status, refreshed.end_reason) == ("ENDED", "MISSED")
    assert await _executions(tenant, seeded.schedule_id) == []


async def _executions(tenant: TenantContext, schedule_id: uuid.UUID) -> list[TaskExecution]:
    from sqlalchemy import select

    async with tenant.session_factory() as session:
        rows = await session.scalars(
            select(TaskExecution).where(TaskExecution.schedule_id == schedule_id)
        )
    return list(rows)


async def _schedule(tenant: TenantContext, schedule_id: uuid.UUID) -> TaskSchedule:
    async with tenant.session_factory() as session:
        row = await session.get(TaskSchedule, schedule_id)
    assert row is not None
    return row
