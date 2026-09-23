import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

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
    lease_owner: str | None = "instance-a",
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
            lease_owner=lease_owner,
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
            cancelled = await canceller.cancel_run(started.run_id, tenant.tenant_id)
            assert cancelled.status == RunStatus.RUNNING
            assert cancelled.cancel_requested is True

        remaining = [event async for event in iterator]
        assert remaining[-1].type == "run.completed"
        assert remaining[-1].data["status"] == "CANCELLED"

    async with session_factory() as session:
        run = await session.get(RunRecord, started.run_id)
        assert run is not None
        assert run.status == "CANCELLED"
        assert run.end_time is not None
        _, event_types = await _business_events(session, started.conversation_id)
        assert event_types == ["USER_MESSAGE", "CANCEL"]


async def test_cancel_run_returns_cancelling_for_running_run(
    tenant: TenantContext,
    fake_resolve: FakeResolveClient,
) -> None:
    run_id = await _insert_run(tenant, "RUNNING")
    async with get_session_factory()() as session:
        service = RunService(session, fake_resolve, "instance-a")
        run = await service.cancel_run(run_id, tenant.tenant_id)
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


async def test_client_disconnect_leaves_run_for_reaper(
    tenant: TenantContext,
    fake_resolve: FakeResolveClient,
    executor_factory: ExecutorFactory,
) -> None:
    """断流不强制取消：执行停止后租约过期由 Reaper 回收（E-07 语义）。"""
    session_factory = get_session_factory()
    async with session_factory() as session:
        service = RunService(session, fake_resolve, "instance-a", executor_factory=executor_factory)
        started = await service.start(_request(tenant), tenant.tenant_id)
        iterator = started.events
        first = await anext(iterator)
        assert first.type == "run.created"

        await iterator.aclose()

    async with session_factory() as session:
        run = await session.get(RunRecord, started.run_id)
        assert run is not None
        assert run.status == RunStatus.RUNNING
        assert run.cancel_requested is False


async def _business_events(
    session: AsyncSession,
    conversation_id: uuid.UUID,
) -> tuple[list[int], list[str]]:
    rows = (
        await session.scalars(
            select(CanonicalEvent)
            .where(
                CanonicalEvent.conversation_id == conversation_id,
                CanonicalEvent.stream_type.is_(None),
            )
            .order_by(CanonicalEvent.seq)
        )
    ).all()
    return [row.seq for row in rows], [row.event_type for row in rows]


class RecordingCredentialsClient:
    def __init__(self, *, api_key: str = "sk-api09", secret: str = "mcp-secret") -> None:
        self.api_key = api_key
        self.secret = secret
        self.payloads: list[dict[str, object]] = []

    async def resolve_credentials(
        self, *, tenant_id: str, payload: dict[str, object], trace_id: str = ""
    ) -> dict[str, object]:
        self.payloads.append(payload)
        mcp_ids = payload.get("mcp_server_ids") or []
        return {
            "model": {"id": str(payload.get("model_id")), "api_key": self.api_key},
            "mcp_servers": [
                {"mcp_server_id": str(mcp_id), "auth_secret": self.secret} for mcp_id in mcp_ids
            ],
        }


