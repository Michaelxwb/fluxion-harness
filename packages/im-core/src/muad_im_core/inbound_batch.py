"""Connector 入站批次：文字 + 1..N 个媒体帧按 route 有界聚合。

热路径在内存；`BatchJournal` 只保存批次身份/成员/合并事件元数据，用于等待期去重和
XADD 后崩溃恢复。附件字节仍由 Connector 的取件链路落共享存储，不进 journal。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from muad_contracts import AttachmentFailure, InboundEvent
from muad_contracts.im import (
    inbound_batch_key,
    inbound_batch_member_key,
    route_key,
)
from redis.asyncio import Redis
from redis.asyncio.cluster import RedisCluster

logger = logging.getLogger(__name__)

BATCH_STATE_PENDING = "PENDING"
BATCH_STATE_PUBLISHING = "PUBLISHING"
BATCH_STATE_PUBLISHED = "PUBLISHED"
BATCH_JOURNAL_TTL_SEC = 86_400

FRAME_TEXT = "text"
FRAME_MEDIA = "media"


@dataclass(frozen=True, slots=True)
class BatchSettings:
    quiet_ms: int
    max_wait_ms: int
    max_attachments: int


@dataclass(frozen=True, slots=True)
class BatchFrame:
    channel: str
    tenant_id: str
    bot_id: str
    external_user_id: str
    external_conversation_id: str | None
    message_id: str
    kind: str
    text: str = ""
    reply_ref: str | None = None

    @property
    def route(self) -> str:
        return route_key(
            channel=self.channel,
            tenant_id=self.tenant_id,
            bot_id=self.bot_id,
            external_conversation_id=self.external_conversation_id,
            external_user_id=self.external_user_id,
        )


class BatchJournal(Protocol):
    async def load(self, *, tenant_id: str, bot_id: str, batch_id: str) -> dict[str, Any] | None: ...
    async def save(
        self, *, tenant_id: str, bot_id: str, batch_id: str, payload: Mapping[str, Any]
    ) -> None: ...
    async def bind_member(
        self, *, tenant_id: str, bot_id: str, message_id: str, batch_id: str
    ) -> None: ...
    async def member_batch(
        self, *, tenant_id: str, bot_id: str, message_id: str
    ) -> str | None: ...


class RedisBatchJournal:
    """最小 Redis journal：batch JSON + member→batch 映射。"""

    def __init__(self, redis: Redis | RedisCluster) -> None:
        self._redis = redis

    async def load(self, *, tenant_id: str, bot_id: str, batch_id: str) -> dict[str, Any] | None:
        raw = await self._redis.get(inbound_batch_key(tenant_id, bot_id, batch_id))
        if raw is None:
            return None
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    async def save(
        self, *, tenant_id: str, bot_id: str, batch_id: str, payload: Mapping[str, Any]
    ) -> None:
        await self._redis.set(
            inbound_batch_key(tenant_id, bot_id, batch_id),
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            ex=BATCH_JOURNAL_TTL_SEC,
        )

    async def bind_member(
        self, *, tenant_id: str, bot_id: str, message_id: str, batch_id: str
    ) -> None:
        await self._redis.set(
            inbound_batch_member_key(tenant_id, bot_id, message_id),
            batch_id,
            ex=BATCH_JOURNAL_TTL_SEC,
        )

    async def member_batch(
        self, *, tenant_id: str, bot_id: str, message_id: str
    ) -> str | None:
        raw = await self._redis.get(inbound_batch_member_key(tenant_id, bot_id, message_id))
        return None if raw is None else str(raw)


@dataclass(slots=True)
class _Member:
    frame: BatchFrame
    event: InboundEvent | None = None
    failure: AttachmentFailure | None = None
    status: str = "PENDING"


@dataclass(slots=True)
class _Batch:
    id: str
    route: str
    settings: BatchSettings
    created_at: float
    members: dict[str, _Member] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    last_ready_at: float | None = None
    timer: asyncio.Task[None] | None = None

    def primary(self) -> _Member | None:
        for message_id in self.order:
            member = self.members[message_id]
            if member.frame.kind == FRAME_TEXT and member.event is not None:
                return member
        for message_id in self.order:
            member = self.members[message_id]
            if member.event is not None:
                return member
        return self.members[self.order[0]] if self.order else None

    def has_pending(self) -> bool:
        return any(member.status == "PENDING" for member in self.members.values())


class InboundBatchCoordinator:
    """按 route 聚合；`publish` 由 Connector 提供，journal 负责跨重投去重。"""

    def __init__(
        self,
        *,
        journal: BatchJournal,
        publish: Callable[[InboundEvent, Sequence[str]], Awaitable[None]],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._journal = journal
        self._publish = publish
        self._clock = clock
        self._batches: dict[str, _Batch] = {}
        self._member_index: dict[tuple[str, str, str], str] = {}
        self._stopped = False

    async def register(self, frame: BatchFrame, settings: BatchSettings) -> bool:
        """登记一个原始帧；返回 False 表示已发布/重复，调用方应丢弃。"""
        tenant_id, bot_id, message_id = frame.tenant_id, frame.bot_id, frame.message_id
        batch_id = self._member_index.get((tenant_id, bot_id, message_id))
        if batch_id is not None:
            batch = self._batches.get(batch_id)
            if batch is not None:
                return False
            if await self._is_published(frame, batch_id):
                return False
        else:
            existing = await self._journal.member_batch(
                tenant_id=tenant_id, bot_id=bot_id, message_id=message_id
            )
            if existing and await self._is_published(frame, existing):
                return False

        batch = self._batches.get(frame.route)
        if batch is not None:
            if message_id in batch.members:
                return False
            if frame.kind == FRAME_TEXT and any(
                member.frame.kind == FRAME_TEXT for member in batch.members.values()
            ):
                await self._flush(batch, "second_text")
                batch = None
            elif frame.kind == FRAME_MEDIA and self._media_count(batch) >= batch.settings.max_attachments:
                batch.members[message_id] = _Member(
                    frame=frame,
                    failure=AttachmentFailure(key=f"{message_id}#overflow", code="ATTACHMENT_COUNT_EXCEEDED"),
                    status="FAILED",
                )
                batch.order.append(message_id)
                self._member_index[(tenant_id, bot_id, message_id)] = batch.id
                await self._journal.bind_member(
                    tenant_id=tenant_id, bot_id=bot_id, message_id=message_id, batch_id=batch.id
                )
                await self._flush(batch, "count_limit")
                return True
        if batch is None:
            batch = _Batch(
                id=uuid.uuid4().hex,
                route=frame.route,
                settings=settings,
                created_at=self._clock(),
            )
            self._batches[frame.route] = batch
        batch.members[message_id] = _Member(frame=frame)
        batch.order.append(message_id)
        self._member_index[(tenant_id, bot_id, message_id)] = batch.id
        await self._journal.bind_member(
            tenant_id=tenant_id, bot_id=bot_id, message_id=message_id, batch_id=batch.id
        )
        await self._save(batch)
        self._arm(batch)
        return True

    async def complete(self, event: InboundEvent) -> InboundEvent | None:
        """媒体/文字物化完成；返回需要立即发布的合并事件（批次满/失败时）。"""
        member = self._find(event.channel, event.bot_id, event.external_message_id)
        if member is None:
            return None
        batch = self._batch_of(member.frame)
        if batch is None:
            return None
        member.event = event
        member.status = "READY"
        batch.last_ready_at = self._clock()
        await self._save(batch)
        self._arm(batch)
        return None

    async def fail(self, frame: BatchFrame, code: str) -> None:
        member = self._find(frame.channel, frame.bot_id, frame.message_id)
        if member is None:
            return
        batch = self._batch_of(frame)
        if batch is None:
            return
        member.failure = AttachmentFailure(key=f"{frame.message_id}#0", code=code)
        member.status = "FAILED"
        batch.last_ready_at = self._clock()
        await self._save(batch)
        self._arm(batch)

    async def flush_route(self, frame: BatchFrame, reason: str) -> None:
        batch = self._batches.get(frame.route)
        if batch is not None:
            await self._flush(batch, reason)

    async def flush_all(self) -> None:
        self._stopped = True
        for batch in list(self._batches.values()):
            await self._flush(batch, "shutdown")

    def _find(self, channel: str, bot_id: str, message_id: str) -> _Member | None:
        for batch in self._batches.values():
            member = batch.members.get(message_id)
            if member is not None and member.frame.channel == channel and member.frame.bot_id == bot_id:
                return member
        return None

    def _batch_of(self, frame: BatchFrame) -> _Batch | None:
        return self._batches.get(frame.route)

    @staticmethod
    def _media_count(batch: _Batch) -> int:
        return sum(1 for member in batch.members.values() if member.frame.kind == FRAME_MEDIA)

    async def _is_published(self, frame: BatchFrame, batch_id: str) -> bool:
        state = await self._journal.load(tenant_id=frame.tenant_id, bot_id=frame.bot_id, batch_id=batch_id)
        return state is not None and state.get("state") == BATCH_STATE_PUBLISHED

    async def _save(self, batch: _Batch) -> None:
        primary = batch.primary()
        payload = {
            "batch_id": batch.id,
            "state": BATCH_STATE_PENDING,
            "primary_message_id": primary.frame.message_id if primary is not None else "",
            "members": [member.frame.message_id for member in batch.members.values()],
        }
        await self._journal.save(
            tenant_id=primary.frame.tenant_id if primary is not None else "",
            bot_id=primary.frame.bot_id if primary is not None else "",
            batch_id=batch.id,
            payload=payload,
        )

    def _arm(self, batch: _Batch) -> None:
        if self._stopped:
            return
        if batch.timer is not None:
            batch.timer.cancel()
        batch.timer = asyncio.create_task(self._timers(batch))

    async def _timers(self, batch: _Batch) -> None:
        try:
            while True:
                if self._batches.get(batch.route) is not batch:
                    return
                now = self._clock()
                hard = batch.created_at + batch.settings.max_wait_ms / 1000
                if batch.has_pending():
                    deadline = hard
                else:
                    quiet = (batch.last_ready_at or now) + batch.settings.quiet_ms / 1000
                    deadline = min(hard, quiet)
                delay = max(0.0, deadline - now)
                await asyncio.sleep(delay)
                if self._batches.get(batch.route) is not batch:
                    return
                if self._clock() + 0.000_001 >= deadline:
                    timed_out = batch.has_pending()
                    if timed_out:
                        for member in batch.members.values():
                            if member.status == "PENDING":
                                member.failure = AttachmentFailure(
                                    key=f"{member.frame.message_id}#0",
                                    code="ATTACHMENT_FETCH_TIMEOUT",
                                )
                                member.status = "FAILED"
                    await self._flush(batch, "max_wait" if timed_out else "quiet")
                    return
        except asyncio.CancelledError:
            raise

    async def _flush(self, batch: _Batch, reason: str) -> None:
        if self._batches.pop(batch.route, None) is not batch:
            return
        if batch.timer is not None:
            batch.timer.cancel()
            batch.timer = None
        primary = batch.primary()
        if primary is None:
            return
        events = [member.event for member in batch.members.values() if member.event is not None]
        failures = [member.failure for member in batch.members.values() if member.failure is not None]
        attachments = [attachment for event in events for attachment in event.attachments]
        primary_event = next(
            (event for event in events if event.external_message_id == primary.frame.message_id),
            events[0] if events else None,
        )
        merged = InboundEvent(
            event_id=uuid.uuid4(),
            channel=primary.frame.channel,
            tenant_id=primary.frame.tenant_id,
            bot_id=primary.frame.bot_id,
            external_message_id=primary.frame.message_id,
            external_conversation_id=primary.frame.external_conversation_id,
            external_user_id=primary.frame.external_user_id,
            text=primary.frame.text if primary.frame.kind == FRAME_TEXT else (primary_event.text if primary_event else ""),
            attachments=tuple(attachments),
            attachment_keys=tuple(failure.key for failure in failures),
            attachment_failures=tuple(failures),
            reply_ref=primary.frame.reply_ref,
            received_at=primary_event.received_at if primary_event else datetime.now(UTC),
            require_complete_attachments=len(batch.members) > 1,
        )
        await self._journal.save(
            tenant_id=merged.tenant_id,
            bot_id=merged.bot_id,
            batch_id=batch.id,
            payload={
                "batch_id": batch.id,
                "state": BATCH_STATE_PUBLISHING,
                "primary_message_id": merged.external_message_id,
                "members": [member.frame.message_id for member in batch.members.values()],
                "event": merged.model_dump(mode="json"),
            },
        )
        try:
            await self._publish(merged, [member.frame.message_id for member in batch.members.values()])
        except Exception as exc:  # noqa: BLE001 - 发布失败必须保留批次
            logger.error("inbound_batch_publish_failed batch_id=%s reason=%s error=%s", batch.id, reason, type(exc).__name__)
            self._batches[batch.route] = batch
            self._arm(batch)
            return
        await self._journal.save(
            tenant_id=merged.tenant_id,
            bot_id=merged.bot_id,
            batch_id=batch.id,
            payload={
                "batch_id": batch.id,
                "state": BATCH_STATE_PUBLISHED,
                "primary_message_id": merged.external_message_id,
                "members": [member.frame.message_id for member in batch.members.values()],
            },
        )
        logger.info(
            "inbound_batch_flush batch_id=%s primary=%s members=%s reason=%s",
            batch.id,
            merged.external_message_id,
            len(batch.members),
            reason,
        )


__all__ = [
    "BATCH_STATE_PENDING",
    "BATCH_STATE_PUBLISHED",
    "BATCH_STATE_PUBLISHING",
    "BatchFrame",
    "BatchJournal",
    "BatchSettings",
    "InboundBatchCoordinator",
    "RedisBatchJournal",
    "FRAME_MEDIA",
    "FRAME_TEXT",
]
