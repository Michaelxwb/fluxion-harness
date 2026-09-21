"""TaskEvent 序号分配与 append-only 语义（B-104 / RULE-worker-001）。

真实边界：并发 PG Session → Task 行锁 → task_event 唯一约束。
不 mock 数据库，也不以单会话顺序调用冒充并发。
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from conftest import TenantContext
from helpers import fetch_events, persist_task
from muad_agent_worker.application.task_events import (
    TaskEventSeed,
    TaskEventType,
    append_event,
    append_events,
)
from sqlalchemy.ext.asyncio import AsyncSession


async def _append_and_commit(
    session: AsyncSession, tenant: TenantContext, task_id: uuid.UUID, event_type: TaskEventType
) -> None:
    await append_event(session, tenant_id=tenant.tenant_id, task_id=task_id, event_type=event_type)
    await session.commit()
    await session.close()


async def test_same_task_appends_are_serialized(tenant: TenantContext) -> None:
    """同一 Task 的并发追加必须被行锁串行化，后来者等待前一个事务提交。"""
    task = await persist_task(tenant)
    factory = tenant.session_factory

    holder = factory()
    await holder.begin()
    await append_event(
        holder, tenant_id=tenant.tenant_id, task_id=task.id, event_type=TaskEventType.CREATED
    )

    waiter = factory()
    await waiter.begin()
    blocked = asyncio.create_task(
        _append_and_commit(waiter, tenant, task.id, TaskEventType.CLAIMED)
    )
    await asyncio.sleep(0.3)
    assert not blocked.done(), "并发追加没有被同一 Task 的行锁阻塞，序号分配存在竞态"

    await holder.commit()
    await holder.close()
    await asyncio.wait_for(blocked, timeout=5)

    events = await fetch_events(tenant, task.id)
    assert sorted(event.seq for event in events) == [1, 2]


async def test_concurrent_appends_never_duplicate_seq(tenant: TenantContext) -> None:
    """N 个并发会话追加到同一 Task，seq 不重号且连续。"""
    task = await persist_task(tenant)
    factory = tenant.session_factory
    event_types = [
        TaskEventType.CLAIMED,
        TaskEventType.RETRY,
        TaskEventType.CLAIMED,
        TaskEventType.RETRY,
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


async def test_timeline_is_monotonic_across_retries(tenant: TenantContext) -> None:
    """跨重试的 Timeline 序号严格单调递增。"""
    task = await persist_task(tenant)
    sequence = [
        TaskEventType.CREATED,
        TaskEventType.CLAIMED,
        TaskEventType.RETRY,
        TaskEventType.CLAIMED,
        TaskEventType.RETRY,
        TaskEventType.COMPLETED,
    ]
    async with tenant.session_factory() as session:
        async with session.begin():
            for event_type in sequence:
                await append_event(
                    session, tenant_id=tenant.tenant_id, task_id=task.id, event_type=event_type
                )

    events = await fetch_events(tenant, task.id)
    seqs = [event.seq for event in events]
    assert seqs == list(range(1, len(sequence) + 1)), seqs
    assert seqs == sorted(set(seqs)), f"序号必须严格单调: {seqs}"


async def test_rollback_leaves_no_event(tenant: TenantContext) -> None:
    """事务回滚后不残留事件（事件与状态同事务）。"""
    task = await persist_task(tenant)
    async with tenant.session_factory() as session:
        await session.begin()
        await append_event(
            session, tenant_id=tenant.tenant_id, task_id=task.id, event_type=TaskEventType.CREATED
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
                    TaskEventSeed(tenant.tenant_id, task.id, TaskEventType.CREATED),
                    TaskEventSeed(tenant.tenant_id, task.id, TaskEventType.CLAIMED),
                    TaskEventSeed(tenant.tenant_id, task.id, TaskEventType.COMPLETED),
                ],
            )

    events = await fetch_events(tenant, task.id)
    assert [event.event_type for event in events] == ["CREATED", "CLAIMED", "COMPLETED"]
    assert [event.seq for event in events] == [1, 2, 3]


async def test_payload_is_redacted(tenant: TenantContext) -> None:
    """事件 payload 落库前脱敏，密钥不进入 task_event。"""
    task = await persist_task(tenant)
    async with tenant.session_factory() as session:
        async with session.begin():
            await append_event(
                session,
                tenant_id=tenant.tenant_id,
                task_id=task.id,
                event_type=TaskEventType.CREATED,
                payload={
                    "api_key": "sk-live-1234567890",
                    "nested": {"bot_secret": "topsecret"},
                    "note": "authorization: Bearer abcdef",
                    "safe": "visible",
                },
            )

    events = await fetch_events(tenant, task.id)
    stored = events[0].payload_json
    assert stored["api_key"] == "***"
    assert stored["nested"]["bot_secret"] == "***"
    assert "abcdef" not in stored["note"]
    assert stored["safe"] == "visible"
    assert "sk-live-1234567890" not in str(stored)


async def test_waiting_and_fan_events_are_supported(tenant: TenantContext) -> None:
    """WAITING / FAN_OUT / FAN_IN 事件类型可用并落库。"""
    task = await persist_task(tenant)
    async with tenant.session_factory() as session:
        async with session.begin():
            await append_event(
                session, tenant_id=tenant.tenant_id, task_id=task.id, event_type=TaskEventType.WAITING
            )
            await append_event(
                session, tenant_id=tenant.tenant_id, task_id=task.id, event_type=TaskEventType.FAN_OUT
            )
            await append_event(
                session, tenant_id=tenant.tenant_id, task_id=task.id, event_type=TaskEventType.FAN_IN
            )

    events = await fetch_events(tenant, task.id)
    assert [event.event_type for event in events] == ["WAITING", "FAN_OUT", "FAN_IN"]


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
                        task.id,
                        TaskEventType.CLAIMED,
                        trace_id="trace-abc",
                    )
                ],
            )

    events = await fetch_events(tenant, task.id)
    assert events[0].trace_id == "trace-abc"


async def test_unknown_task_id_is_rejected(tenant: TenantContext) -> None:
    """事件必须挂在真实存在的 Task 上（同 schema 物理 FK）。"""
    from sqlalchemy.exc import IntegrityError

    async with tenant.session_factory() as session:
        await session.begin()
        with pytest.raises(IntegrityError):
            await append_event(
                session,
                tenant_id=tenant.tenant_id,
                task_id=uuid.uuid4(),
                event_type=TaskEventType.CREATED,
            )
            await session.commit()
