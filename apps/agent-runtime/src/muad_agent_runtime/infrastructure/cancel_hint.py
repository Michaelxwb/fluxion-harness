from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from typing import Protocol, cast

from muad_common.redis_client import create_redis_client
from redis.asyncio import Redis
from redis.exceptions import RedisClusterException, RedisError

CANCEL_HINT_KEY_PREFIX = "run:cancel:"
CANCEL_HINT_TTL_SEC = 1800
CANCEL_HINT_VALUE = "1"

logger = logging.getLogger(__name__)


class AsyncRedisLike(Protocol):
    async def setex(self, name: str, time: int, value: str) -> bool: ...

    async def exists(self, name: str) -> int: ...

    async def ping(self) -> bool: ...

    async def aclose(self) -> None: ...


class CancelHintStore(Protocol):
    async def set(self, run_id: uuid.UUID) -> None: ...

    async def is_set(self, run_id: uuid.UUID) -> bool: ...

    async def aclose(self) -> None: ...


class NullCancelHintStore:
    async def set(self, run_id: uuid.UUID) -> None:
        return None

    async def is_set(self, run_id: uuid.UUID) -> bool:
        return False

    async def aclose(self) -> None:
        return None


class RedisCancelHintStore:
    def __init__(self, client: AsyncRedisLike) -> None:
        self._client = client

    async def set(self, run_id: uuid.UUID) -> None:
        await self._client.setex(
            f"{CANCEL_HINT_KEY_PREFIX}{run_id}",
            CANCEL_HINT_TTL_SEC,
            CANCEL_HINT_VALUE,
        )

    async def is_set(self, run_id: uuid.UUID) -> bool:
        try:
            return bool(await self._client.exists(f"{CANCEL_HINT_KEY_PREFIX}{run_id}"))
        except (RedisError, RedisClusterException):
            logger.warning("cancel_hint_read_failed")
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
        logger.warning("cancel_hint_redis_unavailable")
        await client.aclose()
        return NullCancelHintStore()
    return RedisCancelHintStore(client)
