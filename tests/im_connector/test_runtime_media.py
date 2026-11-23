"""Download admission, time budgets and command isolation are bounded."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest
from muad_im_connector.channels.wecom.sdk_port import WeComMediaRef
from muad_im_core.inbound_batch import BatchSettings
from muad_im_gateway.channels.base import AttachmentFetchError

from tests.im_connector.test_runtime_inbound import _MemoryJournal, _message, _runtime

pytestmark = pytest.mark.unit


class _Settings:
    async def batch(self, tenant_id: str) -> BatchSettings:
        return BatchSettings(quiet_ms=10, max_wait_ms=300, max_attachments=5)


class _SlowMedia:
    name = "WECOM"

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.active = 0
        self.peak = 0

    def attachment_count(self, envelope) -> int:
        return 1

    async def fetch_attachment(self, envelope, index, *, max_bytes):
        self.active += 1
        self.peak = max(self.peak, self.active)
        self.started.set()
        try:
            await self.release.wait()
            raise AttachmentFetchError("ATTACHMENT_FETCH_FAILED")
        finally:
            self.active -= 1


def _media(mid: str, user: str = "user"):
    return replace(_message(mid, ""), external_user_id=user, message_type="file",
                   media=(WeComMediaRef(url="http://media.invalid/file"),))


async def test_slow_download_registers_next_frame_and_does_not_block_commands() -> None:
    journal = _MemoryJournal()
    runtime, _replies, publisher = _runtime(batch_journal=journal, attachment_concurrency=1)
    adapter = _SlowMedia()
    runtime._adapter, runtime._attachment_store = adapter, object()
    runtime._platform_settings = _Settings()
    consumer = asyncio.create_task(runtime._inbound_loop())
    try:
        runtime.raw_sink(_media("f1"))
        await asyncio.wait_for(adapter.started.wait(), timeout=1)
        runtime.raw_sink(_media("f2"))
        runtime.raw_sink(replace(_message("command", "/new"), external_user_id="other"))
        runtime.raw_sink(replace(_message("ordinary", "hello"), external_user_id="other"))
        await asyncio.wait_for(runtime._queue.join(), timeout=1)
        assert {identity[2] for identity in journal.members} == {"f1", "f2", "ordinary"}
        async with asyncio.timeout(1):
            while not any(e.external_message_id == "ordinary" for e in publisher.events):
                await asyncio.sleep(0.001)
        assert {e.external_message_id for e in publisher.events} == {"command", "ordinary"}
        assert adapter.active == 1
        assert adapter.peak == 1
        adapter.release.set()
        await asyncio.wait_for(runtime._batch_coordinator.flush_all(), timeout=1)
        batch = next(event for event in publisher.events if event.external_message_id == "f1")
        assert batch.require_complete_attachments
        assert batch.attachment_failures
    finally:
        await runtime._cancel_tasks((consumer, *runtime._media_tasks))
        await runtime._batch_coordinator.flush_all()


async def test_waiting_for_download_slot_is_included_in_batch_timeout() -> None:
    runtime, _replies, publisher = _runtime(batch_journal=_MemoryJournal(), attachment_concurrency=1)
    adapter = _SlowMedia()
    runtime._adapter, runtime._attachment_store = adapter, object()
    runtime._platform_settings = _Settings()
    await runtime._publish_inbound(_media("f1"))
    await runtime._publish_inbound(_media("f2", "other"))
    try:
        async with asyncio.timeout(1):
            while len(publisher.events) < 2 or runtime._media_tasks:
                await asyncio.sleep(0.01)
        assert adapter.peak == 1
        assert adapter.active == 0
        assert all(e.attachment_failures[0].code == "ATTACHMENT_FETCH_TIMEOUT" for e in publisher.events)
    finally:
        await runtime._cancel_tasks(tuple(runtime._media_tasks))
        await runtime._batch_coordinator.flush_all()


async def test_media_task_capacity_rejects_excess_instead_of_building_unbounded_tasks() -> None:
    runtime, _replies, publisher = _runtime(batch_journal=_MemoryJournal(), inbound_queue_size=1)
    runtime._adapter, runtime._attachment_store = _SlowMedia(), object()
    runtime._platform_settings = _Settings()
    await runtime._publish_inbound(_media("f1"))
    await runtime._publish_inbound(_media("f2"))
    try:
        assert len(runtime._media_tasks) == 1
    finally:
        await runtime._cancel_tasks(tuple(runtime._media_tasks))
        await runtime._batch_coordinator.flush_all()
    assert len(publisher.events) == 1
    assert any(f.key == "f2#0" for f in publisher.events[0].attachment_failures)


async def test_dead_inbound_task_is_not_reported_ready() -> None:
    runtime, _replies, _publisher = _runtime()
    runtime._ready = True
    runtime._snapshot = SimpleNamespace(is_ready=lambda: True)
    task = asyncio.create_task(asyncio.sleep(60), name="connector.inbound")
    runtime._tasks = [task]
    assert runtime.ready
    await runtime._cancel_tasks((task,))
    assert not runtime.ready
