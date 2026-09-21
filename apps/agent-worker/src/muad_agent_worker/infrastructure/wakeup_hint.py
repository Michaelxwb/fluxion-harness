from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Protocol, cast

from redis.asyncio import Redis
from redis.exceptions import RedisError

TASK_WAKEUP_KEY = "task:wakeup"

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
