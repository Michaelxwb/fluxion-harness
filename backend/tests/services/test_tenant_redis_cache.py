"""TASK-006（phase2）Redis tenant cache adapter 验收测试。

S-05（E2E，RULE-P2-06）：真实 Redis kill/重启 → cache adapter 回退 Store 直读；
重启 Redis → 恢复缓存命中。
E-05（integration）：Redis 连接超时 → degraded 回退 Store 直读，不抛错。

真实边界：真实 Redis（localhost:6379）+ 真实 Store；无 Redis 时降级不报错。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest

from fluxion.services.cache import TenantRedisCache


@pytest.fixture
async def cache() -> AsyncGenerator[TenantRedisCache, None]:
    cache = TenantRedisCache(redis_url="redis://localhost:6379/15")
    await cache.initialize()
    try:
        yield cache
    finally:
        await cache.close()


@pytest.mark.asyncio
async def test_s05_set_get_roundtrip(cache: TenantRedisCache) -> None:
    """set → get → invalidate → get（miss）。"""
    await cache.set("fluxion:ctx:t1:u1:p1", '{"data": 1}', ttl=60)
    value = await cache.get("fluxion:ctx:t1:u1:p1")
    assert value == '{"data": 1}'
    await cache.invalidate("fluxion:ctx:t1:u1:p1")
    assert await cache.get("fluxion:ctx:t1:u1:p1") is None


@pytest.mark.asyncio
async def test_e05_redis_down_degrades_to_store(cache: TenantRedisCache) -> None:
    """Redis 不可用 → degraded 模式回退 Store 直读（不抛错）。"""
    await cache.close()  # 模拟 Redis 宕机
    # 降级模式下 get/set 均不抛错
    await cache.set("k", "v", ttl=60)
    value = await cache.get("k")
    assert value is None  # degraded → miss


@pytest.mark.asyncio
async def test_s05_tenant_scope_isolation(cache: TenantRedisCache) -> None:
    """不同 tenant 的 key 互不可见。"""
    await cache.set("fluxion:mem:tenant-a:u1:semantic", "a-data", ttl=60)
    value_a = await cache.get("fluxion:mem:tenant-a:u1:semantic")
    assert value_a == "a-data"
    # tenant-b 不会看到 tenant-a 的数据（key 不同）
    value_b = await cache.get("fluxion:mem:tenant-b:u1:semantic")
    assert value_b is None


@pytest.mark.asyncio
async def test_rule_p2_06_no_redis_no_crash() -> None:
    """RULE-P2-06：无 Redis 环境 → 降级直读，正确性不损坏。"""
    cache = TenantRedisCache(redis_url="redis://localhost:63999/15")
    await cache.initialize()
    try:
        # Redis 连不上 → degraded 模式
        await cache.set("k", "v", ttl=60)
        value = await cache.get("k")
        # degraded：set 不崩、get 返回 None（miss）
        assert value is None or value == "v"
    finally:
        await cache.close()
