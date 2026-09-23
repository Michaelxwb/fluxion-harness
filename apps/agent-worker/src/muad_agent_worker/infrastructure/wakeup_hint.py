from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from typing import Protocol, cast

from redis.asyncio import Redis
from redis.exceptions import RedisError

TASK_WAKEUP_KEY = "task:wakeup"
LISTEN_RETRY_SEC = 5.0

logger = logging.getLogger(__name__)


class AsyncRedisLike(Protocol):
    async def publish(self, channel: str, message: str) -> int: ...

    async def ping(self) -> bool: ...

    async def aclose(self) -> None: ...


class WakeupNotifier(Protocol):
    async def notify(self) -> None: ...

    async def aclose(self) -> None: ...


class NullWakeupNotifier:
    """未配置 Redis 时的空实现：任务仍由 PG 扫描推进。"""

    async def notify(self) -> None:
        return None

    async def aclose(self) -> None:
        return None


class RedisWakeupNotifier:
    def __init__(self, client: AsyncRedisLike) -> None:
        self._client = client

    async def notify(self) -> None:
        try:
            await self._client.publish(TASK_WAKEUP_KEY, "1")
        except RedisError:
            # hint 只是低延迟优化，权威状态在 PG；发不出去不影响已提交的任务。
            logger.warning("task_wakeup_hint_failed")

    async def aclose(self) -> None:
        await self._client.aclose()


def _build_client(redis_url: str) -> AsyncRedisLike:
    return cast(AsyncRedisLike, Redis.from_url(redis_url, decode_responses=True))


async def create_wakeup_notifier(
    redis_url: str | None,
    client_factory: Callable[[str], AsyncRedisLike] = _build_client,
) -> WakeupNotifier:
    if not redis_url:
        return NullWakeupNotifier()
    client = client_factory(redis_url)
    try:
        await client.ping()
    except RedisError:
        logger.warning("task_wakeup_redis_unavailable")
        await client.aclose()
        return NullWakeupNotifier()
    return RedisWakeupNotifier(client)


class WakeupListener(Protocol):
    async def wait(self, timeout: float) -> None: ...

    async def aclose(self) -> None: ...


class NullWakeupListener:
    """无 Redis：按 poll 间隔等待，PG 扫描照常推进。"""

    async def wait(self, timeout: float) -> None:
        await asyncio.sleep(timeout)

    async def aclose(self) -> None:
        return None


class RedisWakeupListener:
    """订阅 `task:wakeup`：新 Task 提交后让空闲 Worker 提前结束 poll 等待。

    只是低延迟 hint——订阅断开时退避重连，期间 Worker 退化为按 poll 间隔扫描。
    """

    def __init__(self, client: Redis) -> None:
        self._client = client
        self._event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._listen())

    async def _listen(self) -> None:
        while True:
            try:
                async with self._client.pubsub() as pubsub:
                    await pubsub.subscribe(TASK_WAKEUP_KEY)
                    async for message in pubsub.listen():
                        if message.get("type") == "message":
                            self._event.set()
            except RedisError:
                logger.warning("task_wakeup_listen_failed")
            await asyncio.sleep(LISTEN_RETRY_SEC)

    async def wait(self, timeout: float) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._event.wait(), timeout)
        self._event.clear()

    async def aclose(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        await self._client.aclose()


async def create_wakeup_listener(redis_url: str | None) -> WakeupListener:
    if not redis_url:
        return NullWakeupListener()
    client = Redis.from_url(redis_url, decode_responses=True)
    try:
        await client.ping()
    except RedisError:
        logger.warning("task_wakeup_redis_unavailable")
        await client.aclose()
        return NullWakeupListener()
    listener = RedisWakeupListener(client)
    listener.start()
    return listener
