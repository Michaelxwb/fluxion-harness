"""集群命令面/key 契约探针（11 项）。

被 `scripts/probe_redis_cluster_commands.py`（上线/本地自查）与 cluster 验收用例共用：
判定一个 Redis 部署是否满足本需求实际用到的命令面与 key 布局。所有写入键都带本次运行的
`muad_probe:{uuid}` 前缀，收尾逐个删除 —— cluster 只有 db 0，不能靠号位隔离。
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from dataclasses import dataclass
from typing import Any

from muad_contracts.im import (
    inbound_stream_key_for_shard,
    lease_epoch_key,
    lease_key,
    outbound_stream_key_for,
)
from redis.asyncio import Redis as PlainRedis
from redis.asyncio import RedisCluster
from redis.crc import key_slot
from redis.exceptions import RedisClusterException, RedisError, ResponseError

PROBE_GROUP = "muad-probe"
PROBE_CONSUMER = "muad-probe-consumer"
_CHANNEL = "muad_probe:channel"


@dataclass(frozen=True)
class ProbeResult:
    name: str
    ok: bool
    detail: str = ""


def _version_tuple(value: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in value.split(".")[:3])
    except ValueError:
        return (0,)


async def run_command_probe(client: RedisCluster) -> list[ProbeResult]:
    """返回 11 项检查结果；不抛业务异常，由调用方决定如何报告。"""
    run_id = uuid.uuid4().hex
    prefix = f"muad_probe:{{{run_id}}}"
    created: list[str] = []
    results: list[ProbeResult] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        results.append(ProbeResult(name=name, ok=ok, detail=detail))

    try:
        cluster = await client.cluster_info()
        state = str(cluster.get("cluster_state") or "")
        record("cluster_state", state == "ok", f"state={state or 'unknown'}")
        slots = int(cluster.get("cluster_slots_assigned") or 0)
        record("slots_covered", slots == 16384, f"{slots}/16384")

        primaries = client.get_primaries()
        node_names = [f"{node.host}:{node.port}" for node in primaries]
        record("primaries", len(primaries) >= 3, f"{node_names}")

        server = await client.info("server")
        version = str(server.get("redis_version") or "")
        record(
            "version_ge_6_2",
            _version_tuple(version)[:2] >= (6, 2),
            f"version={version or 'unknown'}",
        )

        inbound_slots = {
            key_slot(inbound_stream_key_for_shard(i).encode()) for i in range(32)
        }
        outbound_slots = {
            key_slot(outbound_stream_key_for("tenant", bot).encode())
            for bot in ("bot-a", "bot-b")
        }
        lease_slots = {
            key_slot(lease_key("tenant", "bot").encode()),
            key_slot(lease_epoch_key("tenant", "bot").encode()),
        }
        record(
            "hash_tag_same_slot",
            len(inbound_slots) == 1 and len(outbound_slots) == 1 and len(lease_slots) == 1,
            f"inbound={sorted(inbound_slots)} outbound={sorted(outbound_slots)} "
            f"lease={sorted(lease_slots)}",
        )

        stream = f"{prefix}:stream"
        await client.xadd(stream, {"value": "probe"})
        created.append(stream)
        try:
            await client.xgroup_create(stream, PROBE_GROUP, id="0")
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise
        batches = await client.xreadgroup(
            PROBE_GROUP, PROBE_CONSUMER, {stream: ">"}, count=10
        )
        delivered = sum(len(entries) for _, entries in (batches or []))
        record("xreadgroup_same_slot", delivered == 1, f"entries={delivered}")

        claimed = await client.xautoclaim(
            stream,
            PROBE_GROUP,
            PROBE_CONSUMER,
            min_idle_time=0,
            start_id="0-0",
            count=10,
        )
        claimed_ids = [entry[0] for entry in (claimed[1] if claimed else [])]
        record("xautoclaim", bool(claimed_ids), f"claimed={claimed_ids}")

        key_a = f"{prefix}:eval:a"
        key_b = f"{prefix}:eval:b"
        created.extend((key_a, key_b))
        script = (
            "redis.call('SET', KEYS[1], ARGV[1]); "
            "redis.call('SET', KEYS[2], ARGV[1]); "
            "return redis.call('GET', KEYS[2])"
        )
        value = await client.eval(script, 2, key_a, key_b, "probe")
        record("eval_dual_key_same_slot", value == "probe", f"value={value!r}")

        scan_keys = [f"muad_probe:scan-{i}-{run_id}" for i in range(60)]
        for key in scan_keys:
            await client.set(key, "1")
            created.append(key)
        found = {
            key if isinstance(key, str) else key.decode()
            async for key in client.scan_iter(
                match=f"muad_probe:scan-*-{run_id}", count=100
            )
        }
        scan_nodes = {
            (node.host, node.port)
            for key in scan_keys
            if (node := client.get_node_from_key(key, replica=False)) is not None
        }
        record(
            "scan_across_primaries",
            len(found) == len(scan_keys) and len(scan_nodes) >= 2,
            f"found={len(found)}/{len(scan_keys)} primaries={sorted(scan_nodes)}",
        )

        received = await _cross_node_publish(primaries)
        record("cross_node_publish", received == "probe", f"received={received!r}")

        cross_key = f"muad_probe:cross-{run_id}:other"
        while key_slot(cross_key.encode()) == key_slot(key_a.encode()):
            cross_key += "x"
        created.append(cross_key)
        try:
            await client.execute_command("EVAL", script, 2, key_a, cross_key, "probe")
        except (ResponseError, RedisError, RedisClusterException) as exc:
            message = str(exc).upper()
            record(
                "cross_slot_rejected",
                "CROSSSLOT" in message or "SAME KEY SLOT" in message,
                f"{type(exc).__name__}: {exc}",
            )
        else:
            record("cross_slot_rejected", False, "跨 slot EVAL 未被拒绝")
    finally:
        for key in created:
            try:
                await client.delete(key)
            except (RedisError, RedisClusterException) as exc:  # pragma: no cover - 收尾告警
                print(f"[probe] cleanup failed for {key}: {exc}", file=sys.stderr)

    return results


async def _cross_node_publish(primaries: list[Any]) -> str | None:
    """在 primary A 订阅、primary B 发布，验证集群内 PUBLISH 会广播到所有节点。"""
    if len(primaries) < 2:
        return None
    sub_node, pub_node = primaries[0], primaries[1]
    subscriber = PlainRedis(
        host=sub_node.host, port=int(sub_node.port), decode_responses=True
    )
    publisher = PlainRedis(
        host=pub_node.host, port=int(pub_node.port), decode_responses=True
    )
    pubsub = subscriber.pubsub()
    try:
        await pubsub.subscribe(_CHANNEL)
        await asyncio.sleep(0.2)
        await publisher.publish(_CHANNEL, "probe")
        for _ in range(10):
            message = await pubsub.get_message(timeout=0.5)
            if message and message.get("type") == "message":
                data = message.get("data")
                return data if isinstance(data, str) else None
        return None
    finally:
        await pubsub.aclose()
        await subscriber.aclose()
        await publisher.aclose()
