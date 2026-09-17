from __future__ import annotations

import logging
from typing import Any

import pytest
import redis.asyncio
from muad_im_gateway.infrastructure.dedupe import (
    DedupeStoreError,
    InMemoryDedupeStore,
    NullDedupeStore,
    RedisDedupeStore,
    build_dedupe_store,
    is_duplicate,
)


class _StubRedis:
    def __init__(self, result: Any = True, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[tuple[str, str, bool, int]] = []
        self.closed = False
        self.ping_error: Exception | None = None

    async def set(
        self, key: str, value: str, *, nx: bool | None = None, ex: int | None = None
    ) -> Any:
        self.calls.append((key, value, bool(nx), ex or 0))
        if self.error is not None:
            raise self.error
        return self.result

    async def exists(self, key: str) -> Any:
        if self.error is not None:
            raise self.error
        return 1 if self.result else 0

    async def ping(self) -> bool:
        if self.ping_error is not None:
            raise self.ping_error
        return True

    async def aclose(self) -> None:
        self.closed = True


async def test_is_duplicate_with_memory_store() -> None:
    store = InMemoryDedupeStore()
    assert await is_duplicate(store, "k", 60) is False
    assert await is_duplicate(store, "k", 60) is True


async def test_is_duplicate_with_null_store_is_always_false() -> None:
    store = NullDedupeStore()
    assert await is_duplicate(store, "k", 60) is False
    assert await is_duplicate(store, "k", 60) is False


async def test_in_memory_store_accepts_first_and_rejects_duplicate() -> None:
    store = InMemoryDedupeStore()
    assert await store.set_if_absent("k", 60) is True
    assert await store.set_if_absent("k", 60) is False


async def test_in_memory_store_expires_entries() -> None:
    now = [0.0]
    store = InMemoryDedupeStore(clock=lambda: now[0])
    assert await store.set_if_absent("k", 10) is True
    assert await store.set_if_absent("k", 10) is False
    now[0] = 10.0
    assert await store.set_if_absent("k", 10) is True


async def test_null_store_is_at_least_once_and_logs_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = NullDedupeStore()
    with caplog.at_level(logging.WARNING):
        assert await store.set_if_absent("k", 60) is False
        assert await store.set_if_absent("k", 60) is False
    assert sum("dedupe_disabled" in record.message for record in caplog.records) == 2


async def test_redis_store_uses_set_nx_ex() -> None:
    client = _StubRedis(result=True)
    store = RedisDedupeStore(client)
    assert await store.set_if_absent("im:dedupe:WECOM:m1", 600) is True
    assert client.calls == [("im:dedupe:WECOM:m1", "1", True, 600)]


async def test_redis_store_rejects_duplicate() -> None:
    client = _StubRedis(result=None)
    store = RedisDedupeStore(client)
    assert await store.set_if_absent("k", 1) is False


async def test_redis_store_wraps_failures() -> None:
    client = _StubRedis(error=redis.asyncio.RedisError("down"))
    store = RedisDedupeStore(client)
    with pytest.raises(DedupeStoreError):
        await store.set_if_absent("k", 1)


async def test_redis_store_closes_client() -> None:
    client = _StubRedis()
    store = RedisDedupeStore(client)
    await store.aclose()
    assert client.closed is True


async def test_build_dedupe_store_without_url_uses_null() -> None:
    store = await build_dedupe_store(None)
    assert isinstance(store, NullDedupeStore)


async def test_build_dedupe_store_ping_failure_uses_null(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _StubRedis()
    client.ping_error = redis.asyncio.RedisError("down")
    monkeypatch.setattr(
        redis.asyncio.Redis,
        "from_url",
        classmethod(lambda cls, url, **kwargs: client),
    )
    store = await build_dedupe_store("redis://example/0")
    assert isinstance(store, NullDedupeStore)
    assert client.closed is True


async def test_build_dedupe_store_ping_success_uses_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _StubRedis()
    monkeypatch.setattr(
        redis.asyncio.Redis,
        "from_url",
        classmethod(lambda cls, url, **kwargs: client),
    )
    store = await build_dedupe_store("redis://example/0")
    assert isinstance(store, RedisDedupeStore)


async def test_in_memory_mark_and_exists() -> None:
    store = InMemoryDedupeStore()
    assert await store.exists("k") is False
    await store.mark("k", 60)
    assert await store.exists("k") is True


async def test_in_memory_mark_expires() -> None:
    now = [0.0]
    store = InMemoryDedupeStore(clock=lambda: now[0])
    await store.mark("k", 10)
    now[0] = 11.0
    assert await store.exists("k") is False


async def test_redis_mark_uses_set_with_ttl() -> None:
    client = _StubRedis(result=True)
    store = RedisDedupeStore(client)
    await store.mark("delivery:dedupe:k", 604800)
    assert client.calls == [("delivery:dedupe:k", "1", False, 604800)]


async def test_redis_exists_and_failure_wrapping() -> None:
    store = RedisDedupeStore(_StubRedis(result=True))
    assert await store.exists("k") is True
    failing = RedisDedupeStore(_StubRedis(error=redis.asyncio.RedisError("down")))
    with pytest.raises(DedupeStoreError):
        await failing.exists("k")


async def test_null_store_never_duplicates_and_mark_is_noop() -> None:
    store = NullDedupeStore()
    await store.mark("k", 60)
    assert await store.exists("k") is False
