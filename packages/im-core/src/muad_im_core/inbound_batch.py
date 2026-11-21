"""One lifetime task per batch, immutable publication and recoverable metadata."""
from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from muad_contracts import AttachmentFailure, InboundEvent
from muad_logging import get_logger

from .inbound_batch_journal import (
    BATCH_STATE_PENDING,
    BATCH_STATE_PUBLISHED,
    BATCH_STATE_PUBLISHING,
    FRAME_MEDIA,
    FRAME_TEXT,
    BatchFrame,
    BatchJournal,
    BatchMember,
    BatchRecord,
    BatchSettings,
    RedisBatchJournal,
    record_batch_failure,
)

logger = get_logger(__name__)


@dataclass(slots=True)
class _Batch:
    id: str
    settings: BatchSettings
    deadline: float
    deadline_at: datetime
    members: dict[str, BatchMember] = field(default_factory=dict)
    last_ready_at: float = 0.0
    event: InboundEvent | None = None
    reason: str = "quiet"
    sealed: bool = False
    suspended: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    attempted: asyncio.Event = field(default_factory=asyncio.Event)
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    timer: asyncio.Task[None] | None = None

    def primary(self) -> BatchMember:
        return next((m for m in self.members.values() if m.frame.kind == FRAME_TEXT),
                    next(iter(self.members.values())))

    @property
    def route(self) -> str:
        return self.primary().frame.route


