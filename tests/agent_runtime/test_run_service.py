import uuid
from datetime import UTC, datetime, timedelta

from conftest import FakeResolveClient, TenantContext
from muad_agent_runtime.application.executor import ExecutorFactory
from muad_agent_runtime.application.run_service import RunService, reap_abandoned_runs
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import (
    CanonicalEvent,
    Conversation,
    RunRecord,
)
from muad_contracts import ChannelContext, MessageInput, RunRequest, RunStatus
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


def _request(tenant: TenantContext, text: str = "hello") -> RunRequest:
    return RunRequest(
        agent_id=tenant.agent_id,
        platform_user_id=tenant.platform_user_id,
        channel=ChannelContext(type="WECOM", bot_id="bot-1"),
        message=MessageInput(id=f"msg-{uuid.uuid4()}", text=text),
    )


async def _insert_run(
    tenant: TenantContext,
    status: str,
    *,
    lease_until: datetime | None = None,
) -> uuid.UUID:
    async with get_session_factory()() as session:
        conversation = Conversation(
            tenant_id=tenant.tenant_id,
            user_id=tenant.platform_user_id,
            agent_id=tenant.agent_id,
            status="ACTIVE",
            last_seq=0,
        )
        session.add(conversation)
        await session.flush()
        run = RunRecord(
            tenant_id=tenant.tenant_id,
            conversation_id=conversation.id,
            user_id=tenant.platform_user_id,
            agent_id=tenant.agent_id,
            status=status,
            input_text="pending input",
            trace_id=uuid.uuid4().hex,
            cancel_requested=False,
            lease_until=lease_until,
        )
        session.add(run)
        await session.commit()
        return run.id


async def test_cooperative_cancel_stops_running_execution(
    tenant: TenantContext,
    fake_resolve: FakeResolveClient,
    executor_factory: ExecutorFactory,
) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        service = RunService(session, fake_resolve, "instance-a", executor_factory=executor_factory)
        started = await service.start(_request(tenant), tenant.tenant_id)
        iterator = started.events
        first = await anext(iterator)
        assert first.type == "run.created"

        async with session_factory() as cancel_session:
            canceller = RunService(cancel_session, fake_resolve, "instance-b")
            cancelled = await canceller.cancel_run(started.run_id)
            assert cancelled.status == RunStatus.RUNNING
            assert cancelled.cancel_requested is True

        remaining = [event async for event in iterator]
        assert [event.type for event in remaining] == ["run.completed"]
        assert remaining[0].data["status"] == "CANCELLED"

    async with session_factory() as session:
        run = await session.get(RunRecord, started.run_id)
        assert run is not None
        assert run.status == "CANCELLED"
        assert run.end_time is not None
        _, event_types = await _conversation_events(session, started.conversation_id)
        assert event_types == ["USER_MESSAGE", "CANCEL"]


async def test_cancel_run_returns_cancelling_for_running_run(
    tenant: TenantContext,
    fake_resolve: FakeResolveClient,
) -> None:
    run_id = await _insert_run(tenant, "RUNNING")
    async with get_session_factory()() as session:
        service = RunService(session, fake_resolve, "instance-a")
        run = await service.cancel_run(run_id)
        assert run.status == RunStatus.RUNNING
        assert run.cancel_requested is True


async def test_reap_abandoned_marks_expired_lease_failed(
    tenant: TenantContext,
    fake_resolve: FakeResolveClient,
) -> None:
    expired_lease = datetime.now(UTC) - timedelta(seconds=60)
    fresh_lease = datetime.now(UTC) + timedelta(seconds=60)
    expired_run_id = await _insert_run(tenant, "RUNNING", lease_until=expired_lease)
    fresh_run_id = await _insert_run(tenant, "RUNNING", lease_until=fresh_lease)

    async with get_session_factory()() as session:
        reaped = await RunService(session, fake_resolve, "reaper").reap_abandoned()
    assert reaped == 1

    async with get_session_factory()() as session:
        expired = await session.get(RunRecord, expired_run_id)
        assert expired is not None
        assert expired.status == "FAILED"
        assert expired.error_code == "RUN_ABANDONED"
        assert expired.end_time is not None
        fresh = await session.get(RunRecord, fresh_run_id)
        assert fresh is not None
        assert fresh.status == "RUNNING"


async def test_reap_abandoned_module_function_counts_rows(
    tenant: TenantContext,
) -> None:
    await _insert_run(tenant, "RUNNING", lease_until=datetime.now(UTC) - timedelta(seconds=10))
    reaped = await reap_abandoned_runs(get_session_factory())
    assert reaped == 1


async def test_renew_lease_extends_only_running_runs(
    tenant: TenantContext,
    fake_resolve: FakeResolveClient,
) -> None:
    stale_lease = datetime.now(UTC) + timedelta(seconds=1)
    running_id = await _insert_run(tenant, "RUNNING", lease_until=stale_lease)
    completed_id = await _insert_run(tenant, "COMPLETED")

    async with get_session_factory()() as session:
        service = RunService(session, fake_resolve, "instance-a")
        assert await service._renew_lease(running_id) is True
        assert await service._renew_lease(completed_id) is False

    async with get_session_factory()() as session:
        run = await session.get(RunRecord, running_id)
        assert run is not None
        assert run.heartbeat_at is not None
        assert run.lease_until is not None
        assert run.lease_until > stale_lease


async def test_client_disconnect_cancels_running_run(
    tenant: TenantContext,
    fake_resolve: FakeResolveClient,
    executor_factory: ExecutorFactory,
) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        service = RunService(session, fake_resolve, "instance-a", executor_factory=executor_factory)
        started = await service.start(_request(tenant), tenant.tenant_id)
        iterator = started.events
        first = await anext(iterator)
        assert first.type == "run.created"

        await service.on_client_disconnect(started.run_id)
        remaining = [event async for event in iterator]
        assert remaining[0].data["status"] == "CANCELLED"

    async with session_factory() as session:
        run = await session.get(RunRecord, started.run_id)
        assert run is not None
        assert run.status == "CANCELLED"


async def _conversation_events(
    session: AsyncSession,
    conversation_id: uuid.UUID,
) -> tuple[list[int], list[str]]:
    rows = (
        await session.scalars(
            select(CanonicalEvent)
            .where(CanonicalEvent.conversation_id == conversation_id)
            .order_by(CanonicalEvent.seq)
        )
    ).all()
    return [row.seq for row in rows], [row.event_type for row in rows]
