from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from fluxion.registry import OutboxEventRecord, RegistryStore, RegistryStoreError
from fluxion.resources import ResourceKind
from fluxion.runtime.hot_reload import (
    ConfigChangeEvent,
    PolicyChangedEvent,
    ResourcePublishedEvent,
)

logger = logging.getLogger(__name__)


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
        self._task: asyncio.Task[None] | None = None

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

    def start(self, *, interval_seconds: float = 1.0) -> None:
        # A7：后台 drain 循环。serve lifespan 起始 start()、终止 stop()。每周期
        # run_once() claim 一批 → publish → mark PUBLISHED/retry。与 revision 轮询
        # 共存：push-invalidation（handle_config_changed）比 0.25s 轮询更快触达，
        # 两者按 revision 收敛不冲突。不进 initialize()——测试需观察 PENDING 行，
        # 后台 worker 会提前 drain 掉断言所依赖的 pending 状态。
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(interval_seconds))

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        self._task = None
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _run(self, interval_seconds: float) -> None:
        while True:
            try:
                await self.run_once()
            except RegistryStoreError:
                # claim 批次失败（DB 短暂不可用）→ 下周期重试，不终止长跑循环。
                logger.warning("outbox worker claim cycle failed; will retry", exc_info=True)
            except Exception:
                logger.exception("outbox worker dispatch cycle failed")
            await asyncio.sleep(interval_seconds)

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
    if record.aggregate_type == "binding":
        # A12 binding outbox 行：aggregate_type="binding"（非 ResourceKind），
        # kind/resource_id 取自 payload（commit_binding 写入 resource_type +
        # resource_id）。handle_config_changed 仅按 tenant_id+revision 做租户级
        # 失效，kind/resource_id 为元数据，但须是合法 ResourceKind 以构造事件。
        kind = ResourceKind(str(record.payload["resource_type"]))
        resource_id = str(record.payload["resource_id"])
    else:
        kind = ResourceKind(record.aggregate_type)
        resource_id = record.aggregate_id
    # TASK-014：按 kind 细化为具体 Domain Event（ResourcePublished / PolicyChanged）。
    cls = PolicyChangedEvent if kind is ResourceKind.POLICY else ResourcePublishedEvent
    return cls(
        tenant_id=record.tenant_id,
        kind=kind,
        resource_id=resource_id,
        version=record.version,
        revision=record.revision,
    )


def _now() -> datetime:
    return datetime.now(UTC)
