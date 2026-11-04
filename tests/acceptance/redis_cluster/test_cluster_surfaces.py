"""S-06: Cluster 下通知、唤醒、取消、会话、MCP 缓存与去重等 Redis 使用面可用。

真实边界：真实 3 节点 cluster Redis；各组件是五进程运行时使用的同一实现
（通知发布器、cancel hint、worker wakeup、Console session / MCP 缓存、Gateway dedupe）。
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from datetime import UTC, datetime, timedelta

from muad_agent_runtime.infrastructure.cancel_hint import create_cancel_hint_store
from muad_agent_runtime.infrastructure.notification_publisher import NotificationPublisher
from muad_agent_worker.infrastructure.wakeup_hint import (
    create_wakeup_listener,
    create_wakeup_notifier,
)
from muad_common.notification_keys import notification_watermark_key
from muad_common.redis_client import create_pubsub_client_sync, create_redis_client
from muad_common.session_store import RedisSessionStore, SessionRecord
from muad_console_platform.infrastructure.mcp_catalog_cache import RedisMcpCatalogCache
from muad_im_gateway.infrastructure.dedupe import RedisDedupeStore
from redis.asyncio import Redis as PlainRedis

CLUSTER_URL = os.environ.get("MUAD_CLUSTER_REDIS_URL", "redis://127.0.0.1:6391/0")
SEEDS = [
    item.strip()
    for item in os.environ.get(
        "MUAD_CLUSTER_SEEDS", "127.0.0.1:6391,127.0.0.1:6392,127.0.0.1:6393"
    ).split(",")
    if item.strip()
]


async def test_s06_cluster_surfaces(cluster_client) -> None:
    primaries = cluster_client.get_primaries()
    assert len(primaries) >= 2

    # 通知：发布端（派生连接）与订阅端在不同节点也能送达；水位写入 keyed 客户端。
    subscriber = PlainRedis(
        host=primaries[1].host, port=int(primaries[1].port), decode_responses=True
    )
    pubsub = subscriber.pubsub()
    publisher_keyed = create_redis_client(
        CLUSTER_URL, mode="cluster", seeds=SEEDS, decode_responses=True
    )
    publisher = NotificationPublisher(
        publisher_keyed,
        pubsub_client=create_pubsub_client_sync(
            CLUSTER_URL, mode="cluster", seeds=SEEDS, decode_responses=True
        ),
    )
    tenant_id, user_id = "cluster-s06", uuid.uuid4()
    try:
        await pubsub.subscribe("webchat.notifications")
        await publisher.publish(
            tenant_id=tenant_id,
            user_id=user_id,
            notification_id=uuid.uuid4(),
            seq=7,
            notification={"kind": "probe"},
        )
        received = None
        for _ in range(5):
            message = await pubsub.get_message(timeout=1.0)
            if message and message.get("type") == "message":
                received = message.get("data")
                break
        assert received is not None
        watermark = await cluster_client.get(
            notification_watermark_key(tenant_id, user_id)
        )
        assert watermark == "7"
    finally:
        await pubsub.aclose()
        await subscriber.aclose()
        await publisher.aclose()

    # cancel hint：keyed 客户端，标记可被读到。
    hints = await create_cancel_hint_store(CLUSTER_URL, mode="cluster", seeds=SEEDS)
    run_id = uuid.uuid4()
    await hints.set(run_id)
    assert await hints.is_set(run_id)

    # worker wakeup：pub/sub 派生连接，通知能被监听端收到。
    listener = await create_wakeup_listener(CLUSTER_URL, mode="cluster", seeds=SEEDS)
    notifier = await create_wakeup_notifier(CLUSTER_URL, mode="cluster", seeds=SEEDS)
    try:
        await asyncio.sleep(0.5)  # 等订阅建立
        await notifier.notify()
        started = time.monotonic()
        await listener.wait(5.0)
        assert time.monotonic() - started < 4.0
    finally:
        await notifier.aclose()
        await listener.aclose()

    # Console session store：单 key 读写删。
    store = RedisSessionStore(cluster_client)
    token_hash = "cluster-s06-" + uuid.uuid4().hex
    now = datetime.now(UTC)
    record = SessionRecord(
        tenant_id=tenant_id,
        platform_user_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        auth_method="CHAT",
        expires_at=now + timedelta(hours=1),
        idle_ttl_sec=3600,
        agents=(),
    )
    await store.write(token_hash, record, now=now)
    assert (await store.read(token_hash)) is not None
    await store.delete(token_hash)
    assert (await store.read(token_hash)) is None

    # MCP 目录缓存失效：单 key 删除。
    await RedisMcpCatalogCache(cluster_client).invalidate(
        server_id=uuid.uuid4(), revision=1
    )

    # Gateway dedupe：占位 → 终值 → 读取。
    dedupe = RedisDedupeStore(cluster_client)
    key = "cluster:probe:dedupe:" + uuid.uuid4().hex
    owner = await dedupe.reserve(key, 30)
    assert owner
    assert await dedupe.mark(key, owner, "1", 30)
    assert await dedupe.get_value(key) == "1"
    await cluster_client.delete(key)
