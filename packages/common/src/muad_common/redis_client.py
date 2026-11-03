"""公共 Redis 客户端入口（design API-01 / API-04 / API-05）。

两种客户端职责不同，不能混用：

- **keyed 客户端**（`create_redis_client`）：Stream/缓存/租约等有 key 的命令。cluster 模式返回
  `RedisCluster`，按槽路由并跟随 MOVED/ASK；standalone 模式保持今天的 `Redis.from_url` 行为。
- **pub/sub 客户端**（`create_pubsub_client` / `create_pubsub_client_from_cluster`）：`SUBSCRIBE`/
  `PUBLISH` 没有 key、不参与槽路由；redis-py 6.x 的异步 `RedisCluster` 没有 pub/sub 实现，
  所以这里永远返回普通 `Redis`（cluster 下连到拓扑发现出来的某个节点）。

`RedisClusterException` 不继承 `RedisError`，统一归一为 `RedisUnavailableError`，保证既有
`except RedisError` 的降级路径继续生效。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Literal, TypeAlias, cast
from urllib.parse import SplitResult, unquote, urlsplit

from redis.asyncio import Redis
from redis.asyncio.cluster import ClusterNode, RedisCluster
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialWithJitterBackoff
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisClusterException, RedisError

logger = logging.getLogger(__name__)

RedisMode: TypeAlias = Literal["standalone", "cluster"]
RedisClient: TypeAlias = Redis | RedisCluster


class RedisConfigError(ValueError):
    """Redis 配置非法（模式/DSN/seed/db）：启动失败关闭，不进入半可用状态。"""


class RedisUnavailableError(RedisError):
    """集群客户端异常的统一类型，保证既有 `except RedisError` 能接住。"""


class RedisTopologyMismatch(RedisUnavailableError):
    """实际拓扑与 `REDIS_MODE` 不一致。"""


def _parse_mode(mode: str) -> RedisMode:
    value = (mode or "").strip().lower()
    if value == "standalone":
        return "standalone"
    if value == "cluster":
        return "cluster"
    raise RedisConfigError(
        f"invalid REDIS_MODE={mode!r}; expected standalone or cluster"
    )


def parse_seed(value: str) -> tuple[str, int]:
    """解析一个 `host:port` seed；IPv6 必须写成 `[addr]:port`。"""
    raw = (value or "").strip()
    if not raw:
        raise RedisConfigError("empty redis cluster seed")
    if raw.startswith("["):
        host, separator, port_text = raw[1:].partition("]:")
        if not separator:
            raise RedisConfigError(f"invalid redis cluster seed: {raw!r}")
    else:
        host, separator, port_text = raw.rpartition(":")
        if not separator or not host or ":" in host:
            raise RedisConfigError(f"invalid redis cluster seed: {raw!r}")
    host = host.strip()
    if not host or not port_text.isdigit():
        raise RedisConfigError(f"invalid redis cluster seed: {raw!r}")
    port = int(port_text)
    if not 1 <= port <= 65535:
        raise RedisConfigError(f"invalid redis cluster seed: {raw!r}")
    return host, port


def _url_parts(url: str) -> tuple[SplitResult, int]:
    """校验 DSN 并返回 `urlsplit` 结果与默认端口。"""
    parts = urlsplit(url)
    if parts.scheme not in ("redis", "rediss"):
        raise RedisConfigError("REDIS_URL must use redis:// or rediss://")
    if not parts.hostname:
        raise RedisConfigError("REDIS_URL must include a host")
    try:
        port = parts.port or 6379
    except ValueError:
        raise RedisConfigError("REDIS_URL has an invalid port") from None
    return parts, port


def _url_db(parts: SplitResult) -> int:
    path = (parts.path or "").strip("/")
    if not path:
        return 0
    if not path.isdigit():
        raise RedisConfigError("REDIS_URL db must be numeric")
    return int(path)


def _credential_values(parts: SplitResult) -> tuple[str | None, str | None, bool]:
    username = unquote(parts.username) if parts.username else None
    password = unquote(parts.password) if parts.password else None
    return username, password, parts.scheme == "rediss"


def parse_cluster_seeds(
    url: str, seeds: Sequence[str] | None = None
) -> list[tuple[str, int]]:
    """主 seed 在前，追加 `seeds` 并去重保序（同一节点只连一次）。"""
    parts, port = _url_parts(url)
    host = getattr(parts, "hostname", "") or ""
    merged: list[tuple[str, int]] = [(host, port)]
    for raw in seeds or ():
        parsed = parse_seed(raw)
        if parsed not in merged:
            merged.append(parsed)
    return merged


def create_redis_client(
    url: str | None,
    *,
    mode: str = "standalone",
    seeds: Sequence[str] | None = None,
    decode_responses: bool = True,
    max_connections: int | None = None,
) -> RedisClient:
    """keyed 命令客户端：standalone 返回 `Redis`，cluster 返回 `RedisCluster`。"""
    target = (url or "").strip()
    if not target:
        raise RedisConfigError("REDIS_URL is required")
    parsed_mode = _parse_mode(mode)

    if parsed_mode == "standalone":
        if seeds:
            logger.warning(
                "redis_cluster_seeds_ignored mode=standalone seeds=%d", len(seeds)
            )
        if max_connections is None:
            return cast(Redis, Redis.from_url(target, decode_responses=decode_responses))
        return cast(
            Redis,
            Redis.from_url(
                target,
                decode_responses=decode_responses,
                max_connections=max_connections,
            ),
        )

    parts, _port = _url_parts(target)
    if _url_db(parts) != 0:
        raise RedisConfigError("cluster mode requires DB 0 in REDIS_URL")
    nodes = [
        ClusterNode(host, port) for host, port in parse_cluster_seeds(target, seeds)
    ]
    username, password, ssl = _credential_values(parts)
    retry = Retry(ExponentialWithJitterBackoff(base=0.05, cap=1.0), retries=3)
    if max_connections is None:
        return RedisCluster(
            startup_nodes=nodes,
            require_full_coverage=True,
            retry=retry,
            decode_responses=decode_responses,
            username=username,
            password=password,
            ssl=ssl,
        )
    return RedisCluster(
        startup_nodes=nodes,
        require_full_coverage=True,
        retry=retry,
        decode_responses=decode_responses,
        username=username,
        password=password,
        ssl=ssl,
        max_connections=max_connections,
    )


async def create_pubsub_client(
    url: str | None,
    *,
    mode: str = "standalone",
    seeds: Sequence[str] | None = None,
    decode_responses: bool = True,
    max_connections: int = 10,
) -> Redis:
    """pub/sub 客户端：永远返回普通 `Redis`，cluster 下按 seed 顺序选首发可达节点。"""
    target = (url or "").strip()
    if not target:
        raise RedisConfigError("REDIS_URL is required")
    parsed_mode = _parse_mode(mode)

    if parsed_mode == "standalone":
        return cast(
            Redis,
            Redis.from_url(
                target,
                decode_responses=decode_responses,
                max_connections=max_connections,
            ),
        )

    parts, _port = _url_parts(target)
    username, password, ssl = _credential_values(parts)

    candidates = parse_cluster_seeds(target, seeds)
    last_error: Exception | None = None
    for host, port in candidates:
        client = Redis(
            host=host,
            port=port,
            decode_responses=decode_responses,
            max_connections=max_connections,
            socket_connect_timeout=2.0,
            username=username,
            password=password,
            ssl=ssl,
        )
        try:
            await client.ping()
        except (RedisError, OSError) as exc:
            last_error = exc
            await client.aclose()
            continue
        return client
    raise RedisConnectionError(
        f"no redis cluster node reachable (tried {len(candidates)})"
    ) from last_error


def create_pubsub_client_from_cluster(
    cluster: RedisCluster,
    *,
    decode_responses: bool = True,
    max_connections: int = 10,
) -> Redis:
    """从已构造的集群客户端派生 pub/sub 连接（复用其凭据/TLS 与拓扑）。"""
    primaries = cluster.get_primaries()
    if not primaries:
        raise RedisConnectionError("redis cluster has no reachable primary")
    node = primaries[0]
    options = cluster.get_connection_kwargs()
    username = options.get("username")
    password = options.get("password")
    return Redis(
        host=node.host,
        port=int(node.port or 6379),
        decode_responses=decode_responses,
        max_connections=max_connections,
        username=username if isinstance(username, str) else None,
        password=password if isinstance(password, str) else None,
        ssl=bool(options.get("ssl")),
    )


async def verify_redis_topology(client: RedisClient, *, mode: str) -> None:
    """启动自校验：实际拓扑必须与 `REDIS_MODE` 一致，否则启动失败关闭。"""
    parsed_mode = _parse_mode(mode)
    try:
        info = await client.info("server")
    except RedisClusterException as exc:
        raise RedisUnavailableError(
            "redis cluster unavailable during topology check"
        ) from exc

    actual = str(info.get("redis_mode") or "").lower()
    if parsed_mode == "standalone":
        if actual == "cluster":
            raise RedisTopologyMismatch(
                "REDIS_MODE=standalone but server reports cluster"
            )
        return

    if actual != "cluster":
        raise RedisTopologyMismatch(
            f"REDIS_MODE=cluster but server reports {actual or 'unknown'}"
        )
    try:
        cluster_info = await client.cluster_info()  # type: ignore[union-attr]
    except RedisClusterException as exc:
        raise RedisUnavailableError(
            "redis cluster unavailable during topology check"
        ) from exc
    state = str(cluster_info.get("cluster_state") or "")
    assigned = int(cluster_info.get("cluster_slots_assigned") or 0)
    if state != "ok":
        raise RedisTopologyMismatch(f"redis cluster state is {state or 'unknown'}")
    if assigned != 16384:
        raise RedisTopologyMismatch(f"redis cluster covers {assigned}/16384 slots")