async def test_resume_uses_api09_credentials_in_memory(
    tenant: TenantContext,
    fake_resolve: FakeResolveClient,
) -> None:
    """resume 按冻结主键走 API-09；密钥只进内存 ExecutorRequest，不落 Snapshot。"""
    from muad_agent_runtime.application.executor import ExecutorRequest, RunExecutor

    from tests.agent_runtime.conftest import FakeExecutor

    resolved = fake_resolve.response
    _, run_id = await _seed_waiting_run_with_snapshot(tenant, resolved)
    seen: list[ExecutorRequest] = []

    async def factory(request: ExecutorRequest) -> RunExecutor:
        seen.append(request)
        return FakeExecutor(request)

    credentials = RecordingCredentialsClient()
    async with get_session_factory()() as session:
        service = RunService(
            session,
            fake_resolve,
            "instance-a",
            executor_factory=factory,
            credentials_client=credentials,
        )
        started = await service.resume(run_id, "answer", tenant.tenant_id, idempotency_key="k-1")
        [event async for event in started.events]

    assert seen[0].model.api_key == "sk-api09"
    assert seen[0].credentials.mcp_secrets == {
        str(resolved.mcp_servers[0].mcp_server_id): "mcp-secret"
    }
    payload = credentials.payloads[0]
    assert payload["execution_ref"] == {"type": "RUN", "id": str(run_id)}
    assert payload["model_id"] == str(resolved.model.id)

    async with get_session_factory()() as session:
        from muad_agent_runtime.infrastructure.models.runtime import RuntimeSnapshot

        snapshot = await session.scalar(
            select(RuntimeSnapshot).where(RuntimeSnapshot.run_id == run_id)
        )
        assert snapshot is not None
        assert "api_key" not in snapshot.model_json


async def _seed_waiting_run_with_snapshot(
    tenant: TenantContext,
    resolved: Any,
) -> tuple[uuid.UUID, uuid.UUID]:
    from muad_agent_runtime.application.run_service import build_snapshot
    from muad_agent_runtime.infrastructure.models.runtime import RunInterrupt

    conv_id, run_id = uuid.uuid4(), uuid.uuid4()
    async with get_session_factory()() as session:
        session.add(
            Conversation(
                id=conv_id,
                tenant_id=tenant.tenant_id,
                user_id=tenant.platform_user_id,
                agent_id=tenant.agent_id,
                status="ACTIVE",
                last_seq=0,
            )
        )
        run = RunRecord(
            id=run_id,
            tenant_id=tenant.tenant_id,
            conversation_id=conv_id,
            user_id=tenant.platform_user_id,
            agent_id=tenant.agent_id,
            status="WAITING_INPUT",
            input_text="hi",
            trace_id=uuid.uuid4().hex,
            cancel_requested=False,
        )
        session.add(run)
        snapshot = build_snapshot(run_id=run_id, tenant_id=tenant.tenant_id, resolved=resolved)
        session.add(snapshot)
        await session.flush()
        run.snapshot_id = snapshot.id
        session.add(
            RunInterrupt(
                tenant_id=tenant.tenant_id,
                run_id=run_id,
                conversation_id=conv_id,
                interrupt_type="CONFIRM",
                prompt_text="continue?",
                options_json=["yes", "no"],
                status="WAITING",
            )
        )
        await session.commit()
    return conv_id, run_id


async def test_inbound_channel_reaches_executor_as_delivery_route(
    tenant: TenantContext,
    fake_resolve: FakeResolveClient,
    executor_factory: ExecutorFactory,
) -> None:
    """入站渠道持久化到 Run，并作为后台 Task/Schedule 的 delivery_route 传给执行器。"""
    captured: list[Any] = []

    async def capturing_factory(request: Any) -> Any:
        captured.append(request)
        return await executor_factory(request)

    request = RunRequest(
        agent_id=tenant.agent_id,
        platform_user_id=tenant.platform_user_id,
        channel=ChannelContext(
            type="WECOM", bot_id="bot-1", external_user_id="wotv-9", external_conversation_id="conv-9"
        ),
        message=MessageInput(id=f"msg-{uuid.uuid4()}", text="hi"),
    )
    async with get_session_factory()() as session:
        service = RunService(session, fake_resolve, "instance-a", executor_factory=capturing_factory)
        started = await service.start(request, tenant.tenant_id)
        _ = [event async for event in started.events]

    route = captured[0].run_context.delivery_route
    assert route is not None
    assert (route.bot_id, route.external_user_id, route.external_conversation_id) == (
        "bot-1",
        "wotv-9",
        "conv-9",
    )
    async with get_session_factory()() as session:
        run = await session.get(RunRecord, started.run_id)
    assert run is not None and run.channel_json["external_user_id"] == "wotv-9"
