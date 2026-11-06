"""入站 Stream 消费组（API-05 的 Gateway 侧；design §3.2 IM ASYNC 路径）。

Connector（以及 Generic Webhook 路由）把规范化事件 XADD 到 `muad:im.inbound:{shard}`；Gateway 用
消费组 `im-ingress` 读取，按事件里的 `channel` 选适配器，交给统一入站管线建 Run。

诚实边界：

- **at-least-once**：处理成功才 XACK；处理异常保留 pending，由 `XAUTOCLAIM` 在空闲阈值后重试；
- 重试有上界：超过 `MAX_ATTEMPTS` 写 `muad:im:inbound:dead`（原事件 + 错误）后 ACK，不无限重试；
- 未知通道**不静默丢**：同样进 dead stream，日志与指标可见；
- 读循环不阻塞在长流上：每条事件一个任务，用 `max_inflight` 封顶（与 WebChat backlog 同口径）。
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import time
from collections.abc import Mapping
from typing import Any, cast

import redis.asyncio
from muad_api import metrics
from muad_contracts import ChannelEnvelope, UnsupportedMedia
from muad_contracts.im import InboundEvent, inbound_stream_key_for_shard
from redis.asyncio import Redis
from redis.asyncio.cluster import RedisCluster
from redis.exceptions import RedisClusterException

from ..channels.base import ChannelAdapter, ChannelRegistry, UnknownChannelAdapterError
from .inbound import InboundPipeline

logger = logging.getLogger(__name__)

INBOUND_GROUP = "im-ingress"
INBOUND_DEAD_STREAM = "muad:im:inbound:dead"
DEAD_STREAM_MAXLEN = 10_000
ENTRY_ATTEMPTS_PREFIX = "muad:im:inbound:attempts:"
ENTRY_ATTEMPTS_TTL_SEC = 86_400
MAX_ATTEMPTS = 5
RECLAIM_IDLE_MS = 60_000
RECLAIM_INTERVAL_SEC = 30.0
STREAM_FAILURES_METRIC = "im_inbound_stream_failures_total"


class InboundStreamConsumer:
    def __init__(
        self,
        redis: Redis | RedisCluster,
        *,
        registry: ChannelRegistry,
        pipeline: InboundPipeline,
        shards: int,
        max_inflight: int,
        retention_sec: int = 600,
        stream_maxlen: int = 5_000,
        trim_interval_sec: float = 30.0,
        consumer_name: str | None = None,
    ) -> None:
        if shards < 1 or max_inflight < 1:
            raise ValueError("shards and max_inflight must be positive")
        if retention_sec < 1 or stream_maxlen < 1 or trim_interval_sec < 0:
            raise ValueError("retention window, maxlen and trim interval must be positive")
        self._redis = redis
        self._registry = registry
        self._pipeline = pipeline
        self._shards = shards
        self._inflight = asyncio.Semaphore(max_inflight)
        self._consumer = consumer_name or f"gateway:{socket.gethostname()}:{os.getpid()}"
        self._tasks: set[asyncio.Task[None]] = set()
        self._stopped = asyncio.Event()
        self._last_reclaim = 0.0
        self._retention_ms = retention_sec * 1000
        self._stream_maxlen = stream_maxlen
        self._trim_interval_sec = trim_interval_sec
        self._last_trim = 0.0

    async def ensure_groups(self) -> None:
        """为每个 shard 建组（`0` = 连已存在的条目也消费；重启不会跳过崩溃前入队的事件）。"""
        for key in self._stream_keys():
            try:
                await self._redis.xgroup_create(key, INBOUND_GROUP, id="0", mkstream=True)
            except (redis.RedisError, RedisClusterException) as exc:
                if "BUSYGROUP" not in str(exc):
                    raise

    async def run_forever(self) -> None:
        degraded = False
        while not self._stopped.is_set():
            try:
                await self.ensure_groups()
                break
            except (redis.RedisError, RedisClusterException) as exc:
                degraded = True
                # Redis 尚未可用：入站事件仍在 Stream 里（生产者侧持久化），这里退避重试，
                # 不让生命周期任务直接死掉（死掉 = 之后再也没人消费）。
                logger.warning("inbound_stream_group_unavailable error=%s", type(exc).__name__)
                await self._sleep(1.0)
        if degraded:
            logger.warning("inbound_stream_group_recovered")
        while not self._stopped.is_set():
            await self._reclaim_due()
            await self._trim_due()
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
            except (redis.RedisError, RedisClusterException) as exc:
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
            accepted = await self._pipeline.handle_stream_event(
                adapter, _envelope(event, adapter)
            )
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
        if not accepted:
            # 依赖暂不可用/入口明确拒绝：handle 已释放去重占位并给用户反馈；不能 ACK，
            # 让 XAUTOCLAIM 在空闲窗口后重试（否则消息永久丢失，E-19 重启窗口实测）。
            attempts = await self._bump_attempts(entry_id)
            logger.warning(
                "inbound_stream_not_accepted channel=%s message_id=%s attempt=%s",
                event.channel,
                event.external_message_id,
                attempts,
            )
            _count("not_accepted")
            if attempts >= MAX_ATTEMPTS:
                await self._dead_letter(stream, entry_id, fields, "NOT_ACCEPTED")
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
            except (redis.RedisError, RedisClusterException) as exc:
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
        except (redis.RedisError, RedisClusterException) as exc:
            logger.warning("inbound_stream_attempts_failed error=%s", type(exc).__name__)
            return 0

    async def _ack(self, stream: str, entry_id: str) -> None:
        try:
            await self._redis.xack(stream, INBOUND_GROUP, entry_id)
            await self._redis.delete(f"{ENTRY_ATTEMPTS_PREFIX}{entry_id}")
        except (redis.RedisError, RedisClusterException) as exc:
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
        except (redis.RedisError, RedisClusterException) as exc:
            logger.error("inbound_dead_letter_failed entry=%s error=%s", entry_id, type(exc).__name__)

    async def _trim_due(self) -> None:
        """按时间窗口裁剪已投递历史；不越过 last-delivered-id 与最早 pending。"""
        now = asyncio.get_running_loop().time()
        if now - self._last_trim < self._trim_interval_sec:
            return
        self._last_trim = now
        for key in self._stream_keys():
            try:
                await self._trim_stream(key)
            except (redis.RedisError, RedisClusterException, OSError) as exc:
                logger.warning(
                    "inbound_stream_trim_failed stream=%s error=%s",
                    key,
                    type(exc).__name__,
                )

    async def _trim_stream(self, key: str) -> None:
        groups = await self._redis.xinfo_groups(key)
        last_delivered = "0-0"
        for group in groups or ():
            if _text(group.get("name")) == INBOUND_GROUP:
                last_delivered = _text(group.get("last-delivered-id")) or "0-0"
                break
        else:
            return
        if _stream_id_le(last_delivered, "0-0"):
            return
        pending = await self._redis.xpending(key, INBOUND_GROUP)
        pending_min = ""
        if isinstance(pending, Mapping) and pending.get("min"):
            pending_min = _text(pending.get("min"))
        # 上限：有 pending 时停在 pending 之前；否则可以裁到 last-delivered 的后继。
        limit = pending_min or _stream_id_next(last_delivered)
        time_cutoff = f"{int(time.time() * 1000) - self._retention_ms}-0"
        cutoff = time_cutoff if _stream_id_less(time_cutoff, limit) else limit
        if _stream_id_le(cutoff, "0-0"):
            return
        await self._redis.xtrim(key, minid=cutoff, approximate=False)
        await self._check_overflow(key, last_delivered)

    async def _check_overflow(self, key: str, last_delivered: str) -> None:
        """MAXLEN 在生产者侧裁剪；若最老条目已越过 last-delivered，说明丢了未投递事件。"""
        try:
            length = await self._redis.xlen(key)
            if length < self._stream_maxlen:
                return
            first = await self._redis.xrange(key, "-", "+", count=1)
        except (redis.RedisError, RedisClusterException, OSError) as exc:
            logger.warning(
                "inbound_stream_overflow_check_failed stream=%s error=%s",
                key,
                type(exc).__name__,
            )
            return
        if first and _stream_id_less(last_delivered, _text(first[0][0])):
            metrics.inc_counter(
                "inbound_stream_backlog_overflow_total",
                1,
                {},
                help="undeclared unread inbound entries trimmed by MAXLEN",
            )
            logger.error(
                "inbound_stream_backlog_overflow stream=%s first_entry=%s last_delivered=%s",
                key,
                _text(first[0][0]),
                last_delivered,
            )

    def _stream_keys(self) -> tuple[str, ...]:
        return tuple(
            inbound_stream_key_for_shard(shard) for shard in range(self._shards)
        )

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


def _parse_stream_id(value: str) -> tuple[int, int] | None:
    head, _, tail = (value or "").partition("-")
    if not head.isdigit() or not tail.isdigit():
        return None
    return int(head), int(tail)


def _stream_id_less(left: str, right: str) -> bool:
    left_id, right_id = _parse_stream_id(left), _parse_stream_id(right)
    return left_id is not None and right_id is not None and left_id < right_id


def _stream_id_le(left: str, right: str) -> bool:
    left_id, right_id = _parse_stream_id(left), _parse_stream_id(right)
    return left_id is not None and right_id is not None and left_id <= right_id


def _stream_id_next(value: str) -> str:
    parsed = _parse_stream_id(value)
    if parsed is None:
        return value
    return f"{parsed[0]}-{parsed[1] + 1}"


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
