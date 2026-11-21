"""Batch lifecycle against real, isolated Redis; no journal or publisher mocks."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence

import pytest
from muad_common import process_settings
from muad_common.redis_client import RedisClient, create_redis_client
from muad_contracts import AttachmentFailure, InboundEvent
from muad_contracts.im import inbound_batch_key, inbound_stream_key
from muad_im_core.inbound_batch import (
    BatchSettings,
    InboundBatchCoordinator,
    RedisBatchJournal,
)
from muad_im_core.inbound_batch_journal import BatchRecord
from muad_im_core.ingress import InboundPublisher

from tests.acceptance.datastores import _release_redis_db, isolated_redis_url
from tests.im_core.test_inbound_batch import _event, _frame

pytestmark = [pytest.mark.integration, pytest.mark.own_datastore]


@pytest.fixture
async def batch_redis() -> AsyncIterator[RedisClient]:
    settings = process_settings()
    assert settings.redis_url is not None
    owner = f"im-batch-{uuid.uuid4().hex}"
    url = await asyncio.to_thread(isolated_redis_url, settings.redis_url, owner)
    client = create_redis_client(url, decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose()
        await asyncio.to_thread(_release_redis_db, settings.redis_url, owner)


async def _wait_until(check: Callable[[], Awaitable[bool]]) -> None:
    async with asyncio.timeout(2):
        while not await check():
            await asyncio.sleep(0.01)


def _coordinator(redis: RedisClient) -> InboundBatchCoordinator:
    publisher = InboundPublisher(redis, shards=1, stream_maxlen=100, lost_after_sec=60)

    async def publish(event: InboundEvent, members: Sequence[str]) -> None:
        await publisher.publish(event, dedupe_ids=members)

    return InboundBatchCoordinator(journal=RedisBatchJournal(redis), publish=publish)


async def test_quiet_window_publishes_without_another_message(batch_redis: RedisClient) -> None:
    coordinator = _coordinator(batch_redis)
    frame, event = _frame("natural", text="hello"), _event("natural", text="hello")
    try:
        await coordinator.register(frame, BatchSettings(quiet_ms=10, max_wait_ms=100, max_attachments=5))
        await coordinator.complete(event)

        async def published() -> bool:
            return await batch_redis.xlen(inbound_stream_key(event, shards=1)) == 1

        await _wait_until(published)
        batch_id = await RedisBatchJournal(batch_redis).member_batch(
            tenant_id="tenant", bot_id="bot", message_id="natural"
        )

        async def confirmed() -> bool:
            assert batch_id is not None
            raw = await batch_redis.get(inbound_batch_key("tenant", "bot", batch_id))
            return raw is not None and json.loads(raw)["state"] == "PUBLISHED"

        await _wait_until(confirmed)
    finally:
        await coordinator.flush_all()


async def test_merge_keeps_materialization_failure(batch_redis: RedisClient) -> None:
    coordinator = _coordinator(batch_redis)
    settings = BatchSettings(quiet_ms=1000, max_wait_ms=2000, max_attachments=5)
    try:
        await coordinator.register(_frame("t1", text="read"), settings)
        await coordinator.complete(_event("t1", text="read"))
        await coordinator.register(_frame("f1", media=True), settings)
        failed = _event("f1").model_copy(update={
            "attachment_keys": ("f1#0",),
            "attachment_failures": (AttachmentFailure(key="f1#0", code="ATTACHMENT_FETCH_FAILED"),),
        })
        await coordinator.complete(failed)
        await coordinator.flush_route(_frame("t1"), "command")
        entries = await batch_redis.xrange(inbound_stream_key(failed, shards=1))
        merged = json.loads(entries[0][1]["event"])
        assert merged["require_complete_attachments"] is True
        assert merged["attachment_failures"] == [{"key": "f1#0", "code": "ATTACHMENT_FETCH_FAILED"}]
    finally:
        await coordinator.flush_all()


@pytest.mark.parametrize("complete_media", [True, False])
async def test_restart_keeps_original_primary_and_failure_boundary(
    batch_redis: RedisClient, complete_media: bool,
) -> None:
    first = _coordinator(batch_redis)
    settings = BatchSettings(quiet_ms=1000, max_wait_ms=2000, max_attachments=5)
    await first.register(_frame("t1", text="read this"), settings)
    await first.complete(_event("t1", text="read this"))
    await first.register(_frame("f1", media=True), settings)
    if complete_media:
        await first.complete(_event("f1"))
    await first.suspend_bot("tenant", "bot")

    second = _coordinator(batch_redis)
    try:
        await second.recover("tenant", "bot")
        await second.flush_all()
        entries = await batch_redis.xrange(inbound_stream_key(_event("t1"), shards=1))
        assert len(entries) == 1
        event = InboundEvent.model_validate_json(entries[0][1]["event"])
        assert event.external_message_id == "t1"
        assert event.text == "read this"
        assert bool(event.attachment_failures) is not complete_media
        assert event.require_complete_attachments
        assert not await second.register(_frame("f1", media=True), settings)
    finally:
        await second.flush_all()


async def test_publish_confirmation_failure_replays_same_event(batch_redis: RedisClient) -> None:
    class FailingConfirmation(RedisBatchJournal):
        async def save(self, *, tenant_id: str, bot_id: str, batch_id: str, payload: BatchRecord) -> None:
            if payload.state == "PUBLISHED":
                raise OSError("confirmation interrupted")
            await super().save(tenant_id=tenant_id, bot_id=bot_id, batch_id=batch_id, payload=payload)

    publisher = InboundPublisher(batch_redis, shards=1, stream_maxlen=100, lost_after_sec=60)

    async def publish(event: InboundEvent, members: Sequence[str]) -> None:
        await publisher.publish(event, dedupe_ids=members)

    first = InboundBatchCoordinator(journal=FailingConfirmation(batch_redis), publish=publish)
    await first.register(_frame("t1", text="hello"), BatchSettings(10, 100, 5))
    await first.complete(_event("t1", text="hello"))

    async def reached_stream() -> bool:
        return await batch_redis.xlen(inbound_stream_key(_event("t1"), shards=1)) == 1

    await _wait_until(reached_stream)
    await first.suspend_bot("tenant", "bot")
    second = _coordinator(batch_redis)
    await second.recover("tenant", "bot")
    await second.flush_all()
    assert await reached_stream()
    assert not await RedisBatchJournal(batch_redis).unfinished(tenant_id="tenant", bot_id="bot")
