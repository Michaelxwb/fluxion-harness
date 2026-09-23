from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Protocol

import redis.asyncio

logger = logging.getLogger(__name__)

DEDUPE_VALUE = "1"
IN_FLIGHT_VALUE = "in-flight"
DELIVERED_VALUE = "delivered"


class DedupeStoreError(Exception):
    pass


class DedupeStore(Protocol):
    async def set_if_absent(self, key: str, ttl_sec: int) -> bool: ...
    async def exists(self, key: str) -> bool: ...
    async def mark(self, key: str, ttl_sec: int) -> None: ...
    async def reserve(self, key: str, ttl_sec: int) -> bool: ...
    async def get_value(self, key: str) -> str | None: ...
    async def release(self, key: str) -> None: ...
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
            await self._client.set(key, DELIVERED_VALUE, ex=ttl_sec)
        except (redis.RedisError, OSError) as exc:
            raise DedupeStoreError(str(exc)) from exc

    async def reserve(self, key: str, ttl_sec: int) -> bool:
        """`SET NX` 原子占位（值标记为 in-flight），成功只代表占位而非已送达。"""
        try:
            result = await self._client.set(key, IN_FLIGHT_VALUE, nx=True, ex=ttl_sec)
        except (redis.RedisError, OSError) as exc:
            raise DedupeStoreError(str(exc)) from exc
        return bool(result)

    async def get_value(self, key: str) -> str | None:
        try:
            value = await self._client.get(key)
        except (redis.RedisError, OSError) as exc:
            raise DedupeStoreError(str(exc)) from exc
        if value is None:
            return None
        return value.decode() if isinstance(value, bytes) else str(value)

    async def release(self, key: str) -> None:
        try:
            await self._client.delete(key)
        except (redis.RedisError, OSError) as exc:
            raise DedupeStoreError(str(exc)) from exc

    async def aclose(self) -> None:
        await self._client.aclose()


class InMemoryDedupeStore:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._entries: dict[str, tuple[str, float]] = {}

    async def set_if_absent(self, key: str, ttl_sec: int) -> bool:
        now = self._clock()
        self._purge(now)
        if key in self._entries:
            return False
        self._entries[key] = (DEDUPE_VALUE, now + ttl_sec)
        return True

    async def exists(self, key: str) -> bool:
        now = self._clock()
        self._purge(now)
        return key in self._entries

    async def mark(self, key: str, ttl_sec: int) -> None:
        self._entries[key] = (DELIVERED_VALUE, self._clock() + ttl_sec)

    async def reserve(self, key: str, ttl_sec: int) -> bool:
        now = self._clock()
        self._purge(now)
        if key in self._entries:
            return False
        self._entries[key] = (IN_FLIGHT_VALUE, now + ttl_sec)
        return True

    async def get_value(self, key: str) -> str | None:
        now = self._clock()
        self._purge(now)
        entry = self._entries.get(key)
        return entry[0] if entry is not None else None

    async def release(self, key: str) -> None:
        self._entries.pop(key, None)

    async def aclose(self) -> None:
        self._entries.clear()

    def _purge(self, now: float) -> None:
        expired = [key for key, (_, expires_at) in self._entries.items() if expires_at <= now]
        for key in expired:
            self._entries.pop(key)


class NullDedupeStore:
    """Redis 不可用时的降级：不做去重，每次都是真实发送（at-least-once）。"""

    async def set_if_absent(self, key: str, ttl_sec: int) -> bool:
        logger.warning("dedupe_disabled key=%s ttl_sec=%s", key, ttl_sec)
        return False

    async def exists(self, key: str) -> bool:
        return False

    async def mark(self, key: str, ttl_sec: int) -> None:
        logger.warning("dedupe_disabled_mark key=%s ttl_sec=%s", key, ttl_sec)

    async def reserve(self, key: str, ttl_sec: int) -> bool:
        logger.warning("dedupe_disabled_reserve key=%s ttl_sec=%s", key, ttl_sec)
        return True

    async def get_value(self, key: str) -> str | None:
        return None

    async def release(self, key: str) -> None:
        return None

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
