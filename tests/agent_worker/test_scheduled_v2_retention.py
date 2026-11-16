"""TASK-031 · 定时任务历史的有界保留清理（design §3.5.11「保留」、§3.3.6/§3.3.7）。

真实边界：真实 PostgreSQL（FK 顺序、循环 Fire↔Execution 引用、唯一键与行锁）、真实的
`ScheduleRetentionService` / `ScheduleRetentionLoop` 与真实 Worker 读面（ASGI HTTP）。只把
Console 的设置网络取数替成协议实现。

一条事实一个用例：清理范围与 FK 顺序、三类等待态的保护、并发回答不丢恢复材料、终态待投递
不删、截止边界、前进游标越过错过的行、租户隔离、设置来源，以及读面对已清理历史给出保留提示。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from muad_agent_worker.application.human_interactions import HumanInteractionService
from muad_agent_worker.application.ports import PlatformSettingsSnapshot
from muad_agent_worker.application.schedule_read import HistoryWindow, ScheduleReadService
from muad_agent_worker.application.schedule_retention import (
    RetentionCursor,
    ScheduleRetentionLoop,
    ScheduleRetentionService,
)
from muad_agent_worker.infrastructure.db import get_session_factory
from muad_agent_worker.infrastructure.models.task import (
    ScheduleFire,
    TaskDelivery,
    TaskDeliveryAttempt,
    TaskEvent,
    TaskExecution,
    TaskExecutionCheckpoint,
    TaskHumanInteraction,
    TaskSchedule,
    TaskScheduleRevision,
)
from muad_agent_worker.main import app

from agent_worker.conftest import TenantContext
from agent_worker.helpers import internal_service_headers

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 15, 9, 0, tzinfo=UTC)
OWNER = uuid.uuid4()
AGENT = uuid.uuid4()
HASH = "sha256:" + "b" * 64
RETENTION_DAYS = 90


# --------------------------------------------------------------------- seeds


async def seed_schedule(
    tenant: TenantContext, *, tenant_id: str | None = None
) -> tuple[uuid.UUID, uuid.UUID]:
    schedule_id, revision_id = uuid.uuid4(), uuid.uuid4()
    async with tenant.session_factory() as session, session.begin():
        schedule = TaskSchedule(
            id=schedule_id,
            tenant_id=tenant_id or tenant.tenant_id,
            owner_user_id=OWNER,
            agent_id=AGENT,
            name="保留清理",
            status="ACTIVE",
            config_version=1,
            next_fire_at=NOW + timedelta(days=1),
            created_by=OWNER,
        )
        session.add(schedule)
        await session.flush()
        session.add(
            TaskScheduleRevision(
                id=revision_id,
                tenant_id=tenant_id or tenant.tenant_id,
                schedule_id=schedule_id,
                revision_no=1,
                mode="REMINDER",
                instruction_raw="每天提醒",
                normalized_goal="提醒",
                recurrence_kind="CRON",
                cron_expr="0 9 * * *",
                timezone="Asia/Shanghai",
                schedule_policy={},
                context_json={},
                execution_spec={"mode": "REMINDER", "reminder_text": "开会"},
                delivery_intent="NONE",
                preflight_json={},
                created_by=OWNER,
            )
        )
        await session.flush()
        schedule.current_revision_id = revision_id
    return schedule_id, revision_id


async def seed_fire(
    tenant: TenantContext,
    schedule_id: uuid.UUID,
    revision_id: uuid.UUID,
    *,
    age_days: float,
    scheduled_at: datetime | None = None,
    execution_id: uuid.UUID | None = None,
    tenant_id: str | None = None,
) -> uuid.UUID:
    fire_id = uuid.uuid4()
    created = NOW - timedelta(days=age_days)
    async with tenant.session_factory() as session, session.begin():
        session.add(
            ScheduleFire(
                id=fire_id,
                tenant_id=tenant_id or tenant.tenant_id,
                schedule_id=schedule_id,
                schedule_revision_id=revision_id,
                scheduled_at=scheduled_at or created,
                status="EXECUTION_CREATED" if execution_id else "SKIPPED",
                skip_reason=None if execution_id else "OVERLAP",
                execution_id=execution_id,
                create_time=created,
                update_time=created,
            )
        )
    return fire_id


async def seed_execution(
    tenant: TenantContext,
    schedule_id: uuid.UUID,
    revision_id: uuid.UUID,
    *,
    status: str = "COMPLETED",
    age_days: float = 120,
    finished: bool = True,
    fire_id: uuid.UUID | None = None,
    tenant_id: str | None = None,
    deadline_at: datetime | None = None,
) -> uuid.UUID:
    execution_id = uuid.uuid4()
    created = NOW - timedelta(days=age_days)
    async with tenant.session_factory() as session, session.begin():
        session.add(
            TaskExecution(
                id=execution_id,
                tenant_id=tenant_id or tenant.tenant_id,
                schedule_id=schedule_id,
                schedule_revision_id=revision_id,
                schedule_fire_id=fire_id,
                scheduled_at=created,
                agent_id=AGENT,
                actor_user_id=OWNER,
                task_type="REMINDER",
                trigger_type="SCHEDULED",
                status=status,
                input_json={},
                execution_snapshot_json={"schema_version": 4, "agent": {"instructions": "x" * 64}},
                snapshot_hash=HASH,
                snapshot_bytes=2048,
                idempotency_key=f"k-{execution_id}",
                create_time=created,
                update_time=created,
                finished_at=created + timedelta(minutes=1) if finished else None,
                deadline_at=deadline_at or (NOW + timedelta(hours=1)),
            )
        )
    return execution_id


async def add_checkpoint(tenant: TenantContext, execution_id: uuid.UUID) -> uuid.UUID:
    checkpoint_id = uuid.uuid4()
    async with tenant.session_factory() as session, session.begin():
        session.add(
            TaskExecutionCheckpoint(
                id=checkpoint_id,
                tenant_id=tenant.tenant_id,
                execution_id=execution_id,
                checkpoint_no=1,
                state_json={"messages": []},
            )
        )
    return checkpoint_id


async def add_interaction(
    tenant: TenantContext, execution_id: uuid.UUID, *, status: str = "OPEN"
) -> uuid.UUID:
    interaction_id = uuid.uuid4()
    async with tenant.session_factory() as session, session.begin():
        session.add(
            TaskHumanInteraction(
                id=interaction_id,
                tenant_id=tenant.tenant_id,
                execution_id=execution_id,
                owner_user_id=OWNER,
                agent_id=AGENT,
                interaction_type="INPUT",
                status=status,
                question="继续吗",
                input_schema={"type": "object"},
                deadline_at=NOW + timedelta(hours=1),
            )
        )
    return interaction_id


async def add_delivery(
    tenant: TenantContext, execution_id: uuid.UUID, *, status: str = "SENT"
) -> uuid.UUID:
    route_id = uuid.uuid4()
    delivery_id = uuid.uuid4()
    async with tenant.session_factory() as session, session.begin():
        await session.execute(
            sa.text(
                "INSERT INTO task.delivery_route "
                "(id, tenant_id, channel, bot_id, platform_user_id, external_user_id,"
                " route_hash, route_json, status)"
                " VALUES (:id, :tenant, 'WECHAT', 'bot', :owner, 'ext', :hash,"
                " '{}'::jsonb, 'ACTIVE')"
            ),
            {
                "id": route_id,
                "tenant": tenant.tenant_id,
                "owner": OWNER,
                "hash": "sha256:" + route_id.hex,
            },
        )
        session.add(
            TaskDelivery(
                id=delivery_id,
                tenant_id=tenant.tenant_id,
                execution_id=execution_id,
                delivery_route_id=route_id,
                delivery_kind="EXECUTION_RESULT",
                delivery_key=f"execution:{execution_id}:final",
                status=status,
                attempt_count=1,
            )
        )
        await session.flush()
        session.add(
            TaskDeliveryAttempt(
                id=uuid.uuid4(),
                tenant_id=tenant.tenant_id,
                delivery_id=delivery_id,
                attempt_no=1,
                status=status,
            )
        )
    return delivery_id


async def add_outbox(tenant: TenantContext, execution_id: uuid.UUID, *, status: str = "PENDING") -> uuid.UUID:
    event_id, outbox_id, operation_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with tenant.session_factory() as session, session.begin():
        session.add(
            TaskEvent(
                id=event_id,
                tenant_id=tenant.tenant_id,
                entity_type="EXECUTION",
                entity_id=execution_id,
                seq=99,
                event_type="COMPLETED",
                payload_json={},
                occurred_at=NOW - timedelta(days=120),
            )
        )
        await session.flush()
        await session.execute(
            sa.text(
                "INSERT INTO task.runtime_result_outbox "
                "(id, tenant_id, event_id, task_id, task_event_id, operation_id,"
                " task_snapshot_hash, payload_json, payload_hash, status, not_before)"
                " VALUES (:id, :tenant, :event, :task, :task_event, :operation, :hash,"
                " '{}'::jsonb, :hash, :status, :not_before)"
            ),
            {
                "id": outbox_id,
                "tenant": tenant.tenant_id,
                "event": event_id,
                "task": execution_id,
                "task_event": event_id,
                "operation": operation_id,
                "hash": HASH,
                "status": status,
                "not_before": NOW - timedelta(days=120),
            },
        )
    return outbox_id


async def add_event(
    tenant: TenantContext,
    *,
    entity_type: str,
    entity_id: uuid.UUID,
    event_type: str = "COMPLETED",
    seq: int = 1,
) -> uuid.UUID:
    event_id = uuid.uuid4()
    async with tenant.session_factory() as session, session.begin():
        session.add(
            TaskEvent(
                id=event_id,
                tenant_id=tenant.tenant_id,
                entity_type=entity_type,
                entity_id=entity_id,
                seq=seq,
                event_type=event_type,
                payload_json={},
                occurred_at=NOW - timedelta(days=120),
            )
        )
    return event_id


async def run_retention(
    tenant: TenantContext,
    *,
    fire_history_days: int = RETENTION_DAYS,
    execution_history_days: int = RETENTION_DAYS,
    batch_size: int = 200,
    cursor: RetentionCursor | None = None,
) -> Any:
    factory = get_session_factory()
    async with factory() as session, session.begin():
        return await ScheduleRetentionService(session, batch_size=batch_size).run_batch(
            tenant_id=tenant.tenant_id,
            fire_history_days=fire_history_days,
            execution_history_days=execution_history_days,
            cursor=cursor,
            now=NOW,
        )


async def rows(tenant: TenantContext, table: str, where: str = "true") -> list[dict[str, Any]]:
    async with tenant.session_factory() as session:
        result = await session.execute(
            sa.text(f"SELECT * FROM task.{table} WHERE tenant_id = :tenant AND {where}"),
            {"tenant": tenant.tenant_id},
        )
        return [dict(row) for row in result.mappings()]


# --------------------------------------------------------------------- cases


async def test_expired_terminal_execution_is_cleaned_and_fire_row_survives(
    tenant: TenantContext,
) -> None:
    schedule_id, revision_id = await seed_schedule(tenant)
    execution_id = await seed_execution(tenant, schedule_id, revision_id, age_days=120)
    # Fire 还在自己的保留期内（30 天 < 90 天）：执行被清掉它必须留下。
    fire_id = await seed_fire(
        tenant, schedule_id, revision_id, age_days=30, execution_id=execution_id
    )
    checkpoint_id = await add_checkpoint(tenant, execution_id)
    interaction_id = await add_interaction(tenant, execution_id, status="RESOLVED")
    delivery_id = await add_delivery(tenant, execution_id, status="SENT")
    await add_event(tenant, entity_type="EXECUTION", entity_id=execution_id)
    await add_event(tenant, entity_type="HUMAN", entity_id=interaction_id, event_type="RESOLVED")
    await add_event(tenant, entity_type="DELIVERY", entity_id=delivery_id, event_type="SENT")
    await add_event(tenant, entity_type="FIRE", entity_id=fire_id, event_type="CREATED")

    report = await run_retention(tenant)

    assert report.executions == 1
    assert report.checkpoints == 1
    assert report.human_interactions == 1
    assert report.deliveries == 1
    assert report.delivery_attempts == 1
    # Fire 还在保留期内所以它的 FIRE 事件也留着；执行侧三条事件随执行清掉。
    assert report.events == 3
    assert await rows(tenant, "task_execution") == []
    assert await rows(tenant, "task_execution_checkpoint", f"id = '{checkpoint_id}'") == []
    assert await rows(tenant, "task_human_interaction") == []
    assert await rows(tenant, "task_delivery") == []
    # Fire 是历史事实：执行被清掉后 Fire 行仍在，只摘掉指向被执行行的引用。
    fires = await rows(tenant, "schedule_fire", f"id = '{fire_id}'")
    assert len(fires) == 1
    assert fires[0]["execution_id"] is None


async def test_waiting_executions_keep_recovery_material(tenant: TenantContext) -> None:
    """三类等待态都受保护：只认旧的单一 WAITING 会把它们的恢复材料删掉。"""
    schedule_id, revision_id = await seed_schedule(tenant)
    external = await seed_execution(
        tenant, schedule_id, revision_id, status="WAITING_EXTERNAL", finished=False
    )
    human = await seed_execution(
        tenant, schedule_id, revision_id, status="WAITING_HUMAN", finished=False
    )
    for execution_id in (external, human):
        await add_checkpoint(tenant, execution_id)
        await add_delivery(tenant, execution_id, status="SENT")

    report = await run_retention(tenant)

    assert report.executions == 0
    assert {row["id"] for row in await rows(tenant, "task_execution")} == {external, human}
    assert len(await rows(tenant, "task_execution_checkpoint")) == 2
    assert len(await rows(tenant, "task_delivery")) == 2


async def test_concurrent_answer_keeps_recovery_material(tenant: TenantContext) -> None:
    """等待中的执行在被回答前后都不得被清理：回答只是把它放回队列。"""
    schedule_id, revision_id = await seed_schedule(tenant)
    execution_id = await seed_execution(
        tenant, schedule_id, revision_id, status="WAITING_HUMAN", finished=False
    )
    await add_checkpoint(tenant, execution_id)
    interaction_id = await add_interaction(tenant, execution_id, status="OPEN")

    assert (await run_retention(tenant)).executions == 0

    factory = get_session_factory()
    async with factory() as session, session.begin():
        await HumanInteractionService(session, now=NOW).respond(
            interaction_id,
            tenant_id=tenant.tenant_id,
            actor_user_id=OWNER,
            agent_id=AGENT,
            response={"value": "继续"},
        )

    assert (await run_retention(tenant)).executions == 0
    kept = await rows(tenant, "task_execution", f"id = '{execution_id}'")
    assert [row["status"] for row in kept] == ["QUEUED"]
    assert len(await rows(tenant, "task_execution_checkpoint")) == 1
    assert [row["status"] for row in await rows(tenant, "task_human_interaction")] == ["RESOLVED"]


async def test_terminal_execution_awaiting_delivery_is_kept_until_sent(
    tenant: TenantContext,
) -> None:
    schedule_id, revision_id = await seed_schedule(tenant)
    execution_id = await seed_execution(tenant, schedule_id, revision_id, age_days=120)
    delivery_id = await add_delivery(tenant, execution_id, status="PENDING")

    assert (await run_retention(tenant)).executions == 0
    assert len(await rows(tenant, "task_execution", f"id = '{execution_id}'")) == 1

    async with tenant.session_factory() as session, session.begin():
        await session.execute(
            sa.update(TaskDelivery).where(TaskDelivery.id == delivery_id).values(status="SENT")
        )

    assert (await run_retention(tenant)).executions == 1
    assert await rows(tenant, "task_execution") == []
    assert await rows(tenant, "task_delivery") == []


async def test_pending_result_outbox_keeps_the_execution(tenant: TenantContext) -> None:
    schedule_id, revision_id = await seed_schedule(tenant)
    execution_id = await seed_execution(tenant, schedule_id, revision_id, age_days=120)
    await add_outbox(tenant, execution_id, status="PENDING")

    assert (await run_retention(tenant)).executions == 0

    async with tenant.session_factory() as session, session.begin():
        await session.execute(
            sa.text("UPDATE task.runtime_result_outbox SET status = 'SENT' WHERE task_id = :id"),
            {"id": execution_id},
        )

    report = await run_retention(tenant)
    assert report.executions == 1
    assert report.outbox_rows == 1
    assert await rows(tenant, "task_execution") == []


async def test_cutoff_boundary_is_strict(tenant: TenantContext) -> None:
    schedule_id, revision_id = await seed_schedule(tenant)
    # `finished_at = create_time + 1 分钟`：age=90 天 + 1 分钟 ⇒ finished_at 正好落在边界上。
    inside = await seed_execution(
        tenant, schedule_id, revision_id, age_days=RETENTION_DAYS + 1 / 1440,
    )
    outside = await seed_execution(tenant, schedule_id, revision_id, age_days=RETENTION_DAYS + 0.01)

    report = await run_retention(tenant)

    assert report.executions == 1
    assert {row["id"] for row in await rows(tenant, "task_execution")} == {inside}
    assert outside not in {row["id"] for row in await rows(tenant, "task_execution")}


async def test_forward_cursor_advances_across_bounded_batches(tenant: TenantContext) -> None:
    """有界批 + 前进游标：更早的被保护行不挡路，三批各删一条且不重复。"""
    schedule_id, revision_id = await seed_schedule(tenant)
    waiting = await seed_execution(  # 更早，但还在等：不是候选，也不挡后面的候选
        tenant, schedule_id, revision_id, status="WAITING_EXTERNAL", age_days=200, finished=False
    )
    expired = [
        await seed_execution(tenant, schedule_id, revision_id, age_days=age)
        for age in (150, 140, 130)
    ]

    first = await run_retention(tenant, batch_size=1)
    assert first.executions == 1
    assert first.inspected_executions == 1
    assert first.cursor is not None and first.cursor.execution is not None

    second = await run_retention(tenant, batch_size=1, cursor=first.cursor)
    assert second.executions == 1
    assert second.cursor is not None and second.cursor.execution is not None

    third = await run_retention(tenant, batch_size=1, cursor=second.cursor)
    assert third.executions == 1
    # 正好取满一批时还不能断言扫完：再问一次，空批才是"到底了"。
    assert third.cursor is not None
    done = await run_retention(tenant, batch_size=1, cursor=third.cursor)
    assert done.executions == 0 and done.cursor is None

    assert {row["id"] for row in await rows(tenant, "task_execution")} == {waiting}
    for execution_id in expired:
        assert execution_id not in {row["id"] for row in await rows(tenant, "task_execution")}


async def _purge_tenant(tenant: TenantContext, tenant_id: str) -> None:
    """删掉本用例故意造在别的租户名下的行（共享库不留跨用例残留）。"""
    async with tenant.session_factory() as session:
        # Schedule.current_revision_id 是指向 Revision 的循环 FK：先摘引用再删 Revision。
        await session.execute(
            sa.text(
                "UPDATE task.task_schedule SET current_revision_id = NULL WHERE tenant_id = :tenant"
            ),
            {"tenant": tenant_id},
        )
        for table in (
            "task_event",
            "task_delivery_attempt",
            "task_delivery",
            "task_human_interaction",
            "task_execution_checkpoint",
            "task_execution",
            "schedule_fire",
            "task_schedule_revision",
            "task_schedule",
        ):
            await session.execute(
                sa.text(f"DELETE FROM task.{table} WHERE tenant_id = :tenant"),
                {"tenant": tenant_id},
            )
        await session.commit()


async def test_other_tenants_are_never_touched(tenant: TenantContext) -> None:
    other_tenant = f"{tenant.tenant_id}-other"
    try:
        schedule_id, revision_id = await seed_schedule(tenant, tenant_id=other_tenant)
        await seed_execution(
            tenant, schedule_id, revision_id, age_days=200, tenant_id=other_tenant
        )
        await seed_fire(tenant, schedule_id, revision_id, age_days=200, tenant_id=other_tenant)

        report = await run_retention(tenant)

        assert report.executions == 0 and report.fires == 0
        async with tenant.session_factory() as session:
            remaining = await session.scalar(
                sa.select(sa.func.count())
                .select_from(TaskExecution)
                .where(TaskExecution.tenant_id == other_tenant)
            )
        assert remaining == 1
    finally:
        await _purge_tenant(tenant, other_tenant)


class _StubSettingsClient:
    def __init__(self, schedule: dict[str, int]) -> None:
        self._snapshot = PlatformSettingsSnapshot(revision=3, settings={"schedule": schedule})
        self.calls: list[str] = []

    async def fetch_snapshot(self, *, tenant_id: str, trace_id: str = "") -> PlatformSettingsSnapshot:
        self.calls.append(tenant_id)
        return self._snapshot


async def test_loop_reads_both_windows_from_platform_settings(tenant: TenantContext) -> None:
    """两个窗口都来自设置分组：执行 40 天、Fire 10 天，窗口 30/7 ⇒ 两者都过期。"""
    schedule_id, revision_id = await seed_schedule(tenant)
    await seed_execution(tenant, schedule_id, revision_id, age_days=40)
    await seed_fire(tenant, schedule_id, revision_id, age_days=10)

    client = _StubSettingsClient({"max_execution_history_days": 30, "max_fire_history_days": 7})
    loop = ScheduleRetentionLoop(
        get_session_factory(),
        client,
        tenant_id=tenant.tenant_id,
        interval_sec=30,
    )
    tick = await loop.run_once(now=NOW)

    assert client.calls == [tenant.tenant_id]
    assert tick.executions == 1
    assert tick.fires == 1
    assert tick.settings_revision == 3


async def test_fire_history_states_the_retention_window(
    tenant: TenantContext,
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schedule_id, revision_id = await seed_schedule(tenant)
    await seed_fire(tenant, schedule_id, revision_id, age_days=1, scheduled_at=NOW)
    monkeypatch.setattr(
        app.state,
        "platform_settings_client",
        _StubSettingsClient({"max_execution_history_days": 30, "max_fire_history_days": 7}),
        raising=False,
    )
    response = await client.get(
        f"/internal/schedules/{schedule_id}/fires",
        headers=internal_service_headers(tenant.tenant_id, **{"X-Actor-User-Id": str(OWNER)}),
    )

    assert response.status_code == 200, response.text
    retention = response.json()["data"]["retention"]
    assert retention["fire_history_days"] == 7
    assert retention["execution_history_days"] == 30
    assert retention["cleaned_before"] is not None
    assert retention["oldest_retained_at"] is not None


async def test_fire_history_paging_keeps_every_retained_record(tenant: TenantContext) -> None:
    """保留期内的分页不丢页：按 `scheduled_at DESC, id` 稳定排序走完就是全集。"""
    schedule_id, revision_id = await seed_schedule(tenant)
    expected = set()
    for index in range(5):
        fire_id = await seed_fire(
            tenant,
            schedule_id,
            revision_id,
            age_days=1,
            scheduled_at=NOW - timedelta(days=index),
        )
        expected.add(str(fire_id))

    async with get_session_factory()() as session:
        service = ScheduleReadService(session)
        schedule = await session.get(TaskSchedule, schedule_id)
        assert schedule is not None
        seen: list[str] = []
        for page in (1, 2, 3):
            history = await service.fire_history(
                schedule=schedule,
                page=page,
                page_size=2,
                window=HistoryWindow(fire_history_days=7, execution_history_days=30),
            )
            seen.extend(str(item.fire_id) for item in history.items)
            assert history.retention is not None

    assert len(seen) == len(set(seen)) == 5
    assert set(seen) == expected
