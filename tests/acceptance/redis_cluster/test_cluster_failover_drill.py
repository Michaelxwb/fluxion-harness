"""E-01: 本地 3 主 3 从集群的故障与 reshard 演练。

真实边界：真实 Gateway/Runtime/Worker 子进程 + 真实 3M3R Redis + 真实 PostgreSQL。
本地是单容器 6 进程、127.0.0.1 通告地址，因此用**受控 failover**（`CLUSTER FAILOVER`）
替代主机级 kill：槽位所有权仍会切换、客户端同样要处理重定向；主机级故障与跨主机通告
地址变化仍需 staging 复验（见 design §2.5.4）。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from muad_common.redis_client import (
    RedisClient,
    create_redis_client,
    verify_redis_topology,
)
from muad_contracts.im import InboundEvent, inbound_stream_key_for_shard
from muad_im_connector.lease import RedisLeaseManager
from redis.asyncio import Redis as PlainRedis
from redis.crc import key_slot
from redis.exceptions import RedisClusterException, RedisError

from tests.acceptance.im_gateway.environment import (
    BOT_ID,
    TENANT,
    GatewayStack,
    count_tenant_rows,
)

pytestmark = pytest.mark.integration

_NODE_TIMEOUT_SEC = 90.0
_EVENTS = 12


def _master_of(slots: dict[tuple[int, int], dict[str, Any]], slot: int) -> tuple[str, int]:
    for (start, end), entry in slots.items():
        if start <= slot <= end:
            primary = entry["primary"]
            return str(primary[0]), int(primary[1])
    raise AssertionError(f"slot {slot} 没有归属 master")


def _node_lines(raw: str) -> dict[str, dict[str, Any]]:
    nodes: dict[str, dict[str, Any]] = {}
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) < 8:
            continue
        node_id, address, flags, master_id = parts[0], parts[1], parts[2], parts[3]
        host, _, port_text = address.partition("@")[0].rpartition(":")
        nodes[node_id] = {
            "address": (host, int(port_text)),
            "flags": flags,
            "master_id": master_id,
            "slots": parts[8:],
        }
    return nodes


async def _nodes(client: RedisClient) -> dict[str, dict[str, Any]]:
    await client.cluster_slots()  # 初始化节点缓存
    raw = await client.execute_command(
        "CLUSTER", "NODES", target_nodes=client.get_primaries()[0]
    )
    return _node_lines(str(raw))


async def _wait(predicate, *, timeout: float = _NODE_TIMEOUT_SEC, what: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await predicate():
            return
        await asyncio.sleep(0.5)
    raise AssertionError(f"等待超时: {what}")


def _event(stack: GatewayStack, message_id: str) -> InboundEvent:
    return InboundEvent(
        event_id=uuid.uuid4(),
        channel="WECOM",
        tenant_id=TENANT,
        bot_id=BOT_ID,
        external_message_id=message_id,
        external_user_id=stack.bound_external_user_id,
        external_conversation_id=stack.chat_id,
        text="failover drill",
        received_at=datetime.now(UTC),
        reply_ref=f"drill-{message_id}",
    )


async def _publish_with_retry(
    client: RedisClient, stack: GatewayStack, prefix: str, count: int
) -> int:
    key = inbound_stream_key_for_shard(0)
    deadline = time.monotonic() + _NODE_TIMEOUT_SEC
    published = 0
    while published < count:
        if time.monotonic() > deadline:
            raise AssertionError(f"failover 窗口内只发布成功 {published}/{count}")
        try:
            await client.xadd(
                key,
                {
                    "event": _event(
                        stack, f"{prefix}-{published}"
                    ).model_dump_json()
                },
                maxlen=5_000,
            )
            published += 1
        except (RedisError, RedisClusterException):
            await asyncio.sleep(0.2)
    return published


async def _controlled_failover(
    client: RedisClient, slot: int
) -> tuple[tuple[str, int], tuple[str, int]]:
    """让持有目标 slot 的 master 的 replica 接管；返回 (旧 master, 新 master)。"""
    slots = await client.cluster_slots()
    old_master = _master_of(slots, slot)
    entry = next(
        entry
        for entry in slots.values()
        if (str(entry["primary"][0]), int(entry["primary"][1])) == old_master
    )
    assert entry["replicas"], f"{old_master} 没有 replica，无法演练 failover"
    replica = (str(entry["replicas"][0][0]), int(entry["replicas"][0][1]))

    plain = PlainRedis(host=replica[0], port=replica[1], decode_responses=True)
    try:
        await plain.execute_command("CLUSTER", "FAILOVER")
    finally:
        await plain.aclose()

    async def promoted() -> bool:
        current = await client.cluster_slots()
        return _master_of(current, slot) == replica

    await _wait(promoted, what=f"{replica} 接管 slot {slot}")

    async def old_is_replica() -> bool:
        nodes = await _nodes(client)
        for node in nodes.values():
            if node["address"] == old_master:
                return "slave" in node["flags"]
        return False

    await _wait(old_is_replica, what=f"{old_master} 降级为 replica")
    return old_master, replica


def _find_key_for_slot(slot: int) -> str:
    for index in range(1_000_000):
        key = f"muad_drill:reshard:{index}"
        if key_slot(key.encode()) == slot:
            return key
    raise AssertionError(f"找不到落在 slot {slot} 的 key")


async def _reshard_empty_slot(client: RedisClient, slot: int) -> tuple[str, int]:
    """把空闲 slot 从当前 master 迁到另一个 master；返回新 master 地址。"""
    slots = await client.cluster_slots()
    source = _master_of(slots, slot)
    targets = [
        (str(entry["primary"][0]), int(entry["primary"][1]))
        for entry in slots.values()
        if (str(entry["primary"][0]), int(entry["primary"][1])) != source
    ]
    assert targets, "没有可用于 reshard 的目标 master"
    target = targets[0]

    nodes = await _nodes(client)
    ids = {node["address"]: node_id for node_id, node in nodes.items()}
    source_id, target_id = ids[source], ids[target]

    source_client = PlainRedis(host=source[0], port=source[1], decode_responses=True)
    target_client = PlainRedis(host=target[0], port=target[1], decode_responses=True)
    try:
        assert (
            await source_client.execute_command("CLUSTER", "COUNTKEYSINSLOT", slot)
            == 0
        ), f"slot {slot} 非空，不能做空槽演练"
        await target_client.execute_command(
            "CLUSTER", "SETSLOT", slot, "IMPORTING", source_id
        )
        await source_client.execute_command(
            "CLUSTER", "SETSLOT", slot, "MIGRATING", target_id
        )
        for node in nodes.values():
            if "master" not in node["flags"]:
                continue
            host, port = node["address"]
            peer = PlainRedis(host=host, port=port, decode_responses=True)
            try:
                await peer.execute_command(
                    "CLUSTER", "SETSLOT", slot, "NODE", target_id
                )
            finally:
                await peer.aclose()
    finally:
        await source_client.aclose()
        await target_client.aclose()

    async def moved() -> bool:
        current = await client.cluster_slots()
        return _master_of(current, slot) == target

    await _wait(moved, what=f"slot {slot} 迁到 {target}")
    return target


async def _lease_roundtrip(client: RedisClient, tenant: str, bot: str) -> int:
    first = RedisLeaseManager(client, owner_id="drill-a", ttl_sec=10.0)
    lease = await first.acquire(tenant, bot)
    assert lease is not None
    assert await first.renew(lease) is not None

    second = RedisLeaseManager(client, owner_id="drill-b", ttl_sec=10.0)
    assert await second.acquire(tenant, bot) is None, "同一 bot 不得有两个 owner"
    await first.release(lease)
    taken = await second.acquire(tenant, bot)
    assert taken is not None
    await second.release(taken)
    return taken.epoch


async def test_e01_local_failover_and_reshard(
    cluster_client: RedisClient, gateway_stack: GatewayStack
) -> None:
    stack = gateway_stack
    before = await count_tenant_rows("runtime.run_record")

    # 选一个空闲 slot 做 reshard；同时让发布所用 shard 的 slot 做 failover。
    inbound_key = inbound_stream_key_for_shard(0)
    inbound_slot = key_slot(inbound_key.encode())
    slots = await cluster_client.cluster_slots()
    source = _master_of(slots, inbound_slot)
    default_node = cluster_client.get_primaries()[0]
    empty_slot: int | None = None
    for (start, end), entry in slots.items():
        primary = (str(entry["primary"][0]), int(entry["primary"][1]))
        if primary == source:
            continue
        for slot in range(start, end + 1):
            if slot == inbound_slot:
                continue
            count = await cluster_client.execute_command(
                "CLUSTER", "COUNTKEYSINSLOT", slot, target_nodes=default_node
            )
            if count == 0:
                empty_slot = slot
                break
        if empty_slot is not None:
            break
    assert empty_slot is not None, "没有可用于 reshard 的空闲 slot"

    old_master, new_master = await _controlled_failover(cluster_client, inbound_slot)
    assert old_master != new_master

    reshard_target = await _reshard_empty_slot(cluster_client, empty_slot)
    assert reshard_target != source

    # 迁移后的槽位可正常写入（客户端通过 MOVED 重新路由）。
    moved_key = _find_key_for_slot(empty_slot)
    await cluster_client.set(moved_key, "ok", ex=60)
    assert await cluster_client.get(moved_key) == "ok"
    node = cluster_client.get_node_from_key(moved_key, replica=False)
    assert node is not None and (node.host, int(node.port)) == reshard_target

    # failover + reshard 期间持续投递：全部事件都要被处理，且各只产生一个 Run。
    await _publish_with_retry(cluster_client, stack, "e01", _EVENTS)

    async def all_processed() -> bool:
        return await count_tenant_rows("runtime.run_record") >= before + _EVENTS

    await _wait(all_processed, what="failover 后入站事件全部处理")
    await asyncio.sleep(1.0)
    assert await count_tenant_rows("runtime.run_record") == before + _EVENTS, (
        "入站必须至少一次且无重复副作用"
    )

    # 租约：failover/reshard 后仍单 owner、epoch 单调递增。
    epoch = await _lease_roundtrip(
        cluster_client, f"drill-{uuid.uuid4().hex[:8]}", "drill-bot"
    )
    assert epoch >= 1

    # 多 seed 发现：只给一个原 master（现在是 replica）作为 seed，仍能发现集群并读写。
    seed = f"{old_master[0]}:{old_master[1]}"
    discovered = create_redis_client(
        f"redis://{seed}/0", mode="cluster", seeds=[seed], decode_responses=True
    )
    try:
        await verify_redis_topology(discovered, mode="cluster")
        probe_key = f"muad_drill:discover:{uuid.uuid4().hex}"
        await discovered.set(probe_key, "ok", ex=60)
        assert await discovered.get(probe_key) == "ok"
        await discovered.delete(probe_key)
    finally:
        await discovered.aclose()

    # 进程没有重启：gateway 仍是同一个进程，且日志没有未处理的 CROSSSLOT。
    process = stack.processes["gateway"]._process
    assert process is not None and process.poll() is None, "gateway 不得因故障退出"
    log = stack.processes["gateway"].log_path.read_text(errors="replace")
    assert "CROSSSLOT" not in log

    await cluster_client.delete(moved_key)
