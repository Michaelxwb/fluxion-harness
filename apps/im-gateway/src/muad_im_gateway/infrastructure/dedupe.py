"""幂等键存储：**占位（有主）→ 终值**，两种写入都以「值仍是我写的那个」为条件。

**为什么值里带所有者 token**：占位有 TTL，而一次投递可能比 TTL 还长（大文件上传）。占位过期后
新请求能拿到同一个键，此时旧主人的收尾动作**不能**再动这个键 —— 2026-10-06 实测：A 占位后挂起、
占位过期、B 完成真实发送并写下送达标记，A 失败后无条件 `DEL` 把 B 的标记删掉，C 于是又真发一次，
三次调用发出两条真消息。所以 `release` / `mark` / `renew` 一律是**比较值再动**（Lua 原子执行）。

**为什么要有 `renew`**：不续租时"发送比占位 TTL 长"就等于放弃去重（同一 delivery_key 会被并发
真发两次）；续租把占位维持到发送结束，进程崩溃时占位仍会在 TTL 后自动过期、让重试能补发。

**占位 ≠ 送达**：只有 `delivered:` 前缀的值算成功，占位里的重复请求必须回报"未送达"。
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol, cast

import redis.asyncio

logger = logging.getLogger(__name__)

#: 入站去重的"已受理"标记：只表示"这条消息处理过了"，不含任何交付结论。
DEDUPE_VALUE = "1"
IN_FLIGHT_PREFIX = "in-flight:"
DELIVERED_PREFIX = "delivered:"

#: 比较值再删除：释放占位只对**自己的**占位生效。
_RELEASE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""
#: 比较值再升级：占位 → 终值（带新 TTL）。占位已被别人接管时不动，返回 0。
_MARK_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
  return 1
end
return 0
"""
#: 比较值再续租：长发送期间把占位维持住。
_RENEW_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('EXPIRE', KEYS[1], ARGV[2])
end
return 0
"""


class DedupeStoreError(Exception):
    pass


def in_flight_value(owner: str) -> str:
    return f"{IN_FLIGHT_PREFIX}{owner}"


def delivered_value(payload: Mapping[str, object]) -> str:
    """送达标记的值 = 前缀 + 首次交付结果的规范化 JSON（重放时原样回给调用方）。"""
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return f"{DELIVERED_PREFIX}{body}"


def parse_delivered(value: str | None) -> dict[str, object] | None:
    """送达标记 → 首次交付结果；占位/无值返回 `None`，标记损坏返回 `{}`（仍是送达）。"""
    if value is None or not value.startswith(DELIVERED_PREFIX):
        return None
    raw = value[len(DELIVERED_PREFIX) :]
    try:
        payload = json.loads(raw)
    except ValueError:
        logger.warning("dedupe_delivered_value_corrupt")
        return {}
    return payload if isinstance(payload, dict) else {}


class DedupeStore(Protocol):
    """所有方法都可能抛 `DedupeStoreError`（Redis 故障），由调用方决定降级口径。"""

    async def reserve(self, key: str, ttl_sec: int) -> str | None:
        """占位；返回**所有者 token**（后续释放/升级/续租都要它）。已被占则 `None`。"""
        ...

    async def mark(self, key: str, owner: str, value: str, ttl_sec: int) -> bool:
        """把**自己的**占位升级为终值；占位已不属于自己（过期被接管）时不动并返回 `False`。"""
        ...

    async def release(self, key: str, owner: str) -> bool:
        """释放**自己的**占位；占位已不属于自己时不动并返回 `False`。"""
        ...

    async def renew(self, key: str, owner: str, ttl_sec: int) -> bool:
        """续租**自己的**占位；占位已不属于自己时返回 `False`。"""
        ...

    async def get_value(self, key: str) -> str | None: ...
    async def exists(self, key: str) -> bool: ...
    async def aclose(self) -> None: ...


class RedisDedupeStore:
    def __init__(self, client: redis.asyncio.Redis) -> None:
        self._client = client

    async def reserve(self, key: str, ttl_sec: int) -> str | None:
        owner = uuid.uuid4().hex
        try:
            result = await self._client.set(key, in_flight_value(owner), nx=True, ex=ttl_sec)
        except (redis.RedisError, OSError) as exc:
            raise DedupeStoreError(str(exc)) from exc
        return owner if result else None

    async def mark(self, key: str, owner: str, value: str, ttl_sec: int) -> bool:
        return await self._guarded(_MARK_SCRIPT, key, owner, value, ttl_sec)

    async def release(self, key: str, owner: str) -> bool:
        return await self._guarded(_RELEASE_SCRIPT, key, owner)

    async def renew(self, key: str, owner: str, ttl_sec: int) -> bool:
        return await self._guarded(_RENEW_SCRIPT, key, owner, ttl_sec)

    async def get_value(self, key: str) -> str | None:
        try:
            value = await self._client.get(key)
        except (redis.RedisError, OSError) as exc:
            raise DedupeStoreError(str(exc)) from exc
        if value is None:
            return None
        return value.decode() if isinstance(value, bytes) else str(value)

    async def exists(self, key: str) -> bool:
        try:
            found = await self._client.exists(key)
        except (redis.RedisError, OSError) as exc:
            raise DedupeStoreError(str(exc)) from exc
        return bool(found)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _guarded(self, script: str, key: str, owner: str, *args: object) -> bool:
        """在 Redis 里原子地"比较值再动作"。脚本只在值等于**本所有者**的占位时才改键。"""
        try:
            # `redis.asyncio.Redis.eval` 的标注是 `Awaitable[str] | str`（与同步客户端共用基类），
            # 异步客户端下**必然**是 awaitable —— 这里把它钉回 awaitable。
            pending = cast(
                "Awaitable[Any]",
                self._client.eval(
                    script, 1, key, in_flight_value(owner), *[str(arg) for arg in args]
                ),
            )
            result = await pending
        except (redis.RedisError, OSError) as exc:
            raise DedupeStoreError(str(exc)) from exc
        return bool(result)


class InMemoryDedupeStore:
    """进程内替身，语义与 Redis 版逐条一致（含所有者校验）。"""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._entries: dict[str, tuple[str, float]] = {}

    async def reserve(self, key: str, ttl_sec: int) -> str | None:
        now = self._clock()
        self._purge(now)
        if key in self._entries:
            return None
        owner = uuid.uuid4().hex
        self._entries[key] = (in_flight_value(owner), now + ttl_sec)
        return owner

    async def mark(self, key: str, owner: str, value: str, ttl_sec: int) -> bool:
        if not self._holds(key, owner):
            return False
        self._entries[key] = (value, self._clock() + ttl_sec)
        return True

    async def release(self, key: str, owner: str) -> bool:
        if not self._holds(key, owner):
            return False
        del self._entries[key]
        return True

    async def renew(self, key: str, owner: str, ttl_sec: int) -> bool:
        if not self._holds(key, owner):
            return False
        value, _ = self._entries[key]
        self._entries[key] = (value, self._clock() + ttl_sec)
        return True

    async def get_value(self, key: str) -> str | None:
        now = self._clock()
        self._purge(now)
        entry = self._entries.get(key)
        return entry[0] if entry is not None else None

    async def exists(self, key: str) -> bool:
        now = self._clock()
        self._purge(now)
        return key in self._entries

    async def aclose(self) -> None:
        self._entries.clear()

    def _holds(self, key: str, owner: str) -> bool:
        now = self._clock()
        self._purge(now)
        entry = self._entries.get(key)
        return entry is not None and entry[0] == in_flight_value(owner)

    def _purge(self, now: float) -> None:
        expired = [key for key, (_, expires_at) in self._entries.items() if expires_at <= now]
        for key in expired:
            self._entries.pop(key)


class NullDedupeStore:
    """Redis 不可用时的降级：不做去重，每次都是真实发送（at-least-once）。

    占位一律成功（fail-open）：丢消息比重复消息更严重，不能因 Redis 抖动把用户消息吞掉。
    """

    async def reserve(self, key: str, ttl_sec: int) -> str | None:
        logger.warning("dedupe_disabled_reserve key=%s ttl_sec=%s", key, ttl_sec)
        return uuid.uuid4().hex

    async def mark(self, key: str, owner: str, value: str, ttl_sec: int) -> bool:
        logger.warning("dedupe_disabled_mark key=%s ttl_sec=%s", key, ttl_sec)
        return True

    async def release(self, key: str, owner: str) -> bool:
        return True

    async def renew(self, key: str, owner: str, ttl_sec: int) -> bool:
        return True

    async def get_value(self, key: str) -> str | None:
        return None

    async def exists(self, key: str) -> bool:
        return False

    async def aclose(self) -> None:
        return None


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