class InboundBatchCoordinator:
    def __init__(
        self, *, journal: BatchJournal,
        publish: Callable[[InboundEvent, Sequence[str]], Awaitable[None]],
        clock: Callable[[], float] = time.monotonic,
        retry_sec: float = 1.0, capacity: int = 10_000,
    ) -> None:
        if retry_sec <= 0 or capacity < 1:
            raise ValueError("batch retry and capacity must be positive")
        self._journal, self._publish, self._clock = journal, publish, clock
        self._retry_sec, self._capacity = retry_sec, capacity
        self._batches: dict[str, _Batch] = {}
        self._active: dict[str, _Batch] = {}
        self._member_index: dict[tuple[str, str, str], str] = {}
        self._stopped = False

    @property
    def healthy(self) -> bool:
        return all(b.timer is not None and not b.timer.done() for b in self._active.values())

    @property
    def pending_count(self) -> int:
        return len(self._active)

    def remaining(self, frame: BatchFrame) -> float | None:
        batch = self._active.get(self._member_index.get(self._identity(frame), ""))
        if batch is None or batch.sealed:
            return None
        return max(0.0, batch.deadline - self._clock())

    async def register(self, frame: BatchFrame, settings: BatchSettings) -> bool:
        if self._stopped or await self._duplicate(frame):
            return False
        batch = self._batches.get(frame.route)
        if batch is not None and frame.kind == FRAME_TEXT and any(
            m.frame.kind == FRAME_TEXT for m in batch.members.values()
        ):
            await self.flush_route(frame, "second_text")
            batch = None
        if batch is None:
            if len(self._active) >= self._capacity:
                raise RuntimeError("inbound batch capacity exceeded")
            batch = self._new_batch(settings)
        async with batch.lock:
            if batch.sealed:
                return await self.register(frame, settings)
            member = BatchMember(frame=frame)
            batch.members[frame.message_id] = member
            self._batches[frame.route] = batch
            self._active[batch.id] = batch
            self._member_index[self._identity(frame)] = batch.id
            overflow = self._media_count(batch) > batch.settings.max_attachments
            if overflow:
                member.failure = AttachmentFailure(key=f"{frame.message_id}#overflow",
                                                   code="ATTACHMENT_COUNT_EXCEEDED")
                member.status = "FAILED"
                self._seal(batch, "count_limit")
            self._arm(batch)
            await self._save(batch)
        if overflow:
            await batch.attempted.wait()
        return not overflow

    async def complete(self, event: InboundEvent) -> None:
        identity = (event.tenant_id, event.bot_id, event.external_message_id)
        batch = self._active.get(self._member_index.get(identity, ""))
        if batch is None:
            return
        async with batch.lock:
            if batch.sealed:
                return
            member = batch.members[event.external_message_id]
            member.event = event
            member.status = "FAILED" if event.attachment_failures else "READY"
            batch.last_ready_at = self._clock()
            batch.wake.set()
            await self._save(batch)

    async def fail(self, frame: BatchFrame, code: str) -> None:
        batch = self._active.get(self._member_index.get(self._identity(frame), ""))
        if batch is None:
            return
        async with batch.lock:
            if batch.sealed:
                return
            member = batch.members[frame.message_id]
            member.failure = AttachmentFailure(key=f"{frame.message_id}#0", code=code)
            member.status = "FAILED"
            batch.last_ready_at = self._clock()
            batch.wake.set()
            await self._save(batch)

    async def flush_route(self, frame: BatchFrame, reason: str) -> None:
        batch = self._batches.get(frame.route)
        if batch is not None:
            async with batch.lock:
                self._seal(batch, reason)
                batch.wake.set()
            await batch.attempted.wait()

    async def flush_all(self) -> None:
        self._stopped = True
        batches = list(self._active.values())
        for batch in batches:
            async with batch.lock:
                self._seal(batch, "shutdown")
                batch.wake.set()
        tasks = [b.timer for b in batches if b.timer is not None]
        if tasks:
            await asyncio.gather(*tasks)

    async def suspend_bot(self, tenant_id: str, bot_id: str) -> None:
        batches = [b for b in self._active.values()
                   if (b.primary().frame.tenant_id, b.primary().frame.bot_id) == (tenant_id, bot_id)]
        tasks = [b.timer for b in batches if b.timer is not None]
        for batch in batches:
            batch.suspended = True
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for batch in batches:
            self._forget(batch)

    async def recover(self, tenant_id: str, bot_id: str) -> None:
        for record in await self._journal.unfinished(tenant_id=tenant_id, bot_id=bot_id):
            if record.batch_id in self._active:
                continue
            try:
                batch = self._restore(record)
            except ValueError:
                record_batch_failure("incomplete_journal")
                continue
            if len(self._active) >= self._capacity:
                raise RuntimeError("batch recovery capacity exceeded")
            self._active[batch.id] = batch
            for member in batch.members.values():
                self._member_index[self._identity(member.frame)] = batch.id
            self._arm(batch)

    def _restore(self, record: BatchRecord) -> _Batch:
        if not record.member_states or record.settings is None or record.deadline_at is None:
            raise ValueError("journal lacks recoverable input")
        batch = self._new_batch(record.settings)
        batch.id = record.batch_id
        batch.members = {m.frame.message_id: m.model_copy(deep=True) for m in record.member_states}
        if record.state == BATCH_STATE_PUBLISHING and record.event is not None:
            batch.event, batch.sealed = record.event, True
            return batch
        batch.deadline_at = record.deadline_at
        batch.deadline = self._clock() + max(0.0, (record.deadline_at - datetime.now(UTC)).total_seconds())
        self._seal(batch, "recovery")
        return batch

    async def _duplicate(self, frame: BatchFrame) -> bool:
        if self._identity(frame) in self._member_index:
            return True
        batch_id = await self._journal.member_batch(
            tenant_id=frame.tenant_id, bot_id=frame.bot_id, message_id=frame.message_id)
        if batch_id is None:
            return False
        record = await self._journal.load(tenant_id=frame.tenant_id, bot_id=frame.bot_id, batch_id=batch_id)
        if record is None:
            raise ValueError("batch member binding has no journal")
        if record.state != BATCH_STATE_PUBLISHED:
            await self.recover(frame.tenant_id, frame.bot_id)
        return True

    def _new_batch(self, settings: BatchSettings) -> _Batch:
        return _Batch(id=uuid.uuid4().hex, settings=settings,
                      deadline=self._clock() + settings.max_wait_ms / 1000,
                      deadline_at=datetime.now(UTC) + timedelta(milliseconds=settings.max_wait_ms),
                      last_ready_at=self._clock())

    @staticmethod
    def _identity(frame: BatchFrame) -> tuple[str, str, str]:
        return frame.tenant_id, frame.bot_id, frame.message_id

    @staticmethod
    def _media_count(batch: _Batch) -> int:
        return sum(m.frame.media_count for m in batch.members.values() if m.frame.kind == FRAME_MEDIA)

    def _seal(self, batch: _Batch, reason: str) -> None:
        if batch.sealed:
            return
        for member in batch.members.values():
            if member.status == "PENDING":
                code = "ATTACHMENT_FETCH_TIMEOUT" if reason == "max_wait" else "ATTACHMENT_FETCH_FAILED"
                member.failure = AttachmentFailure(key=f"{member.frame.message_id}#0", code=code)
                member.status = "FAILED"
        batch.sealed, batch.reason = True, reason
        batch.event = self._merge(batch)
        if self._batches.get(batch.route) is batch:
            self._batches.pop(batch.route)

    @staticmethod
    def _merge(batch: _Batch) -> InboundEvent:
        primary = batch.primary()
        events = [m.event for m in batch.members.values() if m.event is not None]
        failures = [f for event in events for f in event.attachment_failures]
        failures.extend(m.failure for m in batch.members.values() if m.failure is not None)
        failures = list({f.key: f for f in failures}.values())
        return InboundEvent(
            event_id=uuid.uuid4(), channel=primary.frame.channel, tenant_id=primary.frame.tenant_id,
            bot_id=primary.frame.bot_id, external_message_id=primary.frame.message_id,
            external_conversation_id=primary.frame.external_conversation_id,
            external_user_id=primary.frame.external_user_id, text=primary.frame.text,
            attachments=tuple(ref for event in events for ref in event.attachments),
            attachment_keys=tuple(f.key for f in failures), attachment_failures=tuple(failures),
            reply_ref=primary.frame.reply_ref,
            received_at=primary.event.received_at if primary.event is not None else datetime.now(UTC),
            require_complete_attachments=len(batch.members) > 1,
        )

    async def _save(self, batch: _Batch, *, published: bool = False) -> None:
        frame = batch.primary().frame
        record = BatchRecord(
            batch_id=batch.id,
            state="PUBLISHED" if published else ("PUBLISHING" if batch.sealed else "PENDING"),
            primary_message_id=frame.message_id, members=tuple(batch.members),
            member_states=tuple(batch.members.values()), settings=batch.settings,
            deadline_at=batch.deadline_at, event=batch.event)
        await self._journal.save(tenant_id=frame.tenant_id, bot_id=frame.bot_id,
                                 batch_id=batch.id, payload=record)

    def _arm(self, batch: _Batch) -> None:
        if batch.timer is None:
            batch.timer = asyncio.create_task(self._run(batch), name=f"inbound.batch:{batch.id}")
            batch.timer.add_done_callback(lambda task: self._batch_done(batch, task))
        batch.wake.set()

    def _batch_done(self, batch: _Batch, task: asyncio.Task[None]) -> None:
        error = None if task.cancelled() else task.exception()
        if batch.id in self._active and not batch.suspended and not self._stopped:
            record_batch_failure(type(error).__name__ if error else "unexpected_batch_exit")
            logger.error("inbound_batch_task_stopped batch_id=%s", batch.id)

    async def _run(self, batch: _Batch) -> None:
        try:
            while batch.id in self._active:
                batch.wake.clear()
                delay = self._remaining(batch)
                if delay <= 0:
                    await self._attempt(batch)
                    delay = self._retry_sec if batch.sealed else self._remaining(batch)
                if batch.id in self._active:
                    try:
                        await asyncio.wait_for(batch.wake.wait(), timeout=delay)
                    except TimeoutError:
                        continue
        except asyncio.CancelledError:
            logger.warning("inbound_batch_suspended batch_id=%s", batch.id)
            raise

    def _remaining(self, batch: _Batch) -> float:
        if batch.sealed:
            return 0.0
        deadline = batch.deadline
        if not any(m.status == "PENDING" for m in batch.members.values()):
            deadline = min(deadline, batch.last_ready_at + batch.settings.quiet_ms / 1000)
        return max(0.0, deadline - self._clock())

    async def _attempt(self, batch: _Batch) -> None:
        try:
            async with batch.lock:
                if not batch.sealed:
                    if self._remaining(batch) > 0:
                        return
                    self._seal(batch, "max_wait" if self._clock() >= batch.deadline else "quiet")
                await self._save(batch)
            if batch.event is None:
                raise ValueError("sealed batch has no event")
            await self._publish(batch.event, tuple(batch.members))
            await self._save(batch, published=True)
        except Exception as exc:  # noqa: BLE001 - retry the same immutable event
            record_batch_failure(type(exc).__name__)
            logger.error("inbound_batch_publish_failed batch_id=%s reason=%s", batch.id, batch.reason)
            return
        finally:
            if batch.sealed:
                batch.attempted.set()
        self._forget(batch)
        logger.info("inbound_batch_flush batch_id=%s primary=%s members=%s reason=%s",
                    batch.id, batch.event.external_message_id, len(batch.members), batch.reason)

    def _forget(self, batch: _Batch) -> None:
        self._active.pop(batch.id, None)
        if self._batches.get(batch.route) is batch:
            self._batches.pop(batch.route)
        for member in batch.members.values():
            self._member_index.pop(self._identity(member.frame), None)


__all__ = ["BATCH_STATE_PENDING", "BATCH_STATE_PUBLISHED", "BATCH_STATE_PUBLISHING", "BatchFrame",
           "BatchJournal", "BatchSettings", "InboundBatchCoordinator", "RedisBatchJournal",
           "FRAME_MEDIA", "FRAME_TEXT"]
