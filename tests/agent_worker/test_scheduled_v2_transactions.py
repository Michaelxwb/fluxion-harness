"""统一终态事务、锁序、幂等顺序与 PUSH 投递意图（设计 §3.3.12 / §3.5.1 / §3.5.3）。

真实边界：真实 PG 行锁与事务级 advisory 锁、`task_event` 唯一约束、outbox 的
`task_event_id` 单列 FK。并发用例都用两个真实 Session，不以顺序调用冒充竞态。
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from muad_agent_worker.application.batch_fanin import settle_child
from muad_agent_worker.application.task_events import EntityType, TaskEventType
from muad_agent_worker.application.task_service import TaskService
from muad_agent_worker.application.terminal_tasks import TerminalChange, write_terminal
from muad_agent_worker.infrastructure.models.runtime_operations import (
    RuntimeOperation,
    RuntimeResultOutbox,
)
from muad_agent_worker.infrastructure.models.task import (
    ScheduleFire,
    TaskDelivery,
    TaskEvent,
    TaskExecution,
    TaskSchedule,
    TaskScheduleRevision,
)
from muad_contracts import CompletionMode, TerminalStatus
from sqlalchemy.ext.asyncio import AsyncSession

from agent_worker.conftest import TenantContext
from tests.async_tool_helpers import seed_operation

pytestmark = pytest.mark.integration


async def _events(session: AsyncSession, execution_id: uuid.UUID) -> list[TaskEvent]:
    rows = await session.execute(
        sa.select(TaskEvent).where(
            TaskEvent.entity_type == EntityType.EXECUTION.value,
            TaskEvent.entity_id == execution_id,
        )
    )
    return list(rows.scalars().all())


async def _outbox_count(session: AsyncSession, execution_id: uuid.UUID) -> int:
    return int(
        await session.scalar(
            sa.select(sa.func.count())
            .select_from(RuntimeResultOutbox)
            .where(RuntimeResultOutbox.task_id == execution_id)
        )
    )


async def _seed_scheduled(
    tenant: TenantContext,
    *,
    delivery_intent: str = "NONE",
    with_route: bool = False,
) -> TaskExecution:
    """种一条完整的调度执行：Schedule + Revision + Fire + Execution（v2 三件套非空）。"""
    factory = tenant.session_factory
    now = datetime.now(UTC)
    async with factory() as session, session.begin():
        route_id = None
        if with_route:
            from muad_agent_worker.application.delivery_routes import upsert_delivery_route
            from muad_contracts import DeliveryRouteInput

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
            name="weekday policy check",
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
            mode="AGENTIC",
            instruction_raw="每天 9 点检查客户政策",
            normalized_goal="检查客户政策",
            recurrence_kind="CRON",
            cron_expr="0 9 * * *",
            timezone="Asia/Shanghai",
            schedule_policy={"concurrency_policy": "QUEUE", "max_concurrency": 1},
            execution_spec={"goal": "检查客户政策"},
            delivery_intent=delivery_intent,
            delivery_route_id=route_id,
            created_by=uuid.uuid4(),
        )
        session.add(revision)
        await session.flush()
        # `current_revision_id` 由确认事务回填（TASK-012）；测试不设，避免与按租户
        # 清扫 fixture 的删除顺序（revision → schedule）互相阻塞。
        fire = ScheduleFire(
            id=uuid.uuid4(),
            tenant_id=tenant.tenant_id,
            schedule_id=schedule.id,
            schedule_revision_id=revision.id,
            scheduled_at=now,
            status="EXECUTION_CREATED",
        )
        session.add(fire)
        await session.flush()
        execution = TaskExecution(
            id=uuid.uuid4(),
            tenant_id=tenant.tenant_id,
            schedule_id=schedule.id,
            schedule_fire_id=fire.id,
            schedule_revision_id=revision.id,
            scheduled_at=now,
            agent_id=schedule.agent_id,
            actor_user_id=schedule.owner_user_id,
            trigger_type="SCHEDULED",
            task_type="AGENTIC",
            status="QUEUED",
            input_json={},
            execution_snapshot_schema_version=4,
            execution_snapshot_json={"schema_version": 4},
            snapshot_hash="sha256:" + "a" * 64,
            idempotency_key=f"scheduled-{uuid.uuid4()}",
            priority=100,
            attempt=0,
            max_attempts=3,
            not_before=now,
            deadline_at=now + timedelta(hours=1),
        )
        session.add(execution)
        await session.flush()
        session.expunge_all()
    return execution


async def test_terminal_write_is_one_transaction(tenant: TenantContext) -> None:
    """状态 + finished_at + 事件 + outbox 同一事务；注入失败后三者一起回滚。"""
    factory = tenant.session_factory
    task, _, _ = await seed_operation(factory, tenant=tenant.tenant_id)
    moment = datetime.now(UTC)

    with pytest.raises(RuntimeError):
        async with factory() as session, session.begin():
            assert await write_terminal(
                session, task, TerminalChange(status=TerminalStatus.COMPLETED, result={"ok": True}), moment
            )
            raise RuntimeError("injected failure after terminal write")

    async with factory() as session:
        stored = await session.get(TaskExecution, task.id)
        assert stored.status == "QUEUED" and stored.finished_at is None
        assert await _events(session, task.id) == []
        assert await _outbox_count(session, task.id) == 0


async def test_committed_terminal_facts_agree(tenant: TenantContext) -> None:
    """提交后：finished_at、事件 occurred_at、outbox 引用的是同一条事件。"""
    factory = tenant.session_factory
    task, _, _ = await seed_operation(factory, tenant=tenant.tenant_id)
    moment = datetime.now(UTC).replace(microsecond=0)
    async with factory() as session, session.begin():
        assert await write_terminal(
            session, task, TerminalChange(status=TerminalStatus.COMPLETED, result={"ok": True}), moment
        )
    async with factory() as session:
        stored = await session.get(TaskExecution, task.id)
        event = (await _events(session, task.id))[0]
        outbox = (
            await session.scalars(
                sa.select(RuntimeResultOutbox).where(RuntimeResultOutbox.task_id == task.id)
            )
        ).one()
        assert stored.finished_at == moment
        assert stored.status == "COMPLETED"
        assert event.event_type == str(TaskEventType.COMPLETED)
        assert event.occurred_at == moment
        assert outbox.task_event_id == event.id
        assert outbox.payload_json["task_event_seq"] == event.seq


@pytest.mark.parametrize(
    "path",
    ["completed", "failed", "deadline", "queued-cancel", "waiting-cancel", "fan-in"],
)
async def test_every_root_terminal_path_enqueues_one_result(
    tenant: TenantContext, path: str
) -> None:
    """根终态的每条路径都只发布一次结果，且只写一条终态事件。"""
    factory = tenant.session_factory
    now = datetime.now(UTC)
    root, _, _ = await seed_operation(factory, tenant=tenant.tenant_id, task_status="RUNNING")
    if path in ("queued-cancel", "waiting-cancel"):
        async with factory() as session, session.begin():
            await session.execute(
                sa.update(TaskExecution)
                .where(TaskExecution.id == root.id)
                .values(
                    status="QUEUED" if path == "queued-cancel" else "WAITING_HUMAN",
                    lease_owner=None,
                    lease_until=None,
                )
            )
            assert (await TaskService(session).cancel(tenant.tenant_id, root.id))[0] == "CANCELLED"
    elif path == "fan-in":
        child, _, _ = await seed_operation(factory, tenant=tenant.tenant_id)
        async with factory() as session, session.begin():
            await session.execute(
                sa.update(TaskExecution)
                .where(TaskExecution.id == root.id)
                .values(wait_payload_json={"batch": {"aggregate_mode": "ALL", "concurrency": 2}})
            )
            await session.execute(
                sa.update(TaskExecution)
                .where(TaskExecution.id == child.id)
                .values(parent_id=root.id, root_id=root.id, tenant_id=root.tenant_id)
            )
            child = await session.get(TaskExecution, child.id)
            assert await write_terminal(
                session, child, TerminalChange(status=TerminalStatus.COMPLETED, result={}), now
            )
            assert (await settle_child(session, child, now)).aggregated
    else:
        change = {
            "completed": TerminalChange(status=TerminalStatus.COMPLETED, result={"ok": True}),
            "failed": TerminalChange(status=TerminalStatus.FAILED, error_code="TEST_FAILURE"),
            "deadline": TerminalChange(
                status=TerminalStatus.FAILED, error_code="TASK_DEADLINE_EXCEEDED"
            ),
        }[path]
        event_type = TaskEventType.DEADLINE_EXCEEDED if path == "deadline" else None
        async with factory() as session, session.begin():
            assert await write_terminal(session, root, change, now, event_type=event_type)

    async with factory() as session:
        assert await _outbox_count(session, root.id) == 1
        events = await _events(session, root.id)
        terminal = [
            event
            for event in events
            if event.event_type
            in {
                TaskEventType.COMPLETED,
                TaskEventType.FAILED,
                TaskEventType.CANCELLED,
                TaskEventType.DEADLINE_EXCEEDED,
            }
        ]
        assert len(terminal) == 1, [event.event_type for event in events]


async def test_child_terminal_never_publishes_root_result(tenant: TenantContext) -> None:
    """Child 终态不发布根结果，只由 fan-in 决定 Parent 是否聚合。"""
    factory = tenant.session_factory
    now = datetime.now(UTC)
    root, _, _ = await seed_operation(factory, tenant=tenant.tenant_id, task_status="RUNNING")
    child, _, _ = await seed_operation(factory, tenant=tenant.tenant_id)
    async with factory() as session, session.begin():
        await session.execute(
            sa.update(TaskExecution)
            .where(TaskExecution.id == child.id)
            .values(parent_id=root.id, root_id=root.id, tenant_id=root.tenant_id)
        )
        child = await session.get(TaskExecution, child.id)
        assert await write_terminal(
            session, child, TerminalChange(status=TerminalStatus.COMPLETED, result={}), now
        )
        assert (await settle_child(session, child, now)).aggregated
    async with factory() as session:
        assert await _outbox_count(session, child.id) == 0
        assert await _outbox_count(session, root.id) == 1


async def test_detach_root_does_not_publish(tenant: TenantContext) -> None:
    """DETACH 根不发布结果：结果由任务中心 pull / 投递表承载。"""
    factory = tenant.session_factory
    task, _, _ = await seed_operation(
        factory, tenant=tenant.tenant_id, mode=CompletionMode.DETACH, task_status="RUNNING"
    )
    async with factory() as session, session.begin():
        assert await write_terminal(
            session, task, TerminalChange(status=TerminalStatus.COMPLETED, result={}), datetime.now(UTC)
        )
    async with factory() as session:
        assert await _outbox_count(session, task.id) == 0
        assert len(await _events(session, task.id)) == 1


async def test_stale_lease_is_rejected_by_the_cas(tenant: TenantContext) -> None:
    """迟到 lease：owner/epoch/期限不匹配时 CAS 落空，不写事件也不写 outbox。"""
    factory = tenant.session_factory
    task, _, _ = await seed_operation(factory, tenant=tenant.tenant_id, task_status="RUNNING")
    now = datetime.now(UTC)
    async with factory() as session, session.begin():
        await session.execute(
            sa.update(TaskExecution)
            .where(TaskExecution.id == task.id)
            .values(lease_owner="worker-b", lease_epoch=7, lease_until=now + timedelta(minutes=1))
        )
    async with factory() as session, session.begin():
        written = await write_terminal(
            session,
            task,
            TerminalChange(status=TerminalStatus.COMPLETED, result={}),
            now,
            conditions=(
                TaskExecution.lease_owner == "worker-a",
                TaskExecution.lease_epoch == 6,
                TaskExecution.lease_until > now,
            ),
        )
        assert written is False
    async with factory() as session:
        stored = await session.get(TaskExecution, task.id)
        assert stored.status == "RUNNING" and stored.lease_owner == "worker-b"
        assert await _events(session, task.id) == []
        assert await _outbox_count(session, task.id) == 0


async def test_terminal_execution_cannot_be_reopened(tenant: TenantContext) -> None:
    """终态 Execution 不会被迟到的回答/取消改回 QUEUED（双侧 CAS 的 Worker 一侧）。"""
    factory = tenant.session_factory
    task, _, _ = await seed_operation(factory, tenant=tenant.tenant_id, task_status="RUNNING")
    now = datetime.now(UTC)
    async with factory() as session, session.begin():
        assert await write_terminal(
            session, task, TerminalChange(status=TerminalStatus.CANCELLED), now
        )
    async with factory() as session, session.begin():
        assert not await write_terminal(
            session, task, TerminalChange(status=TerminalStatus.COMPLETED, result={}), now
        )
    async with factory() as session:
        stored = await session.get(TaskExecution, task.id)
        assert stored.status == "CANCELLED"
        assert [event.event_type for event in await _events(session, task.id)] == ["CANCELLED"]


async def test_push_delivery_intent_is_written_with_the_terminal(tenant: TenantContext) -> None:
    """PUSH Revision：终态事务内建投递意图，delivery_key 稳定。"""
    factory = tenant.session_factory
    task = await _seed_scheduled(tenant, delivery_intent="PUSH", with_route=True)
    moment = datetime.now(UTC)
    async with factory() as session, session.begin():
        assert await write_terminal(
            session, task, TerminalChange(status=TerminalStatus.COMPLETED, result={}), moment
        )
    async with factory() as session:
        delivery = (
            await session.scalars(sa.select(TaskDelivery).where(TaskDelivery.execution_id == task.id))
        ).one()
        assert delivery.delivery_key == f"execution:{task.id}:final"
        assert delivery.delivery_kind == "PUSH" and delivery.status == "PENDING"


async def test_none_delivery_intent_creates_no_delivery(tenant: TenantContext) -> None:
    """NONE Revision（WebChat/MSSWWEB）：不建 task_delivery，结果靠任务中心 pull。"""
    factory = tenant.session_factory
    task = await _seed_scheduled(tenant, delivery_intent="NONE")
    async with factory() as session, session.begin():
        assert await write_terminal(
            session, task, TerminalChange(status=TerminalStatus.COMPLETED, result={}), datetime.now(UTC)
        )
    async with factory() as session:
        assert (
            await session.scalar(
                sa.select(sa.func.count())
                .select_from(TaskDelivery)
                .where(TaskDelivery.execution_id == task.id)
            )
            == 0
        )


async def test_lock_order_takes_the_operation_before_the_execution(tenant: TenantContext) -> None:
    """锁序第一段：终态写先拿 runtime_operation，再拿执行树。"""
    factory = tenant.session_factory
    task, _, _ = await seed_operation(factory, tenant=tenant.tenant_id, mode=CompletionMode.DETACH)
    async with factory() as session, session.begin():
        session.add(
            RuntimeOperation(
                id=uuid.uuid4(),
                tenant_id=task.tenant_id,
                operation_id=task.source_operation_id,
                source_run_id=task.source_run_id,
                source_tool_call_id="call-1",
                actor_user_id=task.actor_user_id,
            )
        )

    holder = factory()
    await holder.begin()
    await holder.execute(
        sa.select(RuntimeOperation.id)
        .where(RuntimeOperation.operation_id == task.source_operation_id)
        .with_for_update()
    )

    writer = factory()
    await writer.begin()
    blocked = asyncio.create_task(
        _write_and_commit(writer, task, TerminalChange(status=TerminalStatus.COMPLETED, result={}))
    )
    await asyncio.sleep(0.3)
    assert not blocked.done(), "终态写没有先等待 operation 锁"

    await holder.commit()
    await holder.close()
    assert await asyncio.wait_for(blocked, timeout=5)
    await writer.close()


async def _write_and_commit(
    session: AsyncSession, task: TaskExecution, change: TerminalChange
) -> bool:
    written = await write_terminal(session, task, change, datetime.now(UTC))
    await session.commit()
    return written


async def test_lock_order_takes_the_schedule_before_the_execution(tenant: TenantContext) -> None:
    """锁序第二段：调度执行先拿 Schedule 行锁，再进执行树。"""
    factory = tenant.session_factory
    task = await _seed_scheduled(tenant)

    holder = factory()
    await holder.begin()
    await holder.execute(
        sa.select(TaskSchedule.id).where(TaskSchedule.id == task.schedule_id).with_for_update()
    )

    writer = factory()
    await writer.begin()
    blocked = asyncio.create_task(
        _write_and_commit(writer, task, TerminalChange(status=TerminalStatus.COMPLETED, result={}))
    )
    await asyncio.sleep(0.3)
    assert not blocked.done(), "终态写没有先等待 Schedule 锁"

    await holder.commit()
    await holder.close()
    assert await asyncio.wait_for(blocked, timeout=5)
    await writer.close()


async def test_different_entities_do_not_share_a_sequence(tenant: TenantContext) -> None:
    """不同实体并发终态：各自从 seq=1 开始，互不阻塞。"""
    factory = tenant.session_factory
    first, _, _ = await seed_operation(factory, tenant=tenant.tenant_id)
    second, _, _ = await seed_operation(factory, tenant=tenant.tenant_id)
    now = datetime.now(UTC)

    results = await asyncio.gather(
        *(
            _terminal_in_new_session(factory, task, now)
            for task in (first, second)
        ),
        return_exceptions=True,
    )
    assert not [item for item in results if isinstance(item, BaseException)], results

    async with factory() as session:
        assert [event.seq for event in await _events(session, first.id)] == [1]
        assert [event.seq for event in await _events(session, second.id)] == [1]


async def _terminal_in_new_session(factory, task: TaskExecution, now: datetime) -> bool:
    async with factory() as session, session.begin():
        return await write_terminal(
            session, task, TerminalChange(status=TerminalStatus.COMPLETED, result={}), now
        )


async def test_payload_of_outbox_is_redacted(tenant: TenantContext) -> None:
    """outbox 载荷同样脱敏：密钥不进投递体。"""
    factory = tenant.session_factory
    task, _, _ = await seed_operation(factory, tenant=tenant.tenant_id)
    async with factory() as session, session.begin():
        assert await write_terminal(
            session,
            task,
            TerminalChange(
                status=TerminalStatus.COMPLETED, result={"api_key": "sk-live-should-not-leak"}
            ),
            datetime.now(UTC),
        )
    async with factory() as session:
        outbox = (
            await session.scalars(
                sa.select(RuntimeResultOutbox).where(RuntimeResultOutbox.task_id == task.id)
            )
        ).one()
        assert outbox.payload_json["result"]["api_key"] == "***"
