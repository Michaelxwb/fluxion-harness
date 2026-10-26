"""入站 Stream 消费组（API-05 的 Gateway 侧；design §3.2 IM ASYNC 路径）。

Connector（以及 Generic Webhook 路由）把规范化事件 XADD 到 `im.inbound:{shard}`；Gateway 用
消费组 `im-ingress` 读取，按事件里的 `channel` 选适配器，交给统一入站管线建 Run。

诚实边界：

- **at-least-once**：处理成功才 XACK；处理异常保留 pending，由 `XAUTOCLAIM` 在空闲阈值后重试；
- 重试有上界：超过 `MAX_ATTEMPTS` 写 `im:inbound:dead`（原事件 + 错误）后 ACK，不无限重试；
- 未知通道**不静默丢**：同样进 dead stream，日志与指标可见；
- 读循环不阻塞在长流上：每条事件一个任务，用 `max_inflight` 封顶（与 WebChat backlog 同口径）。
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
from collections.abc import Mapping
from typing import Any, cast

import redis.asyncio
from muad_api import metrics
from muad_contracts import ChannelEnvelope, UnsupportedMedia
from muad_contracts.im import InboundEvent
from redis.asyncio import Redis

from ..channels.base import ChannelAdapter, ChannelRegistry, UnknownChannelAdapterError
from .inbound import InboundPipeline

logger = logging.getLogger(__name__)

INBOUND_GROUP = "im-ingress"
INBOUND_KEY_PREFIX = "im.inbound:"
INBOUND_DEAD_STREAM = "im:inbound:dead"
DEAD_STREAM_MAXLEN = 10_000
ENTRY_ATTEMPTS_PREFIX = "im:inbound:attempts:"
ENTRY_ATTEMPTS_TTL_SEC = 86_400
MAX_ATTEMPTS = 5
RECLAIM_IDLE_MS = 60_000
RECLAIM_INTERVAL_SEC = 30.0
STREAM_FAILURES_METRIC = "im_inbound_stream_failures_total"


class InboundStreamConsumer:
    def __init__(
        self,
        redis: Redis,
        *,
        registry: ChannelRegistry,
        pipeline: InboundPipeline,
        shards: int,
        max_inflight: int,
        consumer_name: str | None = None,
    ) -> None:
        if shards < 1 or max_inflight < 1:
            raise ValueError("shards and max_inflight must be positive")
        self._redis = redis
        self._registry = registry
        self._pipeline = pipeline
        self._shards = shards
        self._inflight = asyncio.Semaphore(max_inflight)
        self._consumer = consumer_name or f"gateway:{socket.gethostname()}:{os.getpid()}"
        self._tasks: set[asyncio.Task[None]] = set()
        self._stopped = asyncio.Event()
        self._last_reclaim = 0.0

    async def ensure_groups(self) -> None:
        """为每个 shard 建组（`0` = 连已存在的条目也消费；重启不会跳过崩溃前入队的事件）。"""
        for key in self._stream_keys():
            try:
                await self._redis.xgroup_create(key, INBOUND_GROUP, id="0", mkstream=True)
            except redis.RedisError as exc:
                if "BUSYGROUP" not in str(exc):
                    raise

    async def run_forever(self) -> None:
        while not self._stopped.is_set():
            try:
                await self.ensure_groups()
                break
            except redis.RedisError as exc:
                # Redis 尚未可用：入站事件仍在 Stream 里（生产者侧持久化），这里退避重试，
                # 不让生命周期任务直接死掉（死掉 = 之后再也没人消费）。
                logger.warning("inbound_stream_group_unavailable error=%s", type(exc).__name__)
                await self._sleep(1.0)
        while not self._stopped.is_set():
            await self._reclaim_due()
            try:
                response = await self._redis.xreadgroup(
                    INBOUND_GROUP,
                    self._consumer,
                    {key: ">" for key in self._stream_keys()},
                    count=32,
                    block=1000,
                )
            except asyncio.CancelledError:
                raise
            except redis.RedisError as exc:
                logger.warning("inbound_stream_read_failed error=%s", type(exc).__name__)
                await self._sleep(1.0)
                continue
            for stream, entries in response or ():
                for entry_id, fields in entries:
                    await self._spawn(_text(stream), _text(entry_id), _fields(fields))
            await self._drain_finished()

    async def stop(self) -> None:
        self._stopped.set()
        for task in tuple(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _spawn(self, stream: str, entry_id: str, fields: Mapping[str, str]) -> None:
        await self._inflight.acquire()
        task = asyncio.create_task(self._process(stream, entry_id, fields))
        self._tasks.add(task)

        def _done(finished: asyncio.Task[None]) -> None:
            self._tasks.discard(finished)
            self._inflight.release()

        task.add_done_callback(_done)

    async def _drain_finished(self) -> None:
        if self._tasks:
            await asyncio.sleep(0)

    async def _process(self, stream: str, entry_id: str, fields: Mapping[str, str]) -> None:
        try:
            event = InboundEvent.model_validate_json(fields.get("event", ""))
        except Exception as exc:  # noqa: BLE001 - 坏载荷必须进 dead letter
            await self._dead_letter(stream, entry_id, fields, f"BAD_PAYLOAD: {type(exc).__name__}")
            await self._ack(stream, entry_id)
            _count("bad_payload")
            return
        try:
            adapter = self._registry.get(event.channel)
        except UnknownChannelAdapterError:
            await self._dead_letter(stream, entry_id, fields, f"UNKNOWN_CHANNEL: {event.channel}")
            await self._ack(stream, entry_id)
            _count("unknown_channel")
            return
        try:
            await self._pipeline.handle_stream_event(adapter, _envelope(event, adapter))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 失败不 ACK，交给 reclaim 重试
            attempts = await self._bump_attempts(entry_id)
            logger.error(
                "inbound_stream_handle_failed channel=%s message_id=%s attempt=%s error=%s",
                event.channel,
                event.external_message_id,
                attempts,
                type(exc).__name__,
            )
            _count("handle_failed")
            if attempts >= MAX_ATTEMPTS:
                await self._dead_letter(stream, entry_id, fields, f"HANDLE_FAILED: {type(exc).__name__}")
                await self._ack(stream, entry_id)
            return
        await self._ack(stream, entry_id)

    async def _reclaim_due(self) -> None:
        now = asyncio.get_running_loop().time()
        if now - self._last_reclaim < RECLAIM_INTERVAL_SEC:
            return
        self._last_reclaim = now
        for key in self._stream_keys():
            try:
                claimed = await self._redis.xautoclaim(
                    key,
                    INBOUND_GROUP,
                    self._consumer,
                    min_idle_time=RECLAIM_IDLE_MS,
                    start_id="0-0",
                    count=8,
                )
            except redis.RedisError as exc:
                logger.warning("inbound_stream_reclaim_failed stream=%s error=%s", key, type(exc).__name__)
                continue
            entries = claimed[1] if isinstance(claimed, (list, tuple)) and len(claimed) > 1 else []
            for entry_id, fields in entries:
                await self._spawn(key, _text(entry_id), _fields(fields))

    async def _bump_attempts(self, entry_id: str) -> int:
        key = f"{ENTRY_ATTEMPTS_PREFIX}{entry_id}"
        try:
            attempts = int(await self._redis.incr(key))
            await self._redis.expire(key, ENTRY_ATTEMPTS_TTL_SEC)
            return attempts
        except redis.RedisError as exc:
            logger.warning("inbound_stream_attempts_failed error=%s", type(exc).__name__)
            return 0

    async def _ack(self, stream: str, entry_id: str) -> None:
        try:
            await self._redis.xack(stream, INBOUND_GROUP, entry_id)
            await self._redis.delete(f"{ENTRY_ATTEMPTS_PREFIX}{entry_id}")
        except redis.RedisError as exc:
            logger.warning("inbound_stream_ack_failed entry=%s error=%s", entry_id, type(exc).__name__)

    async def _dead_letter(
        self, stream: str, entry_id: str, fields: Mapping[str, str], error: str
    ) -> None:
        logger.error("inbound_stream_dead stream=%s entry=%s error=%s", stream, entry_id, error)
        try:
            await self._redis.xadd(
                INBOUND_DEAD_STREAM,
                cast(Any, {**fields, "error": error, "entry_id": entry_id}),
                maxlen=DEAD_STREAM_MAXLEN,
                approximate=True,
            )
        except redis.RedisError as exc:
            logger.error("inbound_dead_letter_failed entry=%s error=%s", entry_id, type(exc).__name__)

    def _stream_keys(self) -> tuple[str, ...]:
        return tuple(f"{INBOUND_KEY_PREFIX}{shard}" for shard in range(self._shards))

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stopped.wait(), timeout=seconds)
        except TimeoutError:
            return


def _envelope(event: InboundEvent, adapter: ChannelAdapter) -> ChannelEnvelope:
    """统一入站事实 → 管线认识的渠道信封；渠道身份由**适配器**给出（不猜）。"""
    return ChannelEnvelope(
        channel=adapter.name,
        bot_id=event.bot_id,
        external_user_id=event.external_user_id,
        external_conversation_id=event.external_conversation_id,
        message_id=event.external_message_id,
        text=event.text,
        attachments=list(event.attachments),
        attachment_failures=list(event.attachment_failures),
        unsupported_media=(
            cast(UnsupportedMedia, event.unsupported_media) if event.unsupported_media else None
        ),
    )


def _fields(raw: Any) -> dict[str, str]:
    if not isinstance(raw, Mapping):
        return {}
    return {_text(key): _text(value) for key, value in raw.items()}


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _count(reason: str) -> None:
    metrics.inc_counter(
        STREAM_FAILURES_METRIC, 1, {"reason": reason}, help="IM inbound stream failures"
    )


__all__ = ["INBOUND_GROUP", "InboundStreamConsumer"]
