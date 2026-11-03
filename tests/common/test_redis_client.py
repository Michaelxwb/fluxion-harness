"""S-07: 公共 Redis 客户端入口的纯配置校验（不建立真实连接）。"""

from __future__ import annotations

import logging

import pytest
from redis.asyncio import Redis
from redis.asyncio.cluster import RedisCluster
from redis.exceptions import RedisClusterException

from muad_common.redis_client import (
    RedisConfigError,
    RedisTopologyMismatch,
    RedisUnavailableError,
    create_redis_client,
    parse_cluster_seeds,
    parse_seed,
    verify_redis_topology,
)


def test_standalone_returns_plain_client() -> None:
    client = create_redis_client("redis://127.0.0.1:6379/0")

    assert isinstance(client, Redis)
    assert not isinstance(client, RedisCluster)


def test_standalone_ignores_seeds_with_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        client = create_redis_client(
            "redis://127.0.0.1:6379/0", seeds=["127.0.0.1:7000"]
        )

    assert isinstance(client, Redis)
    assert "redis_cluster_seeds_ignored" in caplog.text


def test_cluster_returns_cluster_client() -> None:
    client = create_redis_client("redis://:pwd@127.0.0.1:6391/0", mode="cluster")

    assert isinstance(client, RedisCluster)


@pytest.mark.parametrize("url", [None, "", "   "])
def test_missing_url_rejected(url: str | None) -> None:
    with pytest.raises(RedisConfigError):
        create_redis_client(url)


def test_cluster_requires_db_zero() -> None:
    with pytest.raises(RedisConfigError):
        create_redis_client("redis://127.0.0.1:6391/10", mode="cluster")


def test_invalid_mode_rejected() -> None:
    with pytest.raises(RedisConfigError):
        create_redis_client("redis://127.0.0.1:6379/0", mode="sentinel")


def test_error_message_does_not_leak_password() -> None:
    with pytest.raises(RedisConfigError) as info:
        create_redis_client("redis://:s3cret@127.0.0.1:6391/10", mode="cluster")

    assert "s3cret" not in str(info.value)


@pytest.mark.parametrize(
    "seed", ["", ":7000", "host", "host:abc", "host:0", "host:70000", "::1:7000"]
)
def test_invalid_seed_rejected(seed: str) -> None:
    with pytest.raises(RedisConfigError):
        create_redis_client("redis://127.0.0.1:6391/0", mode="cluster", seeds=[seed])


def test_ipv6_seed_accepted() -> None:
    assert parse_seed("[::1]:6391") == ("::1", 6391)


def test_seeds_merge_and_dedupe_primary_first() -> None:
    seeds = parse_cluster_seeds(
        "redis://127.0.0.1:6391/0",
        ["127.0.0.1:6392", "127.0.0.1:6391", "10.0.0.1:7000"],
    )

    assert seeds == [("127.0.0.1", 6391), ("127.0.0.1", 6392), ("10.0.0.1", 7000)]


class _FakeRedis:
    def __init__(
        self,
        info: dict[str, object] | None = None,
        cluster: dict[str, object] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._info = info or {}
        self._cluster = cluster or {}
        self._error = error

    async def info(self, section: str | None = None) -> dict[str, object]:
        if self._error is not None:
            raise self._error
        return self._info

    async def cluster_info(self) -> dict[str, object]:
        if self._error is not None:
            raise self._error
        return self._cluster


async def test_topology_accepts_matching_cluster() -> None:
    client = _FakeRedis(
        {"redis_mode": "cluster"},
        {"cluster_state": "ok", "cluster_slots_assigned": 16384},
    )

    await verify_redis_topology(client, mode="cluster")  # type: ignore[arg-type]


async def test_topology_rejects_mode_mismatch() -> None:
    client = _FakeRedis({"redis_mode": "standalone"})

    with pytest.raises(RedisTopologyMismatch):
        await verify_redis_topology(client, mode="cluster")  # type: ignore[arg-type]


async def test_topology_rejects_standalone_client_against_cluster() -> None:
    client = _FakeRedis({"redis_mode": "cluster"})

    with pytest.raises(RedisTopologyMismatch):
        await verify_redis_topology(client, mode="standalone")  # type: ignore[arg-type]


async def test_topology_rejects_incomplete_slots() -> None:
    client = _FakeRedis(
        {"redis_mode": "cluster"},
        {"cluster_state": "ok", "cluster_slots_assigned": 12000},
    )

    with pytest.raises(RedisTopologyMismatch):
        await verify_redis_topology(client, mode="cluster")  # type: ignore[arg-type]


async def test_topology_normalizes_cluster_exception() -> None:
    client = _FakeRedis(error=RedisClusterException("cluster down"))

    with pytest.raises(RedisUnavailableError):
        await verify_redis_topology(client, mode="cluster")  # type: ignore[arg-type]
