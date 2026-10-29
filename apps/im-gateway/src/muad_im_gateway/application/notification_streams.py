"""每用户通知 SSE 的进程级 hub：一个 gateway Pod 一条 Redis pubsub，按 tenant/user 路由。

通知事实仍在 Runtime 的 PG；Redis 只承载**提交后的轻量信号**（tenant/user/notification_id/seq）。
信号丢失不影响正确性：客户端重连时按持久 seq 回填。队列满表示慢订阅，流会显式结束让客户端
带 `Last-Event-ID` 重连，绝不静默丢一条后继续假装在线。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Final

import redis.asyncio
from muad_common.notification_keys import notification_watermark_key
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

NOTIFICATION_CHANNEL: Final = "webchat.notifications"
#: 单一订阅者缓冲：正常通知量远小于此；到界即结束流，由持久游标恢复。
DEFAULT_QUEUE_SIZE: Final = 256


@dataclass(frozen=True, slots=True)
class NotificationSignal:
    tenant_id: str
    user_id: uuid.UUID
    notification_id: uuid.UUID
    seq: int
    notification: dict[str, object] | None = None


class NotificationStreamHub:
    """订阅 Redis 通知频道并路由到本 Pod 的活跃用户流。"""

    def __init__(
        self,
        client: redis.asyncio.Redis,
        *,
        channel: str = NOTIFICATION_CHANNEL,
        queue_size: int = DEFAULT_QUEUE_SIZE,
    ) -> None:
        if queue_size < 1:
            raise ValueError("queue_size must be positive")
        self._client = client
        self._channel = channel
        self._queue_size = queue_size
        self._subscribers: dict[
            tuple[str, uuid.UUID], set[asyncio.Queue[NotificationSignal | None]]
        ] = {}

    @asynccontextmanager
    async def subscribe(
        self, tenant_id: str, user_id: uuid.UUID
    ) -> AsyncIterator[asyncio.Queue[NotificationSignal | None]]:
        queue: asyncio.Queue[NotificationSignal | None] = asyncio.Queue(
            maxsize=self._queue_size
        )
        key = (tenant_id, user_id)
        self._subscribers.setdefault(key, set()).add(queue)
        try:
            yield queue
        finally:
            queues = self._subscribers.get(key)
            if queues is not None:
                queues.discard(queue)
                if not queues:
                    self._subscribers.pop(key, None)

    async def run_forever(self) -> None:
        """订阅循环：Redis 断开时退避重连；取消即退出（不吞真实错误）。"""
        while True:
            pubsub = self._client.pubsub()
            try:
                await pubsub.subscribe(self._channel)
                async for message in pubsub.listen():
                    if message.get("type") != "message":
                        continue
                    self._dispatch(message.get("data"))
            except asyncio.CancelledError:
                raise
            except (RedisError, OSError) as exc:
                logger.warning(
                    "notification_stream_redis_failed",
                    extra={"error_type": type(exc).__name__},
                )
            finally:
                # redis-py 的 PubSub.aclose 未提供类型标注。
                await pubsub.aclose()  # type: ignore[no-untyped-call]
            await asyncio.sleep(1.0)

    async def watermark(self, tenant_id: str, user_id: uuid.UUID) -> int | None:
        """该用户已发布的最新通知 seq；读失败/键缺失返回 `None`（调用方退回一页回填）。"""
        try:
            raw = await self._client.get(notification_watermark_key(tenant_id, user_id))
        except Exception as exc:  # noqa: BLE001 - 水位只是 backfill 优化
            logger.warning(
                "notification_stream_watermark_failed",
                extra={"error_type": type(exc).__name__},
            )
            return None
        try:
            return int(raw) if raw is not None else None
        except (TypeError, ValueError):
            return None

    def _dispatch(self, raw: object) -> None:
        if not isinstance(raw, str):
            logger.warning("notification_stream_bad_payload")
            return
        try:
            data = json.loads(raw)
            signal = NotificationSignal(
                tenant_id=str(data["tenant_id"]),
                user_id=uuid.UUID(str(data["user_id"])),
                notification_id=uuid.UUID(str(data["notification_id"])),
                seq=int(data["seq"]),
                notification=(
                    data.get("notification")
                    if isinstance(data.get("notification"), dict)
                    else None
                ),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            logger.warning("notification_stream_bad_payload")
            return
        queues = self._subscribers.get((signal.tenant_id, signal.user_id))
        if not queues:
            return
        for queue in list(queues):
            try:
                queue.put_nowait(signal)
            except asyncio.QueueFull:
                # 慢订阅：清一格放结束哨兵，流层结束后客户端带 Last-Event-ID 重连回填。
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    queue.put_nowait(None)
                except asyncio.QueueFull:
                    pass
                logger.warning(
                    "notification_stream_slow_subscriber",
                    extra={"tenant_id": signal.tenant_id},
                )

    async def aclose(self) -> None:
        """清空本进程订阅者；pubsub 任务由 lifespan 取消。"""
        for queues in self._subscribers.values():
            for queue in queues:
                try:
                    queue.put_nowait(None)
                except asyncio.QueueFull:
                    pass
        self._subscribers.clear()
