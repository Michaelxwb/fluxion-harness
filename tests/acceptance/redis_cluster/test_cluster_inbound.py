"""S-02: Cluster 模式下 32 个入站 shard 全部可达且只消费一次。

真实边界：真实 Gateway 子进程 + 真实 3 节点 Redis Cluster + 真实 PostgreSQL。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime

from muad_common.redis_client import RedisClient
from muad_contracts.im import InboundEvent, inbound_stream_key_for_shard
from redis.asyncio import Redis as PlainRedis
from redis.crc import key_slot

from tests.acceptance.im_gateway.environment import (
    BOT_ID,
    TENANT,
    GatewayStack,
    count_tenant_rows,
    restart_process,
)

INBOUND_GROUP = "im-ingress"
DEAD_STREAM = "im:inbound:dead"


def _event(stack: GatewayStack, message_id: str) -> InboundEvent:
    return InboundEvent(
        event_id=uuid.uuid4(),
        channel="WECOM",
        tenant_id=TENANT,
        bot_id=BOT_ID,
        external_message_id=message_id,
        external_user_id=stack.bound_external_user_id,
        external_conversation_id=stack.chat_id,
        text="cluster probe",
        received_at=datetime.now(UTC),
        reply_ref=f"req-{message_id}",
    )


async def _publish(client: RedisClient, stack: GatewayStack, prefix: str, count: int) -> None:
    for shard in range(count):
        await client.xadd(
            inbound_stream_key_for_shard(shard),
            {"event": _event(stack, f"{prefix}-{shard}").model_dump_json()},
            maxlen=5_000,
            approximate=True,
        )


async def _wait_runs(expected: int, *, timeout: float = 180.0) -> int:
    deadline = time.monotonic() + timeout
    current = 0
    while time.monotonic() < deadline:
        current = await count_tenant_rows("runtime.run_record")
        if current >= expected:
            return current
        await asyncio.sleep(2.0)
    return current


async def _pending(client: RedisClient, shard: int) -> int:
    summary = await client.xpending(inbound_stream_key_for_shard(shard), INBOUND_GROUP)
    if isinstance(summary, dict):
        return int(summary.get("pending") or 0)
    return 0


async def test_s02_cluster_inbound(
    cluster_client: RedisClient, gateway_stack: GatewayStack
) -> None:
    stack = gateway_stack
    keys = [inbound_stream_key_for_shard(shard) for shard in range(32)]
    assert len({key_slot(key.encode()) for key in keys}) == 1

    before = await count_tenant_rows("runtime.run_record")
    await _publish(cluster_client, stack, "s02", 32)

    current = await _wait_runs(before + 32)
    await asyncio.sleep(2.0)
    assert await count_tenant_rows("runtime.run_record") == before + 32, (
        f"入站事件必须各处理一次（含重复检查）；当前 {current}"
    )

    assert all([await _pending(cluster_client, shard) == 0 for shard in range(32)])
    assert await cluster_client.xlen(DEAD_STREAM) == 0

    log = stack.processes["gateway"].log_path.read_text(errors="replace")
    assert "MovedError" not in log
    assert "CROSSSLOT" not in log

    # 重启后继续消费同一消费组，pending 归零。
    restart_process(stack.processes["gateway"])
    await _publish(cluster_client, stack, "s02-restart", 1)
    await _wait_runs(before + 33)
    assert all([await _pending(cluster_client, shard) == 0 for shard in range(32)])

    # RULE-09：pub/sub 走普通连接，发布端与订阅端可以分布在不同节点。
    primaries = cluster_client.get_primaries()
    assert primaries
    subscriber = PlainRedis(
        host=primaries[0].host, port=int(primaries[0].port), decode_responses=True
    )
    pubsub = subscriber.pubsub()
    try:
        await pubsub.subscribe("muad:probe:notify")
        publisher = primaries[1] if len(primaries) > 1 else primaries[0]
        await cluster_client.execute_command(
            "PUBLISH", "muad:probe:notify", "ok", target_nodes=publisher
        )
        received = None
        for _ in range(5):
            message = await pubsub.get_message(timeout=1.0)
            if message and message.get("type") == "message":
                received = message.get("data")
                break
        assert received == "ok"
    finally:
        await pubsub.aclose()
        await subscriber.aclose()

    # 保留窗口：已确认条目在窗口外被裁剪，pending 始终受保护（保持 0）。
    deadline = time.monotonic() + 180.0
    while time.monotonic() < deadline:
        lengths = [await cluster_client.xlen(key) for key in keys]
        if sum(lengths) == 0:
            break
        await asyncio.sleep(5.0)
    assert sum([await cluster_client.xlen(key) for key in keys]) == 0
    assert all([await _pending(cluster_client, shard) == 0 for shard in range(32)])
