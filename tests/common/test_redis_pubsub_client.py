"""pub/sub 客户端入口：cluster 下派生节点连接，永不返回集群客户端。"""

from __future__ import annotations

import pytest
from muad_common.redis_client import (
    create_pubsub_client,
    create_pubsub_client_from_cluster,
)
from redis.asyncio import Redis
from redis.asyncio.cluster import RedisCluster
from redis.exceptions import ConnectionError as RedisConnectionError


async def test_standalone_pubsub_returns_plain_client() -> None:
    client = await create_pubsub_client("redis://127.0.0.1:6379/0")

    assert isinstance(client, Redis)
    assert not isinstance(client, RedisCluster)


async def test_cluster_pubsub_unreachable_raises_connection_error() -> None:
    with pytest.raises(RedisConnectionError):
        await create_pubsub_client(
            "redis://127.0.0.1:1/0", mode="cluster", seeds=["127.0.0.1:1"]
        )


class _Node:
    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port


class _FakeCluster:
    def __init__(self, primaries: list[_Node]) -> None:
        self._primaries = primaries

    def get_primaries(self) -> list[_Node]:
        return self._primaries

    def get_connection_kwargs(self) -> dict[str, object]:
        return {
            "username": None,
            "password": None,
            "decode_responses": True,
            "ssl": False,
        }


async def test_pubsub_from_cluster_derives_primary_node() -> None:
    client = create_pubsub_client_from_cluster(
        _FakeCluster([_Node("127.0.0.1", 6391)])  # type: ignore[arg-type]
    )

    assert isinstance(client, Redis)
    kwargs = client.connection_pool.connection_kwargs
    assert kwargs["host"] == "127.0.0.1"
    assert kwargs["port"] == 6391
    await client.aclose()


async def test_pubsub_from_cluster_without_primaries_raises() -> None:
    with pytest.raises(RedisConnectionError):
        create_pubsub_client_from_cluster(_FakeCluster([]))  # type: ignore[arg-type]
