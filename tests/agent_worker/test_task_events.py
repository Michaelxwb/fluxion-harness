"""多态 TaskEvent 的序号分配与 append-only 语义（设计 §3.3.9 / §3.5.10）。

真实边界：并发 PG Session → 实体行锁（或 ARTIFACT/ASYNC 的实体 advisory 锁）→
`UNIQUE(tenant_id, entity_type, entity_id, seq)`。不 mock 数据库，也不以单会话顺序
调用冒充并发。
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from muad_agent_worker.application.task_events import (
    EntityType,
    TaskEventSeed,
    TaskEventType,
    append_event,
    append_events,
)
from muad_agent_worker.infrastructure.models.task import TaskEvent
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_worker.conftest import TenantContext
from agent_worker.helpers import fetch_events, persist_task


async def _append_and_commit(
    session: AsyncSession,
    tenant: TenantContext,
    entity_id: uuid.UUID,
    event_type: TaskEventType,
) -> None:
    await append_event(
        session,
        tenant_id=tenant.tenant_id,
        entity_type=EntityType.EXECUTION,
        entity_id=entity_id,
        event_type=event_type,
    )
    await session.commit()
    await session.close()


async def test_same_entity_appends_are_serialized(tenant: TenantContext) -> None:
    """同一 Execution 的并发追加必须被行锁串行化，后来者等待前一个事务提交。"""
    task = await persist_task(tenant)
    factory = tenant.session_factory

    holder = factory()
    await holder.begin()
    await append_event(
        holder,
        tenant_id=tenant.tenant_id,
        entity_type=EntityType.EXECUTION,
        entity_id=task.id,
        event_type=TaskEventType.QUEUED,
    )

    waiter = factory()
    await waiter.begin()
    blocked = asyncio.create_task(
        _append_and_commit(waiter, tenant, task.id, TaskEventType.CLAIMED)
    )
    await asyncio.sleep(0.3)
    assert not blocked.done(), "并发追加没有被同一实体的行锁阻塞，序号分配存在竞态"

    await holder.commit()
    await holder.close()
    await asyncio.wait_for(blocked, timeout=5)

    events = await fetch_events(tenant, task.id)
    assert sorted(event.seq for event in events) == [1, 2]


async def test_concurrent_appends_never_duplicate_seq(tenant: TenantContext) -> None:
    """N 个并发会话追加到同一 Execution，seq 不重号且连续。"""
    task = await persist_task(tenant)
    factory = tenant.session_factory
    event_types = [
        TaskEventType.CLAIMED,
        TaskEventType.WAITING_EXTERNAL,
        TaskEventType.CLAIMED,
        TaskEventType.WAITING_HUMAN,
        TaskEventType.CLAIMED,
    ]

    sessions = [factory() for _ in event_types]
    for session in sessions:
        await session.begin()

    results = await asyncio.gather(
        *(
            _append_and_commit(session, tenant, task.id, event_type)
            for session, event_type in zip(sessions, event_types, strict=True)
        ),
        return_exceptions=True,
    )

    failures = [item for item in results if isinstance(item, BaseException)]
    assert not failures, f"并发追加失败（序号冲突）: {failures!r}"

    events = await fetch_events(tenant, task.id)
    seqs = sorted(event.seq for event in events)
    assert seqs == list(range(1, len(event_types) + 1)), f"seq 不连续: {seqs}"


async def test_different_entities_do_not_block_each_other(tenant: TenantContext) -> None:
    """不同实体的并发追加互不阻塞：各自从 1 开始，不共享计数器。"""
    first = await persist_task(tenant)
    second = await persist_task(tenant)
    factory = tenant.session_factory

    holder = factory()
    await holder.begin()
    await append_event(
        holder,
        tenant_id=tenant.tenant_id,
        entity_type=EntityType.EXECUTION,
        entity_id=first.id,
        event_type=TaskEventType.QUEUED,
    )

    other = factory()
    await other.begin()
    # 同一张表、不同实体：必须立刻完成，不等待 first 的行锁。
    await asyncio.wait_for(_append_and_commit(other, tenant, second.id, TaskEventType.QUEUED), timeout=5)

    await holder.commit()
    await holder.close()

    assert [event.seq for event in await fetch_events(tenant, first.id)] == [1]
    assert [event.seq for event in await fetch_events(tenant, second.id)] == [1]


async def test_artifact_entity_serializes_on_advisory_lock(tenant: TenantContext) -> None:
    """ARTIFACT 跨 schema 没有物理行：序号分配退化到实体 advisory 锁，仍不重号。"""
    artifact_id = uuid.uuid4()
    factory = tenant.session_factory
    sessions = [factory() for _ in range(4)]
    for session in sessions:
        await session.begin()

    async def append(session: AsyncSession) -> None:
        await append_event(
            session,
            tenant_id=tenant.tenant_id,
            entity_type=EntityType.ARTIFACT,
            entity_id=artifact_id,
            event_type=TaskEventType.REGISTERED,
        )
        await session.commit()
        await session.close()

    results = await asyncio.gather(*(append(session) for session in sessions), return_exceptions=True)
    assert not [item for item in results if isinstance(item, BaseException)], results

    events = await fetch_events(tenant, artifact_id, entity_type=EntityType.ARTIFACT)
    assert sorted(event.seq for event in events) == [1, 2, 3, 4]


async def test_seq_is_scoped_to_entity_type_and_id(tenant: TenantContext) -> None:
    """seq 的维度是 (tenant, entity_type, entity_id)：同 id 不同实体类型各自计数。"""
    shared_id = uuid.uuid4()
    await persist_task(tenant, id=shared_id)
    async with tenant.session_factory() as session, session.begin():
        await append_event(
            session,
            tenant_id=tenant.tenant_id,
            entity_type=EntityType.EXECUTION,
            entity_id=shared_id,
            event_type=TaskEventType.QUEUED,
        )
        await append_event(
            session,
            tenant_id=tenant.tenant_id,
            entity_type=EntityType.SCHEDULE,
            entity_id=shared_id,
            event_type=TaskEventType.CREATED,
        )

    executions = await fetch_events(tenant, shared_id)
    schedules = await fetch_events(tenant, shared_id, entity_type=EntityType.SCHEDULE)
    assert [event.seq for event in executions] == [1]
    assert [event.seq for event in schedules] == [1]


async def test_timeline_is_monotonic(tenant: TenantContext) -> None:
    """一条 Execution 的 Timeline 序号严格单调递增。"""
    task = await persist_task(tenant)
    sequence = [
        TaskEventType.QUEUED,
        TaskEventType.CLAIMED,
        TaskEventType.WAITING_EXTERNAL,
        TaskEventType.CLAIMED,
        TaskEventType.COMPLETED,
    ]
    async with tenant.session_factory() as session:
        async with session.begin():
            for event_type in sequence:
                await append_event(
                    session,
                    tenant_id=tenant.tenant_id,
                    entity_type=EntityType.EXECUTION,
                    entity_id=task.id,
                    event_type=event_type,
                )

    events = await fetch_events(tenant, task.id)
    seqs = [event.seq for event in events]
    assert seqs == list(range(1, len(sequence) + 1)), seqs
    assert [event.event_type for event in events] == [str(item) for item in sequence]


async def test_rollback_leaves_no_event(tenant: TenantContext) -> None:
    """事务回滚后不残留事件（事件与状态同事务）。"""
    task = await persist_task(tenant)
    async with tenant.session_factory() as session:
        await session.begin()
        await append_event(
            session,
            tenant_id=tenant.tenant_id,
            entity_type=EntityType.EXECUTION,
            entity_id=task.id,
            event_type=TaskEventType.QUEUED,
        )
        await session.rollback()

    assert await fetch_events(tenant, task.id) == []


async def test_batch_append_assigns_contiguous_seq(tenant: TenantContext) -> None:
    """同一批次内多个事件按传入顺序分配连续序号。"""
    task = await persist_task(tenant)
    async with tenant.session_factory() as session:
        async with session.begin():
            await append_events(
                session,
                [
                    TaskEventSeed(tenant.tenant_id, EntityType.EXECUTION, task.id, TaskEventType.QUEUED),
                    TaskEventSeed(tenant.tenant_id, EntityType.EXECUTION, task.id, TaskEventType.CLAIMED),
                    TaskEventSeed(tenant.tenant_id, EntityType.EXECUTION, task.id, TaskEventType.COMPLETED),
                ],
            )

    events = await fetch_events(tenant, task.id)
    assert [event.event_type for event in events] == ["QUEUED", "CLAIMED", "COMPLETED"]
    assert [event.seq for event in events] == [1, 2, 3]


async def test_payload_is_redacted(tenant: TenantContext) -> None:
    """事件 payload 落库前脱敏，密钥不进入 task_event。"""
    task = await persist_task(tenant)
    async with tenant.session_factory() as session:
        async with session.begin():
            await append_event(
                session,
                tenant_id=tenant.tenant_id,
                entity_type=EntityType.EXECUTION,
                entity_id=task.id,
                event_type=TaskEventType.QUEUED,
                payload={
                    "api_key": "sk-live-1234567890",
                    "nested": {"bot_secret": "topsecret"},
                    "note": "authorization: Bearer abcdef",
                    "safe": "visible",
                },
            )

    stored = (await fetch_events(tenant, task.id))[0].payload_json
    assert stored["api_key"] == "***"
    assert stored["nested"]["bot_secret"] == "***"
    assert "abcdef" not in stored["note"]
    assert stored["safe"] == "visible"
    assert "sk-live-1234567890" not in str(stored)


async def test_trace_id_is_persisted(tenant: TenantContext) -> None:
    """事件可携带 trace_id。"""
    task = await persist_task(tenant)
    async with tenant.session_factory() as session:
        async with session.begin():
            await append_events(
                session,
                [
                    TaskEventSeed(
                        tenant.tenant_id,
                        EntityType.EXECUTION,
                        task.id,
                        TaskEventType.CLAIMED,
                        trace_id="trace-abc",
                    )
                ],
            )

    assert (await fetch_events(tenant, task.id))[0].trace_id == "trace-abc"


async def test_occurred_at_is_business_time(tenant: TenantContext) -> None:
    """`occurred_at` 是业务发生时间，与入库时间 `create_time` 分开保存。"""
    task = await persist_task(tenant)
    business_time = datetime.now(UTC) - timedelta(minutes=5)
    async with tenant.session_factory() as session:
        async with session.begin():
            await append_event(
                session,
                tenant_id=tenant.tenant_id,
                entity_type=EntityType.EXECUTION,
                entity_id=task.id,
                event_type=TaskEventType.COMPLETED,
                occurred_at=business_time,
            )

    event = (await fetch_events(tenant, task.id))[0]
    assert event.occurred_at == business_time
    assert event.create_time > event.occurred_at + timedelta(minutes=1)


@pytest.mark.parametrize(
    ("entity_type", "event_type"),
    [
        (EntityType.SCHEDULE, TaskEventType.COMPLETED),
        (EntityType.EXECUTION, TaskEventType.DST_GAP_SKIPPED),
        (EntityType.FIRE, TaskEventType.CLAIMED),
        (EntityType.HUMAN, TaskEventType.SENT),
    ],
)
async def test_event_type_is_scoped_to_entity(
    tenant: TenantContext, entity_type: EntityType, event_type: TaskEventType
) -> None:
    """§3.5.10 的事件类型按实体分域：跨实体的事件名在写入前就拒绝。"""
    async with tenant.session_factory() as session:
        await session.begin()
        with pytest.raises(ValueError):
            await append_event(
                session,
                tenant_id=tenant.tenant_id,
                entity_type=entity_type,
                entity_id=uuid.uuid4(),
                event_type=event_type,
            )
        await session.rollback()


async def test_no_physical_fk_by_design(tenant: TenantContext) -> None:
    """多态审计表不建物理 FK（RULE-data-001 的审计引用例外）：写入方保证归属。"""
    ghost = uuid.uuid4()
    async with tenant.session_factory() as session, session.begin():
        event = await append_event(
            session,
            tenant_id=tenant.tenant_id,
            entity_type=EntityType.EXECUTION,
            entity_id=ghost,
            event_type=TaskEventType.QUEUED,
        )
    assert event.entity_id == ghost


async def test_unique_key_includes_tenant(tenant: TenantContext) -> None:
    """唯一键含 tenant_id：不同租户可以对同一实体 id 写同一序号。"""
    shared_id = uuid.uuid4()
    other_tenant = f"{tenant.tenant_id}-other"
    async with tenant.session_factory() as session:
        async with session.begin():
            for current in (tenant.tenant_id, other_tenant):
                await append_event(
                    session,
                    tenant_id=current,
                    entity_type=EntityType.ASYNC,
                    entity_id=shared_id,
                    event_type=TaskEventType.SUBMITTED,
                )
        tenant_id, seq = (
            await session.execute(
                select(TaskEvent.tenant_id, TaskEvent.seq).where(
                    TaskEvent.entity_type == EntityType.ASYNC.value,
                    TaskEvent.entity_id == shared_id,
                    TaskEvent.tenant_id == other_tenant,
                )
            )
        ).one()
        await session.execute(
            TaskEvent.__table__.delete().where(TaskEvent.tenant_id == other_tenant)
        )
        await session.commit()
    assert (tenant_id, seq) == (other_tenant, 1)
