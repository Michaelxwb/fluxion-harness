"""Typed batch snapshots and the existing Redis journal; no media credentials."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal, Protocol

from muad_api import inc_counter
from muad_contracts import AttachmentFailure, InboundEvent
from muad_contracts.im import inbound_batch_key, inbound_batch_member_key, route_key
from muad_logging import get_logger
from pydantic import BaseModel, ValidationError, model_validator
from redis.asyncio import Redis
from redis.asyncio.cluster import RedisCluster

logger = get_logger(__name__)
BATCH_JOURNAL_TTL_SEC = 86_400
BATCH_SCAN_LIMIT = 10_000
BATCH_STATE_PENDING = "PENDING"
BATCH_STATE_PUBLISHING = "PUBLISHING"
BATCH_STATE_PUBLISHED = "PUBLISHED"
FRAME_TEXT: Final = "text"
FRAME_MEDIA: Final = "media"


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
    kind: Literal["text", "media"]
    text: str = ""
    reply_ref: str | None = None
    media_count: int = 1

    @property
    def route(self) -> str:
        return route_key(channel=self.channel, tenant_id=self.tenant_id, bot_id=self.bot_id,
                         external_user_id=self.external_user_id,
                         external_conversation_id=self.external_conversation_id)


class BatchMember(BaseModel):
    frame: BatchFrame
    event: InboundEvent | None = None
    failure: AttachmentFailure | None = None
    status: Literal["PENDING", "READY", "FAILED"] = "PENDING"


class BatchRecord(BaseModel):
    batch_id: str
    state: Literal["PENDING", "PUBLISHING", "PUBLISHED"]
    primary_message_id: str
    members: tuple[str, ...]
    member_states: tuple[BatchMember, ...] = ()
    settings: BatchSettings | None = None
    deadline_at: datetime | None = None
    event: InboundEvent | None = None

    @model_validator(mode="after")
    def consistent_identity(self) -> BatchRecord:
        if not self.members or self.primary_message_id not in self.members:
            raise ValueError("batch primary must be a member")
        if len(set(self.members)) != len(self.members):
            raise ValueError("batch contains duplicate identities")
        if self.member_states and tuple(m.frame.message_id for m in self.member_states) != self.members:
            raise ValueError("batch member state order differs from identities")
        if self.state == "PUBLISHING" and (
            self.event is None or self.event.external_message_id != self.primary_message_id
        ):
            raise ValueError("publishing batch must preserve its primary event")
        return self


class BatchJournal(Protocol):
    async def load(self, *, tenant_id: str, bot_id: str, batch_id: str) -> BatchRecord | None: ...
    async def save(self, *, tenant_id: str, bot_id: str, batch_id: str, payload: BatchRecord) -> None: ...
    async def member_batch(self, *, tenant_id: str, bot_id: str, message_id: str) -> str | None: ...
    async def unfinished(self, *, tenant_id: str, bot_id: str) -> Sequence[BatchRecord]: ...


def record_batch_failure(reason: str) -> None:
    inc_counter("connector_inbound_total", 1, {"outcome": "failed"},
                help="Connector inbound publish outcomes")
    logger.warning("inbound_batch_failed reason=%s", reason)


class RedisBatchJournal:
    def __init__(self, redis: Redis | RedisCluster) -> None:
        self._redis = redis

    async def load(self, *, tenant_id: str, bot_id: str, batch_id: str) -> BatchRecord | None:
        raw = await self._redis.get(inbound_batch_key(tenant_id, bot_id, batch_id))
        return BatchRecord.model_validate_json(raw) if raw is not None else None

    async def save(self, *, tenant_id: str, bot_id: str, batch_id: str, payload: BatchRecord) -> None:
        # All keys share a bot hash tag, also in Redis Cluster.
        pipe = self._redis.pipeline(transaction=True)
        pipe.set(inbound_batch_key(tenant_id, bot_id, batch_id), payload.model_dump_json(),
                 ex=BATCH_JOURNAL_TTL_SEC)
        for message_id in payload.members:
            pipe.set(inbound_batch_member_key(tenant_id, bot_id, message_id), batch_id,
                     ex=BATCH_JOURNAL_TTL_SEC)
        await pipe.execute()

    async def member_batch(self, *, tenant_id: str, bot_id: str, message_id: str) -> str | None:
        raw = await self._redis.get(inbound_batch_member_key(tenant_id, bot_id, message_id))
        return raw.decode() if isinstance(raw, bytes) else raw

    async def unfinished(self, *, tenant_id: str, bot_id: str) -> Sequence[BatchRecord]:
        keys: list[str | bytes] = []
        async for key in self._redis.scan_iter(match=inbound_batch_key(tenant_id, bot_id, "*"), count=500):
            keys.append(key)
            if len(keys) > BATCH_SCAN_LIMIT:
                raise RuntimeError("batch journal scan capacity exceeded")
        if not keys:
            return ()
        records: list[BatchRecord] = []
        for raw in await self._redis.mget(keys):
            if raw is None:
                continue
            try:
                record = BatchRecord.model_validate_json(raw)
            except ValidationError:
                record_batch_failure("invalid_journal")
                continue
            if record.state != BATCH_STATE_PUBLISHED:
                records.append(record)
        return records
