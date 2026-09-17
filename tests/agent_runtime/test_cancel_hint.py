import uuid

import pytest
from conftest import FakeResolveClient, TenantContext
from muad_agent_runtime.application.run_service import RunService
from muad_agent_runtime.infrastructure.cancel_hint import (
    CANCEL_HINT_TTL_SEC,
    CANCEL_HINT_VALUE,
    NullCancelHintStore,
    RedisCancelHintStore,
    create_cancel_hint_store,
)
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import Conversation, RunRecord
from redis.exceptions import ConnectionError as RedisConnectionError


class StubRedisClient:
    def __init__(self, *, ping_ok: bool = True) -> None:
        self.calls: list[tuple[str, int, str]] = []
        self.closed = False
        self._ping_ok = ping_ok

    async def setex(self, name: str, time: int, value: str) -> bool:
        self.calls.append((name, time, value))
        return True

    async def ping(self) -> bool:
        if not self._ping_ok:
            raise RedisConnectionError("redis down")
        return True

    async def aclose(self) -> None:
        self.closed = True


class RecordingCancelHintStore:
    def __init__(self) -> None:
        self.run_ids: list[uuid.UUID] = []
        self.closed = False

    async def set(self, run_id: uuid.UUID) -> None:
        self.run_ids.append(run_id)

    async def aclose(self) -> None:
        self.closed = True


async def _insert_run(tenant: TenantContext, status: str) -> uuid.UUID:
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
        )
        session.add(run)
        await session.commit()
        return run.id


async def test_redis_store_writes_setex_with_ttl_and_closes() -> None:
    client = StubRedisClient()
    store = RedisCancelHintStore(client)
    run_id = uuid.uuid4()

    await store.set(run_id)
    await store.aclose()

    assert client.calls == [(f"run:cancel:{run_id}", CANCEL_HINT_TTL_SEC, CANCEL_HINT_VALUE)]
    assert client.closed is True


async def test_null_store_is_a_noop() -> None:
    store = NullCancelHintStore()

    await store.set(uuid.uuid4())
    await store.aclose()


async def test_create_returns_null_without_redis_url() -> None:
    store = await create_cancel_hint_store(None)
    assert isinstance(store, NullCancelHintStore)


async def test_create_falls_back_to_null_when_ping_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = StubRedisClient(ping_ok=False)

    with caplog.at_level("WARNING"):
        store = await create_cancel_hint_store("redis://cache:6379/0", client_factory=lambda url: client)

    assert isinstance(store, NullCancelHintStore)
    assert client.closed is True
    assert "cancel_hint_redis_unavailable" in caplog.text


async def test_create_returns_redis_store_when_ping_succeeds() -> None:
    client = StubRedisClient()

    store = await create_cancel_hint_store("redis://cache:6379/0", client_factory=lambda url: client)

    assert isinstance(store, RedisCancelHintStore)
    await store.set(uuid.uuid4())
    assert client.calls


async def test_cancel_running_run_writes_hint(
    tenant: TenantContext,
    fake_resolve: FakeResolveClient,
) -> None:
    run_id = await _insert_run(tenant, "RUNNING")
    hints = RecordingCancelHintStore()
    async with get_session_factory()() as session:
        service = RunService(session, fake_resolve, "instance-a", cancel_hints=hints)
        await service.cancel_run(run_id)

    assert hints.run_ids == [run_id]


async def test_cancel_waiting_input_run_skips_hint(
    tenant: TenantContext,
    fake_resolve: FakeResolveClient,
) -> None:
    run_id = await _insert_run(tenant, "WAITING_INPUT")
    hints = RecordingCancelHintStore()
    async with get_session_factory()() as session:
        service = RunService(session, fake_resolve, "instance-a", cancel_hints=hints)
        run = await service.cancel_run(run_id)

    assert run.status == "CANCELLED"
    assert hints.run_ids == []


async def test_cancel_terminal_run_is_idempotent_without_hint(
    tenant: TenantContext,
    fake_resolve: FakeResolveClient,
) -> None:
    run_id = await _insert_run(tenant, "COMPLETED")
    hints = RecordingCancelHintStore()
    async with get_session_factory()() as session:
        service = RunService(session, fake_resolve, "instance-a", cancel_hints=hints)
        run = await service.cancel_run(run_id)

    assert run.status == "COMPLETED"
    assert hints.run_ids == []


async def test_cancel_active_writes_hint(
    tenant: TenantContext,
    fake_resolve: FakeResolveClient,
) -> None:
    run_id = await _insert_run(tenant, "RUNNING")
    hints = RecordingCancelHintStore()
    async with get_session_factory()() as session:
        service = RunService(session, fake_resolve, "instance-a", cancel_hints=hints)
        await service.cancel_active(tenant.agent_id, tenant.platform_user_id, tenant.tenant_id)

    assert hints.run_ids == [run_id]
