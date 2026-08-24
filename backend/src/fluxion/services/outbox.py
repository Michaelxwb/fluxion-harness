from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from fluxion.registry import OutboxEventRecord, RegistryStore
from fluxion.resources import ResourceKind
from fluxion.runtime.hot_reload import ConfigChangeEvent


class ConfigEventPublisher(Protocol):
    async def publish(self, event: ConfigChangeEvent) -> None: ...


class RedisStreamClient(Protocol):
    async def xadd(
        self,
        stream: str,
        fields: Mapping[str, str],
        *,
        id: str,
    ) -> object: ...

    async def xrange(
        self,
        stream: str,
        *,
        min: str,
        max: str,
        count: int,
    ) -> Sequence[object]: ...


@dataclass(frozen=True, slots=True)
class OutboxDispatchResult:
    claimed: int
    published: int
    retried: int
    failed: int


class InProcessConfigEventPublisher:
    def __init__(self, handler: Callable[[ConfigChangeEvent], None]) -> None:
        self._handler = handler

    async def publish(self, event: ConfigChangeEvent) -> None:
        self._handler(event)


class RedisStreamsConfigEventPublisher:
    """Redis Streams publisher with deterministic stream IDs for retry idempotency."""

    def __init__(
        self,
        client: RedisStreamClient,
        *,
        stream_prefix: str = "fluxion:config-changed",
        timeout_seconds: float = 2.0,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._client = client
        self._stream_prefix = stream_prefix
        self._timeout_seconds = timeout_seconds

    async def publish(self, event: ConfigChangeEvent) -> None:
        stream = f"{self._stream_prefix}:{event.tenant_id}"
        stream_id = f"{event.revision}-0"
        fields = {
            "event_type": "config.changed",
            "tenant_id": event.tenant_id,
            "kind": event.kind.value,
            "resource_id": event.resource_id,
            "version": event.version,
            "revision": str(event.revision),
            "payload": json.dumps(event.to_payload(), sort_keys=True),
        }
        try:
            async with asyncio.timeout(self._timeout_seconds):
                await self._client.xadd(stream, fields, id=stream_id)
        except Exception:
            if await self._already_published(stream, stream_id):
                return
            raise

    async def _already_published(self, stream: str, stream_id: str) -> bool:
        async with asyncio.timeout(self._timeout_seconds):
            rows = await self._client.xrange(
                stream,
                min=stream_id,
                max=stream_id,
                count=1,
            )
        return bool(rows)


class OutboxWorker:
    def __init__(
        self,
        store: RegistryStore,
        publisher: ConfigEventPublisher,
        *,
        worker_id: str,
        batch_size: int = 100,
        lease_seconds: float = 30.0,
        publish_timeout_seconds: float = 5.0,
        base_backoff_seconds: float = 1.0,
        max_backoff_seconds: float = 60.0,
        max_attempts: int = 8,
    ) -> None:
        if batch_size <= 0 or lease_seconds <= 0 or publish_timeout_seconds <= 0:
            raise ValueError("batch_size, lease_seconds and publish timeout must be positive")
        if base_backoff_seconds < 0 or max_backoff_seconds < base_backoff_seconds:
            raise ValueError("invalid outbox backoff")
        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        self._store = store
        self._publisher = publisher
        self._worker_id = worker_id
        self._batch_size = batch_size
        self._lease_seconds = lease_seconds
        self._publish_timeout_seconds = publish_timeout_seconds
        self._base_backoff_seconds = base_backoff_seconds
        self._max_backoff_seconds = max_backoff_seconds
        self._max_attempts = max_attempts

    async def run_once(self) -> OutboxDispatchResult:
        events = await self._store.claim_outbox(
            worker_id=self._worker_id,
            limit=self._batch_size,
            lease_seconds=self._lease_seconds,
        )
        published = retried = failed = 0
        for record in events:
            outcome = await self._dispatch(record)
            published += outcome == "published"
            retried += outcome == "retried"
            failed += outcome == "failed"
        return OutboxDispatchResult(len(events), published, retried, failed)

    async def _dispatch(self, record: OutboxEventRecord) -> str:
        try:
            async with asyncio.timeout(self._publish_timeout_seconds):
                await self._publisher.publish(_config_event(record))
        except Exception as exc:  # noqa: BLE001 - 外部发布失败必须进入有界重试
            terminal = record.attempt_count + 1 >= self._max_attempts
            await self._store.mark_outbox_retry(
                record.event_id,
                worker_id=self._worker_id,
                error=type(exc).__name__,
                retry_at=_now() + timedelta(seconds=self._backoff(record.attempt_count)),
                terminal=terminal,
            )
            return "failed" if terminal else "retried"
        await self._store.mark_outbox_published(record.event_id, worker_id=self._worker_id)
        return "published"

    def _backoff(self, attempt_count: int) -> float:
        delay = self._base_backoff_seconds * pow(2.0, attempt_count)
        return min(delay, self._max_backoff_seconds)


def _config_event(record: OutboxEventRecord) -> ConfigChangeEvent:
    return ConfigChangeEvent(
        tenant_id=record.tenant_id,
        kind=ResourceKind(record.aggregate_type),
        resource_id=record.aggregate_id,
        version=record.version,
        revision=record.revision,
    )


def _now() -> datetime:
    return datetime.now(UTC)
