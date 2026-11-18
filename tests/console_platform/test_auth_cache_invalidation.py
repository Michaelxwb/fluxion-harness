"""认证缓存同步失效的核心语义单测（不依赖 DB/Redis，可离线跑作 TASK-006 门禁）。

覆盖：

- 屏障值解析（缺失=READY、READY:<fence>、BLOCKED 裸 fence）。
- key 命名带同租户 hash tag（多 key 原子 EVAL 在 Cluster 下不跨槽）。
- `InvalidationPlan` 空/非空判定。
- `AuthCacheDirectory` 在未装配 Redis 时是空操作（装配缺失 fail-open，不阻断写入）。

真实 Lua 脚本的读取/回填 CAS 语义由 Pod 内真 Redis 集成用例覆盖。
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from muad_common.auth_cache import (
    AUTH_CACHE_ROOT,
    READY_PREFIX,
    barrier_key,
    credential_key,
    parse_barrier,
    permission_key,
    platform_state_key,
    user_version_key,
)


def test_parse_barrier_absent_is_ready() -> None:
    assert parse_barrier(None) == (True, None)


def test_parse_barrier_ready_carries_fence() -> None:
    assert parse_barrier(f"{READY_PREFIX}abc123") == (True, "abc123")
    assert parse_barrier(b"READY:zzz") == (True, "zzz")


def test_parse_barrier_blocked_returns_bare_fence() -> None:
    is_ready, fence = parse_barrier("fence-x")
    assert is_ready is False
    assert fence == "fence-x"


def test_keys_share_tenant_hash_tag() -> None:
    tenant = "t-42"
    uid = uuid.uuid4()
    keys = [
        barrier_key(tenant),
        user_version_key(tenant, uid),
        permission_key(tenant, uid),
        credential_key(tenant, "mssw", "hash"),
        platform_state_key(tenant, "mssw"),
    ]
    for key in keys:
        assert key.startswith(f"{AUTH_CACHE_ROOT}:{{t-42}}:")
        # 只允许一处 `{...}`，其余不含花括号，保证同一 slot。
        assert key.count("{") == 1 and key.count("}") == 1


def test_permission_and_version_keys_are_distinct() -> None:
    tenant = "t1"
    uid = uuid.uuid4()
    assert permission_key(tenant, uid) != user_version_key(tenant, uid)


@pytest.mark.asyncio
async def test_directory_is_noop_without_cache() -> None:
    from muad_console_platform.application.auth_cache_invalidator import (
        AuthCacheDirectory,
        InvalidationPlan,
    )

    class _Session:
        async def execute(self, *a, **k):  # pragma: no cover - 不应被调用
            raise AssertionError("no-op must not touch DB")

    directory = AuthCacheDirectory(_Session(), cache=None)
    # 未装配 Redis：即便有受影响用户也不应触达 DB/Redis。
    await directory.invalidate(
        "t1", plan=InvalidationPlan(user_ids=(uuid.uuid4(),))
    )
    await directory.invalidate("t1", plan=InvalidationPlan())


def test_invalidation_plan_empty() -> None:
    from muad_console_platform.application.auth_cache_invalidator import InvalidationPlan

    assert InvalidationPlan().is_empty() is True
    assert InvalidationPlan(user_ids=(uuid.uuid4(),)).is_empty() is False


# ---------------------------------------------------------------------------
# 真库 + 真 Redis 集成：走真实管理接口，断言版本递增与屏障收敛。
# ---------------------------------------------------------------------------


@pytest.fixture
async def wired_auth_cache():
    """把 `app.state.auth_cache` 接到真实 Redis，使管理接口真正执行失效。

    `ASGITransport` 不跑 lifespan，生产由 `main.py` 装配；这里补上，语义与生产一致。
    """
    from muad_common import SharedSettings
    from muad_common.auth_cache import AuthCacheStore
    from muad_common.redis_client import create_redis_client
    from muad_console_platform.main import app

    settings = SharedSettings()
    if not settings.redis_url:
        pytest.skip("需要真实 Redis")
    # 与生产同构：standalone 返回 `Redis`，cluster 返回 `RedisCluster`。裸 `Redis.from_url` 在
    # 集群模式下多 key 的 EVAL/DEL 会被 MOVED 打回（`AuthCacheUnavailable('MovedError')`）。
    seeds = [
        item.strip()
        for item in (settings.redis_cluster_seeds or "").split(",")
        if item.strip()
    ]
    client = create_redis_client(
        settings.redis_url,
        mode=settings.redis_mode,
        seeds=seeds,
        decode_responses=True,
    )
    app.state.auth_cache = AuthCacheStore(client, ttl_sec=60)
    try:
        yield client
    finally:
        app.state.auth_cache = None
        await client.aclose()


async def test_grant_bumps_revision_and_barrier_ready(
    client: AsyncClient, tenant, wired_auth_cache
) -> None:
    """授权一次：`auth_revision` 递增，租户屏障收敛到 READY，协调行 READY。"""
    from muad_common.auth_cache import barrier_key, parse_barrier
    from muad_console_platform.infrastructure.db import get_session_factory
    from muad_console_platform.infrastructure.models.control import PlatformUser
    from sqlalchemy import text

    headers = {"X-Tenant-Id": tenant.tenant_id}
    async with get_session_factory()() as session:
        user = PlatformUser(
            tenant_id=tenant.tenant_id,
            user_code=f"inv-{uuid.uuid4().hex[:8]}",
            display_name="Inv",
        )
        session.add(user)
        await session.flush()
        user_id = user.id
        await session.commit()
    agent_resp = await client.post(
        "/api/v1/agents",
        json={
            "key": f"inv-{uuid.uuid4().hex[:8]}",
            "name": "Inv Agent",
            "instructions": "inst",
            "model_id": str(tenant.model_id),
        },
        headers=headers,
    )
    assert agent_resp.status_code == 200, agent_resp.text
    agent_id = agent_resp.json()["data"]["id"]

    session_factory = get_session_factory()
    async with session_factory() as session:
        before = await session.scalar(
            text("SELECT auth_revision FROM control.platform_user WHERE id = :id"),
            {"id": user_id},
        )
    grant_resp = await client.post(
        f"/api/v1/agents/{agent_id}/users/{user_id}",
        headers=headers,
    )
    assert grant_resp.status_code == 200, grant_resp.text

    async with session_factory() as session:
        after = await session.scalar(
            text("SELECT auth_revision FROM control.platform_user WHERE id = :id"),
            {"id": user_id},
        )
        phase = await session.scalar(
            text(
                "SELECT phase FROM control.auth_cache_sync_state "
                "WHERE tenant_id = :t AND scope = 'TENANT'"
            ),
            {"t": tenant.tenant_id},
        )
    assert int(after) == int(before) + 1
    assert phase == "READY"
    is_ready, fence = parse_barrier(await wired_auth_cache.get(barrier_key(tenant.tenant_id)))
    assert is_ready is True and fence
