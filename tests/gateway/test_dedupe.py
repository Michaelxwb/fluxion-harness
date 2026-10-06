"""去重存储：**占位有主、终值 CAS**（2026-10-06 重写）。

被替换掉的旧口径是"占位只写一个常量值、释放无条件删键"：占位过期后新请求能拿到同一个键，
旧主人随后失败时那记无条件 `DEL` 会把**新主人已经写下的成功标记**删掉（实测：三次调用发出
两条真消息）。所以这里逐条钉住"只有占位仍属于自己才动它"。
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
import redis.asyncio
from muad_im_gateway.infrastructure.dedupe import (
    DEDUPE_VALUE,
    DELIVERED_PREFIX,
    IN_FLIGHT_PREFIX,
    DedupeStoreError,
    InMemoryDedupeStore,
    NullDedupeStore,
    RedisDedupeStore,
    build_dedupe_store,
    delivered_value,
    in_flight_value,
    parse_delivered,
)


class _StubRedis:
    """按真实语义模拟 Redis：值比较在 `eval` 里做（脚本内容不看，只看比较出的结果）。"""

    def __init__(self, result: Any = True, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[tuple[str, str, bool, int]] = []
        self.scripts: list[tuple[str, str, str]] = []
        self.deleted: list[str] = []
        self.stored: dict[str, str] = {}
        self.closed = False
        self.ping_error: Exception | None = None

    async def set(
        self, key: str, value: str, *, nx: bool | None = None, ex: int | None = None
    ) -> Any:
        self.calls.append((key, value, bool(nx), ex or 0))
        if self.error is not None:
            raise self.error
        return self.result

    async def get(self, key: str) -> Any:
        if self.error is not None:
            raise self.error
        return self.result

    async def delete(self, key: str) -> Any:
        self.deleted.append(key)
        if self.error is not None:
            raise self.error
        return 1

    async def exists(self, key: str) -> Any:
        if self.error is not None:
            raise self.error
        return 1 if self.result else 0

    async def eval(
        self, script: str, numkeys: int, key: str, expected: str, *args: object
    ) -> Any:
        """只模拟"值等于 expected 才动作"这一条语义 —— 脚本名由调用序区分。"""
        if self.error is not None:
            raise self.error
        self.scripts.append((script, key, expected))
        if self.stored.get(key) != expected:
            return 0
        if "EXPIRE" in script:
            return 1
        if args:
            self.stored[key] = str(args[0])
            return 1
        self.stored.pop(key, None)
        self.deleted.append(key)
        return 1

    async def ping(self) -> bool:
        if self.ping_error is not None:
            raise self.ping_error
        return True

    async def aclose(self) -> None:
        self.closed = True


async def test_memory_store_reserves_once_and_expires() -> None:
    now = [0.0]
    store = InMemoryDedupeStore(clock=lambda: now[0])
    owner = await store.reserve("k", 10)
    assert owner is not None
    assert await store.reserve("k", 10) is None  # 已被占
    now[0] = 10.0
    assert await store.reserve("k", 10) is not None  # 过期后可再占


async def test_memory_store_mark_is_owner_guarded() -> None:
    """**所有权是硬条件**：旧主人不能把新主人的键改成自己的结论。"""
    now = [0.0]
    store = InMemoryDedupeStore(clock=lambda: now[0])
    stale = await store.reserve("k", 1)
    now[0] = 2.0  # 占位过期
    new = await store.reserve("k", 60)  # 新请求拿到同一个键
    assert stale is not None and new is not None
    assert await store.mark("k", stale, DEDUPE_VALUE, 60) is False
    assert await store.get_value("k") == in_flight_value(new)
    assert await store.mark("k", new, DEDUPE_VALUE, 60) is True
    assert await store.get_value("k") == DEDUPE_VALUE


async def test_memory_store_release_is_owner_guarded() -> None:
    now = [0.0]
    store = InMemoryDedupeStore(clock=lambda: now[0])
    stale = await store.reserve("k", 1)
    now[0] = 2.0
    fresh = await store.reserve("k", 60)
    assert stale is not None and fresh is not None
    assert await store.release("k", stale) is False
    assert await store.exists("k") is True
    assert await store.release("k", fresh) is True
    assert await store.exists("k") is False


async def test_memory_store_renew_extends_only_our_placeholder() -> None:
    now = [0.0]
    store = InMemoryDedupeStore(clock=lambda: now[0])
    owner = await store.reserve("k", 10)
    assert owner is not None
    now[0] = 9.0
    assert await store.renew("k", owner, 10) is True
    now[0] = 15.0  # 若不续租，此刻已过期
    assert await store.exists("k") is True
    assert await store.renew("k", "someone-else", 10) is False


async def test_redis_store_reserve_returns_a_unique_owner_token() -> None:
    client = _StubRedis(result=True)
    store = RedisDedupeStore(client)  # type: ignore[arg-type]
    owner = await store.reserve("im:dedupe:WECOM:m1", 600)
    assert owner is not None
    key, value, nx, ttl = client.calls[0]
    assert (key, nx, ttl) == ("im:dedupe:WECOM:m1", True, 600)
    assert value == in_flight_value(owner)


async def test_redis_store_rejects_duplicate() -> None:
    client = _StubRedis(result=None)
    store = RedisDedupeStore(client)  # type: ignore[arg-type]
    assert await store.reserve("k", 1) is None


async def test_redis_store_mark_release_renew_compare_and_act() -> None:
    client = _StubRedis(result=None)
    store = RedisDedupeStore(client)  # type: ignore[arg-type]
    client.stored["k"] = in_flight_value("owner")
    assert await store.mark("k", "owner", delivered_value({"outcome": "DELIVERED"}), 604800) is True
    assert client.stored["k"].startswith(DELIVERED_PREFIX)
    client.stored["k"] = in_flight_value("owner")
    assert await store.renew("k", "owner", 30) is True
    assert await store.release("k", "owner") is True
    # 别人的占位：三个动作都不生效
    client.stored["k"] = in_flight_value("someone-else")
    assert await store.mark("k", "owner", DEDUPE_VALUE, 60) is False
    assert await store.renew("k", "owner", 30) is False
    assert await store.release("k", "owner") is False
    assert len([key for key in client.deleted if key == "k"]) == 1


async def test_redis_store_wraps_failures() -> None:
    client = _StubRedis(error=redis.asyncio.RedisError("down"))
    store = RedisDedupeStore(client)  # type: ignore[arg-type]
    with pytest.raises(DedupeStoreError):
        await store.reserve("k", 1)
    with pytest.raises(DedupeStoreError):
        await store.mark("k", "owner", DEDUPE_VALUE, 1)
    with pytest.raises(DedupeStoreError):
        await store.release("k", "owner")
    with pytest.raises(DedupeStoreError):
        await store.renew("k", "owner", 1)
    with pytest.raises(DedupeStoreError):
        await store.get_value("k")
    with pytest.raises(DedupeStoreError):
        await store.exists("k")


async def test_redis_store_closes_client() -> None:
    client = _StubRedis()
    store = RedisDedupeStore(client)  # type: ignore[arg-type]
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


async def test_null_store_is_fail_open_and_still_hands_out_owners(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Redis 不可用时**照常放行**（丢消息比重复消息更严重），且返回的所有者各自独立。"""
    store = NullDedupeStore()
    with caplog.at_level(logging.WARNING):
        first = await store.reserve("k", 60)
        second = await store.reserve("k", 60)
    assert first is not None and second is not None and first != second
    assert await store.get_value("k") is None
    assert await store.mark("k", first, DEDUPE_VALUE, 60) is True
    assert await store.release("k", first) is True
    assert await store.renew("k", first, 60) is True
    assert sum("dedupe_disabled_reserve" in record.message for record in caplog.records) == 2


async def test_delivered_value_round_trips_the_first_result() -> None:
    """送达标记带着**首次交付的结果**：重放要能原样回给调用方（降级链接只出现过一次）。"""
    payload = {"outcome": "DEGRADED", "fallback_url": "https://example.invalid/f", "n": 1}
    value = delivered_value(payload)
    assert value.startswith(DELIVERED_PREFIX)
    assert parse_delivered(value) == payload


async def test_parse_delivered_rejects_placeholders_and_empty() -> None:
    assert parse_delivered(None) is None
    assert parse_delivered(in_flight_value("abc")) is None
    assert parse_delivered(DEDUPE_VALUE) is None
    assert parse_delivered(f"{DELIVERED_PREFIX}not-json") == {}  # 标记在 = 已送达


async def test_prefixes_are_distinct() -> None:
    assert not IN_FLIGHT_PREFIX.startswith(DELIVERED_PREFIX)
    assert not DELIVERED_PREFIX.startswith(IN_FLIGHT_PREFIX)
