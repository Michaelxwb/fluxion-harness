"""Task 协作取消 hint：`task:cancel:{task_id}`（TTL 30m，设计 §3.3.6）。

PG `cancel_requested` 才是权威；hint 只让执行中的 Worker 在两次心跳之间更快停下。
Redis 不可用时降级为 Null 实现，取消照常由心跳读 PG 生效。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from typing import Protocol, cast

from muad_common.redis_client import create_redis_client
from redis.asyncio import Redis
from redis.exceptions import RedisClusterException, RedisError

from ..metrics import record_outcome

TASK_CANCEL_KEY_PREFIX = "task:cancel:"
TASK_CANCEL_TTL_SEC = 1800
TASK_CANCEL_VALUE = "1"

logger = logging.getLogger(__name__)


class AsyncRedisLike(Protocol):
    async def setex(self, name: str, time: int, value: str) -> bool: ...

    async def exists(self, name: str) -> int: ...

    async def ping(self) -> bool: ...

    async def aclose(self) -> None: ...


class CancelHintStore(Protocol):
    async def mark(self, task_id: uuid.UUID) -> None: ...

    async def is_marked(self, task_id: uuid.UUID) -> bool: ...

    async def aclose(self) -> None: ...


class NullCancelHintStore:
    async def mark(self, task_id: uuid.UUID) -> None:
        return None

    async def is_marked(self, task_id: uuid.UUID) -> bool:
        return False

    async def aclose(self) -> None:
        return None


class RedisCancelHintStore:
    def __init__(self, client: AsyncRedisLike) -> None:
        self._client = client

    async def mark(self, task_id: uuid.UUID) -> None:
        try:
            await self._client.setex(
                f"{TASK_CANCEL_KEY_PREFIX}{task_id}", TASK_CANCEL_TTL_SEC, TASK_CANCEL_VALUE
            )
        except (RedisError, RedisClusterException):
            logger.warning("task_cancel_hint_write_failed")
            record_outcome("task_hint_degraded_total", "TASK_CANCEL_HINT_WRITE_FAILED")

    async def is_marked(self, task_id: uuid.UUID) -> bool:
        try:
            return bool(await self._client.exists(f"{TASK_CANCEL_KEY_PREFIX}{task_id}"))
        except (RedisError, RedisClusterException):
            logger.warning("task_cancel_hint_read_failed")
            record_outcome("task_hint_degraded_total", "TASK_CANCEL_HINT_READ_FAILED")
            return False

    async def aclose(self) -> None:
        await self._client.aclose()


def _build_client(
    redis_url: str, *, mode: str = "standalone", seeds: list[str] | None = None
) -> AsyncRedisLike:
    return cast(
        AsyncRedisLike, create_redis_client(redis_url, mode=mode, seeds=seeds)
    )


async def create_cancel_hint_store(
    redis_url: str | None,
    *,
    mode: str = "standalone",
    seeds: list[str] | None = None,
    client_factory: Callable[[str], AsyncRedisLike] | None = None,
) -> CancelHintStore:
    if not redis_url:
        return NullCancelHintStore()
    factory = client_factory or (lambda url: _build_client(url, mode=mode, seeds=seeds))
    client = factory(redis_url)
    try:
        await client.ping()
    except (RedisError, RedisClusterException):
        logger.warning("task_cancel_hint_redis_unavailable")
        record_outcome("task_hint_degraded_total", "TASK_CANCEL_HINT_REDIS_UNAVAILABLE")
        await client.aclose()
        return NullCancelHintStore()
    return RedisCancelHintStore(client)
