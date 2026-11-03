"""E-02: Cluster 不可用时按既有语义降级（组件边界，不阻断进程）。

真实边界：真实 cluster 客户端对着不可达集群（连接被拒）；断言异常归一与
Null/降级实现，验证进程不会因 RedisClusterException 崩溃。
"""

from __future__ import annotations

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from muad_agent_runtime.infrastructure.cancel_hint import (
    NullCancelHintStore,
    create_cancel_hint_store,
)
from muad_agent_worker.infrastructure.wakeup_hint import (
    NullWakeupListener,
    NullWakeupNotifier,
    create_wakeup_listener,
    create_wakeup_notifier,
)
from muad_common.redis_client import (
    RedisUnavailableError,
    create_pubsub_client,
    create_redis_client,
    verify_redis_topology,
)

DEAD_URL = "redis://127.0.0.1:1/0"
DEAD_SEEDS = ["127.0.0.1:1"]


async def test_e02_unreachable_cluster_raises_normalized_error() -> None:
    client = create_redis_client(DEAD_URL, mode="cluster", seeds=DEAD_SEEDS)
    try:
        with pytest.raises(RedisUnavailableError):
            await verify_redis_topology(client, mode="cluster")
    finally:
        await client.aclose()

    with pytest.raises(RedisConnectionError):
        await create_pubsub_client(DEAD_URL, mode="cluster", seeds=DEAD_SEEDS)


async def test_e02_hints_degrade_to_null_without_crashing() -> None:
    assert isinstance(
        await create_cancel_hint_store(DEAD_URL, mode="cluster", seeds=DEAD_SEEDS),
        NullCancelHintStore,
    )
    assert isinstance(
        await create_wakeup_notifier(DEAD_URL, mode="cluster", seeds=DEAD_SEEDS),
        NullWakeupNotifier,
    )
    assert isinstance(
        await create_wakeup_listener(DEAD_URL, mode="cluster", seeds=DEAD_SEEDS),
        NullWakeupListener,
    )
