"""Scheduler 主路径：一轮扫多条、时区推进、模板渲染与 deadline 节拍（B-115 / 评审 #15）。

真实边界：真实 PostgreSQL 行锁与 CAS、真实 Scheduler/Fire 写入、真实 recurrence 引擎；
只有 Console 的网络取数是协议替身。

v2 口径替换（TASK-014）：创建/更新/暂停/恢复/删除与列表过滤属于 Draft+确认 与
生命周期矩阵，已由 `test_scheduled_v2_confirm.py` / `test_scheduled_v2_lifecycle.py` /
`test_schedule_queries_actions.py` 按 v2 词汇逐一断言，本文件只保留调度循环自己的事实。
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from muad_agent_worker.infrastructure.models.task import TaskExecution
from muad_agent_worker.scheduler.service import TASK_DEADLINE_EXCEEDED, SchedulerLoop
from muad_common import SharedSettings
from muad_contracts import load_schedule_snapshot

from agent_worker.conftest import TenantContext
from agent_worker.helpers import fetch_task, persist_task
from agent_worker.test_scheduled_v2_fire import (
    SLOT,
    MaterialsClient,
    SettingsClient,
    fires,
    loop_for,
    materials_for,
    payload_for,
    seed,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")


async def test_run_due_fires_every_due_schedule_in_one_tick(tenant: TenantContext) -> None:
    """同一时刻到期的多个 Schedule 在一轮内全部触发，不因排队被判 misfire。"""
    seeded = [
        await seed(tenant, next_fire_at=SLOT, agent_id=uuid.uuid4()) for _ in range(12)
    ]
    loop = loop_for(tenant, MaterialsClient(materials_for()))

    processed = await loop.run_due(now=SLOT + timedelta(seconds=1))

    assert processed == 12
    for item in seeded:
        rows = await fires(tenant, item.schedule_id)
        assert [row.status for row in rows] == ["EXECUTION_CREATED"]
        assert len(await _executions(tenant, item.schedule_id)) == 1


async def test_cron_advances_next_fire_at_on_the_schedule_timezone(
    tenant: TenantContext,
) -> None:
    """`next_fire_at` 在 Revision 的 IANA 时区上推进：上海 09:00 的下一槽仍是 09:00。"""
    seeded = await seed(tenant, next_fire_at=SLOT)
    loop = loop_for(tenant, MaterialsClient(materials_for()))

    assert await loop.run_once(now=SLOT + timedelta(seconds=1)) is not None

    [fire] = await fires(tenant, seeded.schedule_id)
    local = fire.scheduled_at.astimezone(SHANGHAI)
    assert (local.hour, local.minute) == (9, 0)
    refreshed = await _schedule(tenant, seeded.schedule_id)
    assert refreshed.next_fire_at is not None
    following = refreshed.next_fire_at.astimezone(SHANGHAI)
    assert (following.hour, following.minute) == (9, 0)
    assert following.date() == local.date() + timedelta(days=1)


async def test_fire_renders_input_template_variables_in_schedule_timezone(
    tenant: TenantContext,
) -> None:
    """`{{previous_day}}`/`{{fire_date}}` 按 Schedule 时区与本次计划时刻渲染后冻结进 Execution。"""
    fire_at = datetime(2026, 2, 28, 17, 0, tzinfo=UTC)  # 2026-03-01 01:00 上海
    seeded = await seed(
        tenant,
        next_fire_at=fire_at,
        cron="0 1 * * *",
        timezone="Asia/Shanghai",
        payload=payload_for(
            "SKILL", input_template={"report_day": "{{previous_day}}", "nested": ["on {{fire_date}}"]}
        ),
        context={"output": {"format": "markdown"}},
    )
    loop = loop_for(tenant, MaterialsClient(materials_for()))

    assert await loop.run_once(now=fire_at + timedelta(seconds=1)) is not None

    [execution] = await _executions(tenant, seeded.schedule_id)
    assert execution.input_json == {
        "kind": "SKILL_PLAN",
        "mode": "SKILL",
        "session_target": "isolated",
        "skill_id": str(uuid.UUID(payload_for("SKILL")["skill_id"])),
        "input": {"report_day": "2026-02-28", "nested": ["on 2026-03-01"]},
        "argv": [],
        "window": {"kind": "DAY", "offset": -1},
        "context": {"output": {"format": "markdown"}},
    }
    # Revision 上的模板本身不被改写。
    refreshed = await _revision(tenant, seeded.revision_id)
    assert refreshed.payload_json["input"]["report_day"] == "{{previous_day}}"


async def test_invalid_template_is_a_permanent_admission_failure(tenant: TenantContext) -> None:
    """未知模板变量在 v2 是准入的永久错误（`SCHEMA_INCOMPATIBLE`），不建可执行 Execution。"""
    seeded = await seed(
        tenant,
        next_fire_at=SLOT,
        payload=payload_for("SKILL", input_template={"day": "{{tomorrow}}"}),
    )
    client = MaterialsClient(materials_for())
    loop = loop_for(tenant, client)

    assert await loop.run_once(now=SLOT + timedelta(seconds=1)) is None

    assert client.calls == [], "Revision 自身不合法时连物料都不取"
    [fire] = await fires(tenant, seeded.schedule_id)
    assert (fire.status, fire.admission_error_code) == ("FAILED_ADMISSION", "SCHEMA_INCOMPATIBLE")
    assert await _executions(tenant, seeded.schedule_id) == []
    refreshed = await _schedule(tenant, seeded.schedule_id)
    assert refreshed.status == "NEEDS_ATTENTION"
    assert refreshed.attention_reason == "SCHEMA_INCOMPATIBLE"


async def test_fire_snapshot_is_v4_and_freezes_no_model_secret(tenant: TenantContext) -> None:
    """触发快照是 v4 依赖集：必需键齐全、无密钥、字节数与哈希自洽。"""
    seeded = await seed(tenant, next_fire_at=SLOT)
    loop = loop_for(tenant, MaterialsClient(materials_for()))

    assert await loop.run_once(now=SLOT + timedelta(seconds=1)) is not None

    [execution] = await _executions(tenant, seeded.schedule_id)
    snapshot = execution.execution_snapshot_json
    assert execution.execution_snapshot_schema_version == 4
    assert snapshot["schema_version"] == 4
    for key in (
        "schema_version",
        "snapshot_bytes",
        "mode",
        "agent",
        "model",
        "policy",
        "skills",
        "tools",
        "mcp",
        "memory",
        "budget",
        "allowlist",
        "prompt_template",
        "context",
        "grants",
    ):
        assert key in snapshot
    # v4 校验器接受的才是冻结快照；密钥在构建期就被拒绝而不是打印期脱敏。
    assert load_schedule_snapshot(snapshot).model_dump(mode="json")["snapshot_bytes"] == (
        execution.snapshot_bytes
    )
    assert "api_key" not in str(snapshot)


async def test_deadline_sweep_is_not_blocked_by_a_slow_admission(
    tenant: TenantContext,
) -> None:
    """慢的 Schedule 准入不得拖住 deadline sweep（评审 #15）。

    此前 `run_forever` 先 `await` 整轮 `run_due` 再 sweep：一轮最多 `scheduler_batch_size`
    条 Schedule、每条都要取设置并解析有效定义，于是 `task_deadline_sweep_interval_sec` 不是
    实际最大延迟——一次慢的外部调用就能让它迟到整轮。
    """
    now = datetime.now(UTC)
    await seed(tenant, next_fire_at=now - timedelta(seconds=1))
    expired = await persist_task(tenant, status="QUEUED", deadline_at=now - timedelta(seconds=1))

    blocked = asyncio.Event()
    release = asyncio.Event()

    async def hold() -> None:
        blocked.set()
        await release.wait()

    settings = SharedSettings(scheduler_poll_interval_sec=1, task_deadline_sweep_interval_sec=1)
    loop = SchedulerLoop(
        tenant.session_factory,
        MaterialsClient(materials_for(), hook=hold),
        settings,
        settings_client=SettingsClient(),
    )
    running = asyncio.create_task(loop.run_forever())
    try:
        await asyncio.wait_for(blocked.wait(), timeout=10)
        deadline = datetime.now(UTC) + timedelta(seconds=10)
        while datetime.now(UTC) < deadline:
            if (await fetch_task(tenant, expired.id)).error_code == TASK_DEADLINE_EXCEEDED:
                break
            await asyncio.sleep(0.1)
        refreshed = await fetch_task(tenant, expired.id)
        assert refreshed.status == "FAILED", "准入卡住时 sweep 也没跑"
        assert refreshed.error_code == TASK_DEADLINE_EXCEEDED
        assert loop._last_sweep_at is not None
    finally:
        release.set()
        running.cancel()
        with suppress(asyncio.CancelledError):
            await running


async def _executions(tenant: TenantContext, schedule_id: uuid.UUID) -> list[TaskExecution]:
    from sqlalchemy import select

    async with tenant.session_factory() as session:
        rows = await session.scalars(
            select(TaskExecution)
            .where(TaskExecution.schedule_id == schedule_id)
            .order_by(TaskExecution.create_time)
        )
    return list(rows)


async def _schedule(tenant: TenantContext, schedule_id: uuid.UUID):
    from muad_agent_worker.infrastructure.models.task import TaskSchedule

    async with tenant.session_factory() as session:
        row = await session.get(TaskSchedule, schedule_id)
    assert row is not None
    return row


async def _revision(tenant: TenantContext, revision_id: uuid.UUID):
    from muad_agent_worker.infrastructure.models.task import TaskScheduleRevision

    async with tenant.session_factory() as session:
        row = await session.get(TaskScheduleRevision, revision_id)
    assert row is not None
    return row
