"""TenantRedisCache：tenant-scoped L2 缓存 adapter（closure TASK-006 / P1）。

Redis L2 可选增强（P1 基础设施，正确性不依赖——remediation §13.5）。
L1 内存缓存必备。Redis 不可用 → degraded 模式（get 返回 None，set 静默）。
"""

from __future__ import annotations

import json
from typing import Any

from redis.asyncio import Redis, from_url

from fluxion.observability.tracing import traced_scope


class TenantRedisCache:
    """tenant-scoped key 的 Redis L2 缓存（degraded fallback 到 miss）。"""

    def __init__(self, redis_url: str = "redis://localhost:6379/15") -> None:
        self._redis_url = redis_url
        self._client: Redis | None = None

    async def initialize(self) -> None:
        try:
            self._client = from_url(self._redis_url, decode_responses=True)
            await self._client.ping()
        except Exception:
            self._client = None  # degraded

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def degraded(self) -> bool:
        return self._client is None

    async def get(self, key: str) -> str | None:
        # O506（TASK-008）：Redis cache span（degraded 无 client 时不产 span）
        if self._client is None:
            return None
        async with traced_scope(
            "redis.cache", attributes={"db.operation": "get", "fluxion.cache_key": key}
        ):
            try:
                return await self._client.get(key)
            except Exception:
                return None

    async def set(self, key: str, value: str, ttl: int = 300) -> None:
        if self._client is None:
            return
        async with traced_scope(
            "redis.cache", attributes={"db.operation": "set", "fluxion.cache_key": key}
        ):
            try:
                await self._client.set(key, value, ex=ttl)
            except Exception:
                pass  # degraded：缓存写失败不影响主流程

    async def invalidate(self, key: str) -> None:
        if self._client is None:
            return
        try:
            await self._client.delete(key)
        except Exception:
            pass

    async def clear_all(self, pattern: str = "fluxion:*") -> int:
        """按 pattern 清空缓存键（运维/测试用）。"""
        if self._client is None:
            return 0
        count = 0
        try:
            async for key in self._client.scan_iter(match=pattern):
                await self._client.delete(key)
                count += 1
        except Exception:
            pass
        return count
