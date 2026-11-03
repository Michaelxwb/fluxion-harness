"""S-05: Cluster 模式下 PlatformSession 失效清空所有 session。

真实边界：真实 3 节点 cluster Redis + 真实 MSS 适配器 + 真实 HTTP 平台探针；
PlatformSession 的 acquire（4-key EVAL）与失效清理都由 Console 使用的同一套
platform-sdk 组件执行（本场景不经过 Console HTTP 层）。
"""

from __future__ import annotations

import uuid

from muad_platform_sdk import MSSAdapter, PlatformAdapterRegistry
from muad_platform_sdk.session.invalidator import RedisPlatformSessionInvalidator
from muad_platform_sdk.session.manager import SessionRequest
from muad_platform_sdk.session.redis_manager import RedisPlatformSessionManager

from tests.sdk.test_mss_sessions import platform_server as _platform_server_fixture

platform_server = _platform_server_fixture


async def test_s05_cluster_platform_session_clear(cluster_client, platform_server) -> None:
    server, platform, credential = platform_server
    registry = PlatformAdapterRegistry()
    registry.register(MSSAdapter())

    tenant = "cluster-session-" + uuid.uuid4().hex[:8]
    platform_id = str(uuid.uuid4())
    manager = RedisPlatformSessionManager(
        cluster_client, registry, tenant_id=tenant, platform_id=platform_id
    )

    # 三个 actor session：每个 acquire 都走 4-key EVAL（session/index/lock/generation）。
    sessions = []
    for scope in ("user:a", "user:b", "user:c"):
        request = SessionRequest(platform, scope, credential.version, "ref", credential)
        session = await manager.acquire(request)
        assert session is not None
        sessions.append(session)

    members = await cluster_client.smembers(manager.index)
    assert len(members) == 3

    # 其他 tenant/platform 的 session 不受影响。
    other = RedisPlatformSessionManager(
        cluster_client,
        registry,
        tenant_id=tenant + "-other",
        platform_id=str(uuid.uuid4()),
    )
    other_request = SessionRequest(platform, "user:a", credential.version, "ref", credential)
    assert await other.acquire(other_request) is not None
    other_key = await cluster_client.smembers(other.index)
    assert len(other_key) == 1

    invalidator = RedisPlatformSessionInvalidator(cluster_client)
    cleared = await invalidator.clear_platform(
        tenant_id=tenant, platform_id=uuid.UUID(platform_id)
    )
    assert cleared == 3

    assert await cluster_client.smembers(manager.index) == set()
    assert await cluster_client.exists(manager.index) == 0
    assert await cluster_client.smembers(other.index) == other_key
