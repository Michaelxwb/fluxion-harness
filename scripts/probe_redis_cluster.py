"""Redis Cluster 上线前置检查（只读）。

校验项：
1) 每个节点地址 TCP 可达（seed 只是发现入口，客户端最终连**通告地址**）；
2) 集群发现：primary 数量、`cluster_state:ok`、16384 槽全覆盖；
3) `redis_mode=cluster`、`redis_version >= 6.2`（XAUTOCLAIM 下限）；
4) `maxmemory` 已设置、`maxclients` 与当前连接数可读。

用法：
    python scripts/probe_redis_cluster.py --nodes 10.74.145.45:6393,10.74.145.46:6393,10.74.145.47:6393
    python scripts/probe_redis_cluster.py --url 'redis://:pwd@10.74.145.45:6393/0' --nodes ...
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from urllib.parse import unquote, urlsplit

from redis.asyncio import RedisCluster


def _parse_nodes(raw: str) -> list[tuple[str, int]]:
    nodes: list[tuple[str, int]] = []
    for item in raw.split(","):
        value = item.strip()
        if not value:
            continue
        host, _, port_text = value.rpartition(":")
        if not host or not port_text.isdigit():
            raise ValueError(f"invalid node address: {value!r}")
        nodes.append((host, int(port_text)))
    if not nodes:
        raise ValueError("at least one node address is required")
    return nodes


def _credential_values(url: str | None) -> tuple[str | None, str | None, bool]:
    if not url:
        return None, None, False
    parts = urlsplit(url)
    username = unquote(parts.username) if parts.username else None
    password = unquote(parts.password) if parts.password else None
    return username, password, parts.scheme == "rediss"


async def _tcp_ok(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
    except (OSError, TimeoutError):
        return False
    writer.close()
    await writer.wait_closed()
    del reader
    return True


def _version_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split(".")[:3])


async def _probe(nodes: list[tuple[str, int]], url: str | None) -> int:
    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' - ' + detail) if detail else ''}")
        if not ok:
            failures.append(name)

    reachable = [await _tcp_ok(host, port) for host, port in nodes]
    check(
        "tcp reachable",
        all(reachable),
        ", ".join(
            f"{host}:{port}={'ok' if ok else 'refused'}"
            for (host, port), ok in zip(nodes, reachable)
        ),
    )

    seed = next((node for node, ok in zip(nodes, reachable) if ok), nodes[0])
    username, password, use_tls = _credential_values(url)
    client = RedisCluster(
        host=seed[0],
        port=seed[1],
        decode_responses=True,
        require_full_coverage=True,
        username=username,
        password=password,
        ssl=use_tls,
    )
    try:
        check("ping", bool(await client.ping()))

        server = await client.info("server")
        mode = str(server.get("redis_mode") or "")
        version = str(server.get("redis_version") or "")
        check("redis_mode", mode == "cluster", f"mode={mode or 'unknown'}")
        check(
            "redis_version >= 6.2",
            bool(version) and _version_tuple(version)[:2] >= (6, 2),
            f"version={version or 'unknown'}",
        )

        cluster = await client.cluster_info()
        state = str(cluster.get("cluster_state") or "")
        slots = int(cluster.get("cluster_slots_assigned") or 0)
        check("cluster_state", state == "ok", f"state={state or 'unknown'}")
        check("slots covered", slots == 16384, f"{slots}/16384")

        primaries = client.get_primaries()
        check(
            "primaries discovered",
            len(primaries) >= 1,
            f"{[(node.host, node.port) for node in primaries]}",
        )

        memory = await client.info("memory")
        maxmemory = int(memory.get("maxmemory") or 0)
        used = int(memory.get("used_memory") or 0)
        check(
            "maxmemory configured",
            maxmemory > 0,
            f"maxmemory={maxmemory} used={used}",
        )

        clients = await client.info("clients")
        connected = int(clients.get("connected_clients") or 0)
        maxclients = await client.execute_command(
            "CONFIG", "GET", "maxclients", target_nodes=client.get_default_node()
        )
        limit = "unknown"
        if isinstance(maxclients, dict):
            value = maxclients.get("maxclients")
        elif isinstance(maxclients, (list, tuple)) and len(maxclients) >= 2:
            value = maxclients[1]
        else:
            value = None
        if value:
            limit = str(value)
        check("maxclients readable", limit != "unknown", f"connected={connected} maxclients={limit}")
    finally:
        await client.aclose()

    if failures:
        print(f"\n{len(failures)} check(s) failed: {', '.join(failures)}")
        return 1
    print("\nall checks passed")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Redis Cluster preflight")
    parser.add_argument(
        "--nodes", required=True, help="comma separated host:port list (all announced nodes)"
    )
    parser.add_argument("--url", default=None, help="DSN with credentials/TLS (optional)")
    args = parser.parse_args(argv)
    try:
        nodes = _parse_nodes(args.nodes)
    except ValueError as exc:
        print(f"invalid --nodes: {exc}", file=sys.stderr)
        return 2
    return asyncio.run(_probe(nodes, args.url))


if __name__ == "__main__":
    raise SystemExit(main())
