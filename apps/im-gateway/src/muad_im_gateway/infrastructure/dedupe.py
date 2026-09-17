from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Protocol

import redis.asyncio

logger = logging.getLogger(__name__)

DEDUPE_VALUE = "1"


class DedupeStoreError(Exception):
    pass


class DedupeStore(Protocol):
    async def set_if_absent(self, key: str, ttl_sec: int) -> bool: ...
    async def exists(self, key: str) -> bool: ...
    async def mark(self, key: str, ttl_sec: int) -> None: ...
    async def aclose(self) -> None: ...


class RedisDedupeStore:
    def __init__(self, client: redis.asyncio.Redis) -> None:
        self._client = client

    async def set_if_absent(self, key: str, ttl_sec: int) -> bool:
        try:
            result = await self._client.set(key, DEDUPE_VALUE, nx=True, ex=ttl_sec)
        except (redis.RedisError, OSError) as exc:
            raise DedupeStoreError(str(exc)) from exc
        return bool(result)

    async def exists(self, key: str) -> bool:
        try:
            found = await self._client.exists(key)
        except (redis.RedisError, OSError) as exc:
            raise DedupeStoreError(str(exc)) from exc
        return bool(found)

    async def mark(self, key: str, ttl_sec: int) -> None:
        try:
            await self._client.set(key, DEDUPE_VALUE, ex=ttl_sec)
        except (redis.RedisError, OSError) as exc:
            raise DedupeStoreError(str(exc)) from exc

    async def aclose(self) -> None:
        await self._client.aclose()


class InMemoryDedupeStore:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._expires_at: dict[str, float] = {}

    async def set_if_absent(self, key: str, ttl_sec: int) -> bool:
        now = self._clock()
        self._purge(now)
        if key in self._expires_at:
            return False
        self._expires_at[key] = now + ttl_sec
        return True

    async def exists(self, key: str) -> bool:
        now = self._clock()
        self._purge(now)
        return key in self._expires_at

    async def mark(self, key: str, ttl_sec: int) -> None:
        self._expires_at[key] = self._clock() + ttl_sec

    async def aclose(self) -> None:
        self._expires_at.clear()

    def _purge(self, now: float) -> None:
        expired = [key for key, expires_at in self._expires_at.items() if expires_at <= now]
        for key in expired:
            self._expires_at.pop(key)


class NullDedupeStore:
    async def set_if_absent(self, key: str, ttl_sec: int) -> bool:
        logger.warning("dedupe_disabled key=%s ttl_sec=%s", key, ttl_sec)
        return False

    async def exists(self, key: str) -> bool:
        return False

    async def mark(self, key: str, ttl_sec: int) -> None:
        logger.warning("dedupe_disabled_mark key=%s ttl_sec=%s", key, ttl_sec)

    async def aclose(self) -> None:
        return None


async def is_duplicate(store: DedupeStore, key: str, ttl_sec: int) -> bool:
    if isinstance(store, NullDedupeStore):
        return False
    return not await store.set_if_absent(key, ttl_sec)


async def build_dedupe_store(redis_url: str | None) -> DedupeStore:
    if not redis_url:
        logger.warning("dedupe_redis_url_missing using_null_store")
        return NullDedupeStore()
    client = redis.asyncio.Redis.from_url(redis_url)
    try:
        await client.ping()
    except (redis.RedisError, OSError) as exc:
        logger.warning("dedupe_redis_ping_failed error=%s using_null_store", exc)
        await client.aclose()
        return NullDedupeStore()
    logger.info("dedupe_redis_connected")
    return RedisDedupeStore(client)
