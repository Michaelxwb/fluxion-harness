"""Connector 入站持久化：先写去重/崩溃标记，再 XADD `im.inbound`。

诚实边界（E-03/E-07）：

- Stream 是 **at-least-once**；消费端必须按 `external_message_id` 幂等。
- 收到消息后、XADD 前崩溃：标记停在 `RECEIVED`。平台**重投**时允许再次发布（最终一个 Run）；
  平台**不重投**时，启动扫描把过期标记置 `LOST` 并记 ERROR + 指标——不假装成功，也不补造 Run。
- `PUBLISHED` 的标记才表示"已经进过 Stream"；重复回调直接跳过。

本次实现刻意不用 Redis Stream consumer group 做入站去重：去重必须在 **XADD 之前**发生，
否则重复回调已经变成两条 Stream 记录，消费端只能靠 Runtime 幂等兜底；入口标记把窗口前移。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import StrEnum

from muad_contracts.im import InboundEvent, inbound_dedupe_key, inbound_stream_key
from redis.asyncio import Redis

logger = logging.getLogger(__name__)

STATE_RECEIVED = "RECEIVED"
STATE_PUBLISHED = "PUBLISHED"
STATE_LOST = "LOST"
DEDUPE_TTL_SEC = 86_400
SWEEP_SCAN_LIMIT = 10_000


class PublishOutcome(StrEnum):
    PUBLISHED = "PUBLISHED"
    DUPLICATE = "DUPLICATE"


@dataclass(frozen=True, slots=True)
class SweepResult:
    scanned: int
    lost: int


class InboundPublisher:
    def __init__(
        self,
        redis: Redis,
        *,
        shards: int,
        stream_maxlen: int,
        lost_after_sec: int,
        pause_before_xadd_sec: float = 0.0,
    ) -> None:
        if shards < 1:
            raise ValueError("shards must be positive")
        if stream_maxlen < 1:
            raise ValueError("stream_maxlen must be positive")
        if lost_after_sec < 1:
            raise ValueError("lost_after_sec must be positive")
        self._redis = redis
        self._shards = shards
        self._maxlen = stream_maxlen
        self._lost_after_sec = lost_after_sec
        self._pause = pause_before_xadd_sec

    async def publish(self, event: InboundEvent) -> PublishOutcome:
        key = inbound_dedupe_key(event.tenant_id, event.bot_id, event.external_message_id)
        state = await self._begin(key)
        if state == PublishOutcome.DUPLICATE:
            return state
        if self._pause > 0:
            # 确定性故障注入：测试在标记写入后、XADD 前 kill 进程（生产默认 0）。
            await asyncio.sleep(self._pause)
        stream = inbound_stream_key(event, shards=self._shards)
        entry_id = await self._redis.xadd(
            stream,
            {
                "event_id": str(event.event_id),
                "channel": event.channel,
                "tenant_id": event.tenant_id,
                "bot_id": event.bot_id,
                "external_message_id": event.external_message_id,
                "event": event.model_dump_json(),
            },
            maxlen=self._maxlen,
            approximate=True,
        )
        await self._redis.set(
            key,
            f"{STATE_PUBLISHED}:{_text(entry_id)}",
            ex=DEDUPE_TTL_SEC,
        )
        return PublishOutcome.PUBLISHED

    async def _begin(self, key: str) -> PublishOutcome:
        """写/读入口标记；返回是否继续发布。

        - 已有 `PUBLISHED` ⇒ 重复回调，跳过；
        - 已有 `RECEIVED`/`LOST` ⇒ 上次发布未完成/已判丢失，本次重投继续发布（at-least-once）；
        - 没有标记 ⇒ `SET NX` 抢占；抢占失败重新读一次，按上面规则裁决。
        """
        now = time.time()
        if await self._redis.set(
            key, f"{STATE_RECEIVED}:{now}", nx=True, ex=DEDUPE_TTL_SEC
        ):
            return PublishOutcome.PUBLISHED
        current = _text(await self._redis.get(key))
        if current.startswith(f"{STATE_PUBLISHED}:"):
            return PublishOutcome.DUPLICATE
        if current.startswith(STATE_RECEIVED) or current.startswith(STATE_LOST):
            return PublishOutcome.PUBLISHED
        # 值不可识别（损坏/被外部改写）：不静默覆盖，交给调用方显式失败。
        raise ValueError(f"inbound dedupe state is corrupt: {key}")

    async def sweep_lost(self) -> SweepResult:
        """启动扫描：把超时仍停在 `RECEIVED` 的入站标记显式判 LOST。"""
        cutoff = time.time() - self._lost_after_sec
        scanned = 0
        lost = 0
        async for raw_key in self._redis.scan_iter(
            match="connector:inbound:*", count=500
        ):
            scanned += 1
            if scanned > SWEEP_SCAN_LIMIT:
                logger.warning("connector_inbound_sweep_truncated scanned=%s", scanned)
                break
            key = _text(raw_key)
            value = _text(await self._redis.get(key))
            if not value.startswith(f"{STATE_RECEIVED}:"):
                continue
            try:
                received_at = float(value.split(":", 1)[1])
            except (IndexError, ValueError):
                continue
            if received_at > cutoff:
                continue
            ttl = await self._redis.ttl(key)
            await self._redis.set(
                key, f"{STATE_LOST}:{time.time()}", keepttl=ttl > 0
            )
            lost += 1
            logger.error(
                "connector_inbound_lost key=%s received_at=%.0f threshold_sec=%s",
                key,
                received_at,
                self._lost_after_sec,
            )
        return SweepResult(scanned=scanned, lost=lost)


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)
