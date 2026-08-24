from __future__ import annotations

import asyncio
from collections.abc import Mapping

import pytest
from sqlalchemy import select
from tests.console_helpers import console_stack, create_resource, publish_resource

from fluxion.registry.schema import outbox_events
from fluxion.resources import ResourceKind
from fluxion.runtime.hot_reload import ConfigChangeEvent
from fluxion.services.outbox import OutboxWorker, RedisStreamsConfigEventPublisher


class FlakyEventPublisher:
    def __init__(self) -> None:
        self.calls = 0
        self.events: list[ConfigChangeEvent] = []

    async def publish(self, event: ConfigChangeEvent) -> None:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("event bus unavailable")
        self.events.append(event)


async def test_E_C106_event_failure_stays_pending_and_can_retry() -> None:
    async with console_stack() as stack:
        await create_resource(
            stack.client,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="assistant",
        )
        response = await publish_resource(
            stack.client,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="assistant",
            request_id="req-E-C106",
        )
        publisher = FlakyEventPublisher()
        worker = OutboxWorker(
            stack.store,
            publisher,
            worker_id="worker-E-C106",
            base_backoff_seconds=0,
        )

        first = await worker.run_once()
        async with stack.store.engine.connect() as connection:  # type: ignore[attr-defined]
            pending = (await connection.execute(select(outbox_events))).mappings().one()
        second = await worker.run_once()
        async with stack.store.engine.connect() as connection:  # type: ignore[attr-defined]
            recovered = (await connection.execute(select(outbox_events))).mappings().one()

    assert response.status_code == 200
    assert response.json()["data"]["event_status"] == "pending"
    assert first.retried == 1
    assert pending["status"] == "pending"
    assert pending["attempt_count"] == 1
    assert second.published == 1
    assert recovered["status"] == "published"
    assert recovered["attempt_count"] == 1
    assert publisher.calls == 2
    assert len(publisher.events) == 1


class FakeRedisStreamClient:
    def __init__(self, *, fail_after_write: bool = False, delay_seconds: float = 0) -> None:
        self.fail_after_write = fail_after_write
        self.delay_seconds = delay_seconds
        self.streams: dict[str, dict[str, dict[str, str]]] = {}

    async def xadd(
        self,
        stream: str,
        fields: Mapping[str, str],
        *,
        id: str,
    ) -> object:
        await asyncio.sleep(self.delay_seconds)
        entries = self.streams.setdefault(stream, {})
        if id in entries:
            raise RuntimeError("duplicate stream id")
        entries[id] = dict(fields)
        if self.fail_after_write:
            raise RuntimeError("connection lost after write")
        return id

    async def xrange(
        self,
        stream: str,
        *,
        min: str,
        max: str,
        count: int,
    ) -> list[object]:
        del count
        entry = self.streams.get(stream, {}).get(min)
        return [] if min != max or entry is None else [(min, entry)]


async def test_redis_stream_publisher_uses_revision_id_and_recovers_ambiguous_write() -> None:
    client = FakeRedisStreamClient(fail_after_write=True)
    publisher = RedisStreamsConfigEventPublisher(client, timeout_seconds=0.1)
    event = ConfigChangeEvent(
        tenant_id="tenant-a",
        kind=ResourceKind.RUNTIME_PROFILE,
        resource_id="assistant",
        version="2",
        revision=9,
    )

    await publisher.publish(event)
    await publisher.publish(event)

    stream = client.streams["fluxion:config-changed:tenant-a"]
    assert list(stream) == ["9-0"]
    assert stream["9-0"]["event_type"] == "config.changed"


async def test_redis_stream_publisher_has_bounded_timeout() -> None:
    client = FakeRedisStreamClient(delay_seconds=0.05)
    publisher = RedisStreamsConfigEventPublisher(client, timeout_seconds=0.01)
    event = ConfigChangeEvent(
        tenant_id="tenant-a",
        kind=ResourceKind.RUNTIME_PROFILE,
        resource_id="assistant",
        version="2",
        revision=10,
    )

    with pytest.raises(TimeoutError):
        await publisher.publish(event)
