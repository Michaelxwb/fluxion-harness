"""Connector 入站批次：文字+多附件、超时、重复与数量上限。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from muad_contracts import AttachmentRef, InboundEvent
from muad_im_core.inbound_batch import (
    BATCH_STATE_PUBLISHED,
    BatchFrame,
    BatchSettings,
    InboundBatchCoordinator,
)
from muad_im_core.inbound_batch_journal import BatchRecord

pytestmark = pytest.mark.unit


class MemoryJournal:
    def __init__(self) -> None:
        self.batches: dict[str, BatchRecord] = {}
        self.members: dict[tuple[str, str, str], str] = {}

    async def load(self, *, tenant_id: str, bot_id: str, batch_id: str) -> BatchRecord | None:
        return self.batches.get(batch_id)

    async def save(self, *, tenant_id: str, bot_id: str, batch_id: str, payload: BatchRecord) -> None:
        await asyncio.sleep(0)
        self.batches[batch_id] = payload.model_copy(deep=True)
        for message_id in payload.members:
            self.members[(tenant_id, bot_id, message_id)] = batch_id

    async def member_batch(self, *, tenant_id: str, bot_id: str, message_id: str) -> str | None:
        return self.members.get((tenant_id, bot_id, message_id))

    async def unfinished(self, *, tenant_id: str, bot_id: str) -> list[BatchRecord]:
        return [r for r in self.batches.values() if r.state != BATCH_STATE_PUBLISHED]


class Publisher:
    def __init__(self) -> None:
        self.events: list[tuple[InboundEvent, tuple[str, ...]]] = []

    async def __call__(self, event: InboundEvent, member_ids: Any) -> None:
        self.events.append((event, tuple(member_ids)))


def _frame(message_id: str, *, text: str = "", media: bool = False) -> BatchFrame:
    return BatchFrame(
        channel="WECOM",
        tenant_id="tenant",
        bot_id="bot",
        external_user_id="user",
        external_conversation_id=None,
        message_id=message_id,
        kind="media" if media else "text",
        text=text,
        reply_ref=f"reply-{message_id}",
    )


def _event(message_id: str, *, text: str = "", attachments: tuple[AttachmentRef, ...] = ()) -> InboundEvent:
    return InboundEvent(
        event_id=__import__("uuid").uuid4(),
        channel="WECOM",
        tenant_id="tenant",
        bot_id="bot",
        external_message_id=message_id,
        external_user_id="user",
        text=text,
        attachments=attachments,
        received_at=datetime.now(UTC),
    )


def _ref(name: str) -> AttachmentRef:
    return AttachmentRef(
        storage_key=f"inbound/{name}",
        kind="DOCUMENT",
        media_type="application/pdf",
        size=1,
        filename=name,
        checksum="sha256:" + "a" * 64,
        source_channel="WECOM",
    )


async def _wait_published(publisher: Publisher, count: int = 1) -> None:
    for _ in range(100):
        if len(publisher.events) >= count:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"expected {count} published events, got {len(publisher.events)}")


async def test_text_and_multiple_media_merge_into_one_event() -> None:
    journal, publisher = MemoryJournal(), Publisher()
    coordinator = InboundBatchCoordinator(
        journal=journal,
        publish=publisher,
    )
    settings = BatchSettings(quiet_ms=20, max_wait_ms=500, max_attachments=5)
    assert await coordinator.register(_frame("t1", text="解析附件"), settings)
    await coordinator.complete(_event("t1", text="解析附件"))
    assert await coordinator.register(_frame("f1", media=True), settings)
    await coordinator.complete(_event("f1", attachments=(_ref("a.pdf"),)))
    assert await coordinator.register(_frame("f2", media=True), settings)
    await coordinator.complete(_event("f2", attachments=(_ref("b.pdf"),)))

    await _wait_published(publisher)
    event, ids = publisher.events[0]
    assert event.external_message_id == "t1"
    assert event.text == "解析附件"
    assert [ref.filename for ref in event.attachments] == ["a.pdf", "b.pdf"]
    assert ids == ("t1", "f1", "f2")
    assert event.require_complete_attachments is True


async def test_pending_media_times_out_without_running_text_only() -> None:
    journal, publisher = MemoryJournal(), Publisher()
    coordinator = InboundBatchCoordinator(journal=journal, publish=publisher)
    settings = BatchSettings(quiet_ms=10, max_wait_ms=40, max_attachments=5)
    await coordinator.register(_frame("t1", text="解析附件"), settings)
    await coordinator.complete(_event("t1", text="解析附件"))
    await coordinator.register(_frame("f1", media=True), settings)
    # f1 never materializes.

    await _wait_published(publisher)
    event, ids = publisher.events[0]
    assert ids == ("t1", "f1")
    assert event.text == "解析附件"
    assert event.attachments == ()
    assert event.attachment_failures[0].code == "ATTACHMENT_FETCH_TIMEOUT"
    assert event.require_complete_attachments is True


async def test_second_text_flushes_previous_batch() -> None:
    journal, publisher = MemoryJournal(), Publisher()
    coordinator = InboundBatchCoordinator(journal=journal, publish=publisher)
    settings = BatchSettings(quiet_ms=1000, max_wait_ms=2000, max_attachments=5)
    await coordinator.register(_frame("t1", text="第一句"), settings)
    await coordinator.complete(_event("t1", text="第一句"))
    await coordinator.register(_frame("t2", text="第二句"), settings)
    await coordinator.complete(_event("t2", text="第二句"))

    await _wait_published(publisher)
    assert [event.text for event, _ in publisher.events] == ["第一句"]
    # Second batch is still waiting for its quiet window; flush it explicitly.
    await coordinator.flush_route(_frame("t2"), "test")
    assert [event.text for event, _ in publisher.events] == ["第一句", "第二句"]


async def test_count_limit_rejects_whole_batch() -> None:
    journal, publisher = MemoryJournal(), Publisher()
    coordinator = InboundBatchCoordinator(journal=journal, publish=publisher)
    settings = BatchSettings(quiet_ms=1000, max_wait_ms=2000, max_attachments=1)
    await coordinator.register(_frame("t1", text="解析"), settings)
    await coordinator.complete(_event("t1", text="解析"))
    await coordinator.register(_frame("f1", media=True), settings)
    await coordinator.complete(_event("f1", attachments=(_ref("a.pdf"),)))
    await coordinator.register(_frame("f2", media=True), settings)

    await _wait_published(publisher)
    event, ids = publisher.events[0]
    assert ids == ("t1", "f1", "f2")
    assert event.attachment_failures[0].code == "ATTACHMENT_COUNT_EXCEEDED"
    assert event.require_complete_attachments is True


async def test_published_member_is_not_registered_again() -> None:
    journal, publisher = MemoryJournal(), Publisher()
    coordinator = InboundBatchCoordinator(journal=journal, publish=publisher)
    settings = BatchSettings(quiet_ms=10, max_wait_ms=200, max_attachments=5)
    await coordinator.register(_frame("t1", text="解析"), settings)
    await coordinator.complete(_event("t1", text="解析"))
    await _wait_published(publisher)

    assert await coordinator.register(_frame("t1", text="解析"), settings) is False
    await asyncio.sleep(0.05)
    assert len(publisher.events) == 1
    assert journal.batches[next(iter(journal.batches))].state == BATCH_STATE_PUBLISHED


async def test_explicit_flush_is_owned_by_batch_task_and_suspend_unblocks_caller() -> None:
    journal = MemoryJournal()
    entered = asyncio.Event()
    publishing_tasks: list[str] = []

    async def blocked_publish(event, members) -> None:
        publishing_tasks.append(asyncio.current_task().get_name())
        entered.set()
        await asyncio.Event().wait()

    coordinator = InboundBatchCoordinator(journal=journal, publish=blocked_publish)
    frame = _frame("t1", text="hello")
    await coordinator.register(frame, BatchSettings(1000, 2000, 5))
    await coordinator.complete(_event("t1", text="hello"))
    caller = asyncio.create_task(coordinator.flush_route(frame, "command"), name="incoming-command")
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        assert publishing_tasks[0].startswith("inbound.batch:")
        await coordinator.suspend_bot("tenant", "bot")
        await asyncio.wait_for(caller, timeout=1)
        assert coordinator.pending_count == 0
        assert next(iter(journal.batches.values())).state == "PUBLISHING"
    finally:
        await coordinator.suspend_bot("tenant", "bot")
        caller.cancel()
        await asyncio.gather(caller, return_exceptions=True)
