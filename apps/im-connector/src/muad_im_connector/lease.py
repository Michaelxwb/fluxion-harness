"""bot 所有权租约：Redis 原子 acquire/renew/release + 单调 fencing epoch。

语义（design §3.3「connector:bot:{tenant}:{bot_id}」）：

- **单 owner**：同一 bot 在任意时刻只有一个 Connector 可以建立长连接；
- **epoch 单调**：epoch 存在独立计数器里（不随 lease 过期删除），旧 owner 的 fencing token
  永远小于新 owner；即使 lease 键过期并被重建也不会回退；
- **过期即失权**：续租用 owner+epoch 比较，失配返回 None，调用方必须立刻停连接。

时间取 **Redis 服务端 TIME**：Connector 可能跑在不同主机上，不能拿本地墙钟裁决过期。
"""

from __future__ import annotations

import json
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any, cast

from redis.asyncio import Redis

ACQUIRE_SCRIPT = """
local lease_key = KEYS[1]
local epoch_key = KEYS[2]
local owner = ARGV[1]
local ttl_ms = tonumber(ARGV[2])
local ttl_sec = tonumber(ARGV[3])
local now_parts = redis.call('TIME')
local now = tonumber(now_parts[1]) + tonumber(now_parts[2]) / 1000000
local current = redis.call('GET', lease_key)
if current then
  local decoded = cjson.decode(current)
  local expires = tonumber(decoded['expires_at']) or 0
  if expires > now and decoded['owner_id'] ~= owner then
    return {0, tonumber(decoded['epoch']) or 0, expires}
  end
end
local epoch = redis.call('INCR', epoch_key)
if epoch < 1 then
  epoch = 1
  redis.call('SET', epoch_key, epoch)
end
local record = cjson.encode({owner_id=owner, epoch=epoch, expires_at=now + ttl_sec})
redis.call('SET', lease_key, record, 'PX', ttl_ms)
return {1, epoch, now + ttl_sec}
"""

RENEW_SCRIPT = """
local lease_key = KEYS[1]
local owner = ARGV[1]
local epoch = tonumber(ARGV[2])
local ttl_ms = tonumber(ARGV[3])
local ttl_sec = tonumber(ARGV[4])
local current = redis.call('GET', lease_key)
if not current then
  return {0, epoch, 0}
end
local decoded = cjson.decode(current)
if decoded['owner_id'] ~= owner or tonumber(decoded['epoch']) ~= epoch then
  return {0, tonumber(decoded['epoch']) or 0, tonumber(decoded['expires_at']) or 0}
end
local now_parts = redis.call('TIME')
local now = tonumber(now_parts[1]) + tonumber(now_parts[2]) / 1000000
local record = cjson.encode({owner_id=owner, epoch=epoch, expires_at=now + ttl_sec})
redis.call('SET', lease_key, record, 'PX', ttl_ms)
return {1, epoch, now + ttl_sec}
"""

RELEASE_SCRIPT = """
local lease_key = KEYS[1]
local owner = ARGV[1]
local epoch = tonumber(ARGV[2])
local current = redis.call('GET', lease_key)
if not current then
  return 0
end
local decoded = cjson.decode(current)
if decoded['owner_id'] ~= owner or tonumber(decoded['epoch']) ~= epoch then
  return 0
end
redis.call('DEL', lease_key)
return 1
"""


@dataclass(frozen=True, slots=True)
class BotLease:
    """一次成功取得的 bot 所有权事实。"""

    tenant_id: str
    bot_id: str
    owner_id: str
    epoch: int
    expires_at: float

    def matches(self, other: BotLease) -> bool:
        return (
            self.tenant_id == other.tenant_id
            and self.bot_id == other.bot_id
            and self.owner_id == other.owner_id
            and self.epoch == other.epoch
        )


class RedisLeaseManager:
    """Redis 租约管理器；所有方法都用 Lua 保证原子性。"""

    def __init__(self, redis: Redis, *, owner_id: str, ttl_sec: float) -> None:
        if not owner_id:
            raise ValueError("owner_id is required")
        if ttl_sec < 1:
            raise ValueError("ttl_sec must be >= 1")
        self._redis = redis
        self._owner_id = owner_id
        self._ttl_sec = ttl_sec
        self._ttl_ms = int(ttl_sec * 1000)

    @property
    def owner_id(self) -> str:
        return self._owner_id

    @property
    def ttl_sec(self) -> float:
        return self._ttl_sec

    @staticmethod
    def lease_key(tenant_id: str, bot_id: str) -> str:
        return f"connector:bot:{tenant_id}:{bot_id}"

    @staticmethod
    def epoch_key(tenant_id: str, bot_id: str) -> str:
        return f"connector:bot:{tenant_id}:{bot_id}:epoch"

    async def _eval(self, script: str, keys: list[str], *args: str) -> Any:
        return await cast(
            Awaitable[Any], self._redis.eval(script, len(keys), *keys, *args)
        )

    async def acquire(self, tenant_id: str, bot_id: str) -> BotLease | None:
        result = await self._eval(
            ACQUIRE_SCRIPT,
            [self.lease_key(tenant_id, bot_id), self.epoch_key(tenant_id, bot_id)],
            self._owner_id,
            str(self._ttl_ms),
            str(self._ttl_sec),
        )
        acquired, epoch, expires_at = _triple(result)
        if not acquired:
            return None
        return BotLease(
            tenant_id=tenant_id,
            bot_id=bot_id,
            owner_id=self._owner_id,
            epoch=epoch,
            expires_at=expires_at,
        )

    async def renew(self, lease: BotLease) -> BotLease | None:
        result = await self._eval(
            RENEW_SCRIPT,
            [self.lease_key(lease.tenant_id, lease.bot_id)],
            self._owner_id,
            str(lease.epoch),
            str(self._ttl_ms),
            str(self._ttl_sec),
        )
        renewed, _epoch, expires_at = _triple(result)
        if not renewed:
            return None
        return BotLease(
            tenant_id=lease.tenant_id,
            bot_id=lease.bot_id,
            owner_id=self._owner_id,
            epoch=lease.epoch,
            expires_at=expires_at,
        )

    async def release(self, lease: BotLease) -> bool:
        result = await self._eval(
            RELEASE_SCRIPT,
            [self.lease_key(lease.tenant_id, lease.bot_id)],
            self._owner_id,
            str(lease.epoch),
        )
        return bool(result)

    async def read(self, tenant_id: str, bot_id: str) -> BotLease | None:
        raw = await cast(Awaitable[Any], self._redis.get(self.lease_key(tenant_id, bot_id)))
        if not raw:
            return None
        return _decode(raw, tenant_id=tenant_id, bot_id=bot_id)


def _triple(value: Any) -> tuple[int, int, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"lease script returned unexpected value: {value!r}")
    return int(value[0] or 0), int(value[1] or 0), float(value[2] or 0.0)


def _decode(raw: Any, *, tenant_id: str, bot_id: str) -> BotLease:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("lease record is corrupt") from exc
    return BotLease(
        tenant_id=tenant_id,
        bot_id=bot_id,
        owner_id=str(payload.get("owner_id") or ""),
        epoch=int(payload.get("epoch") or 0),
        expires_at=float(payload.get("expires_at") or 0.0),
    )
