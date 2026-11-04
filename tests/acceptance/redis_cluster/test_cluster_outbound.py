"""S-04: Cluster 模式下出站命令与结果跨进程可达。

真实边界：真实 Gateway 生产端组件（DeliveryStreams）+ 真实 Connector XREAD
+ 真实 3 节点 cluster Redis + 真实 PostgreSQL。
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import UTC, datetime

from muad_common.redis_client import RedisClient
from muad_contracts.im import (
    DeliveryCommand,
    DeliveryKind,
    command_result_key,
    outbound_cursor_key,
    outbound_stream_key_for,
)
from muad_im_gateway.infrastructure.streams import DeliveryStreams
from redis.crc import key_slot

from tests.acceptance.im_gateway.connector_environment import BOT_ID
from tests.acceptance.im_gateway.environment import TENANT


def _command(text: str, *, bot_id: str = BOT_ID) -> DeliveryCommand:
    return DeliveryCommand(
        command_id=uuid.uuid4(),
        channel="WECOM",
        tenant_id=TENANT,
        bot_id=bot_id,
        external_user_id="cluster-e2e-user",
        kind=DeliveryKind.TEXT,
        text=text,
        created_at=datetime.now(UTC),
    )


async def _wait_result(
    client: RedisClient, command_id: uuid.UUID, *, timeout: float = 40.0
) -> dict[str, object] | None:
    deadline = time.monotonic() + timeout
    key = command_result_key(command_id)
    while time.monotonic() < deadline:
        raw = await client.get(key)
        if raw:
            return json.loads(raw)
        await asyncio.sleep(0.2)
    return None


async def _wait_cursor(
    client: RedisClient, tenant_id: str, bot_id: str, *, timeout: float = 60.0
) -> str | None:
    """Connector 先写 result、随后才推进 cursor；两步之间不是原子写。"""
    deadline = time.monotonic() + timeout
    key = outbound_cursor_key(tenant_id, bot_id)
    while time.monotonic() < deadline:
        cursor = await client.get(key)
        if cursor:
            return cursor
        await asyncio.sleep(0.1)
    return None


async def test_s04_cluster_outbound_roundtrip(
    cluster_client: RedisClient, connector_stack: object
) -> None:
    stream_key = outbound_stream_key_for(TENANT, BOT_ID)
    assert key_slot(stream_key.encode()) == key_slot(
        outbound_stream_key_for(TENANT, "another-bot").encode()
    )

    streams = DeliveryStreams(cluster_client, retention_sec=3_600)
    command = _command("cluster outbound probe")
    await streams.enqueue(command)

    result = await _wait_result(cluster_client, command.command_id)
    assert result is not None, "Connector 未写回出站结果"
    assert result.get("command_id") == str(command.command_id)

    # cursor 只由当前 owner 推进到已处理条目；result 与 cursor 是两次写，需有界等待。
    cursor = await _wait_cursor(cluster_client, TENANT, BOT_ID)
    assert cursor, "owner 必须推进 cursor"

    # 窗口外命令被裁：在一条新的 bot 流上注入很旧的条目，再由生产端 XADD 触发 MINID 裁剪。
    stale_bot = f"cluster-stale-{uuid.uuid4().hex[:8]}"
    stale_stream = outbound_stream_key_for(TENANT, stale_bot)
    stale = _command("stale command", bot_id=stale_bot)
    stale_entry = await cluster_client.xadd(
        stale_stream,
        {"event": stale.model_dump_json()},
        id="1000-0",
    )
    assert stale_entry == "1000-0"
    await streams.enqueue(_command("after-stale", bot_id=stale_bot))
    entries = await cluster_client.xrange(stale_stream, "1000-0", "1000-0")
    assert entries == [], "窗口外（早于保留窗口）的命令必须被裁掉"
    await cluster_client.delete(stale_stream)
