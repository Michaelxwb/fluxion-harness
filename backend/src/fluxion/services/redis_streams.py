from __future__ import annotations

import importlib
from collections.abc import Callable, Mapping, Sequence
from typing import Protocol

from fluxion.resources import ResourceKind
from fluxion.runtime.hot_reload import ConfigChangeEvent

DEFAULT_STREAM_PREFIX = "fluxion:config-changed"


class RedisAsyncClient(Protocol):
    """redis.asyncio.Redis 需要的最小接口（鸭子类型，避免强依赖 redis 包类型）。"""

    async def xadd(self, name: str, fields: Mapping[str, str], id: str) -> object: ...

    async def xrange(
        self, name: str, min: str, max: str, count: int | None
    ) -> Sequence[object]: ...

    async def xread(
        self, streams: Mapping[str, str], count: int | None, block: int | None
    ) -> object: ...

    async def aclose(self) -> None: ...


class RedisStreamsClient:
    """生产配置通知的 Redis Streams 客户端，包装 redis.asyncio.Redis。

    dev 模式用 SQLite revision polling；生产用 Redis Streams + Transactional Outbox，
    本客户端是 RedisStreamClient Protocol 的真实实现，供
    RedisStreamsConfigEventPublisher 与 RedisConfigEventSubscriber 共用。
    """

    def __init__(self, client: RedisAsyncClient) -> None:
        self._client = client

    async def xadd(self, stream: str, fields: Mapping[str, str], *, id: str) -> object:
        return await self._client.xadd(stream, fields, id=id)

    async def xrange(
        self,
        stream: str,
        *,
        min: str,
        max: str,
        count: int,
    ) -> Sequence[object]:
        return await self._client.xrange(stream, min, max, count)

    async def xread(
        self,
        streams: Mapping[str, str],
        *,
        count: int,
        block: int,
    ) -> object:
        return await self._client.xread(streams, count=count, block=block)

    async def close(self) -> None:
        await self._client.aclose()


def build_redis_streams_client(dsn: str, **kwargs: object) -> RedisStreamsClient:
    """从 Redis DSN 创建生产客户端；redis 包缺失时 fail-fast 并给出明确指引。"""
    try:
        aioredis = importlib.import_module("redis.asyncio")
    except ImportError as exc:
        raise RuntimeError("生产 Redis 配置通知需要安装 redis 包（pip install redis）") from exc
    client = aioredis.from_url(dsn, decode_responses=True, **kwargs)
    return RedisStreamsClient(client)


class RedisConfigEventSubscriber:
    """订阅 fluxion:config-changed:<tenant> 流，把 Stream 消息解析为 ConfigChangeEvent。

    消费循环有界：单次 read 使用 block 超时，避免无限等待；调用方（Runtime Pod）
    收到事件后回调 handle_config_changed 完成本地缓存失效。
    """

    def __init__(
        self,
        client: RedisStreamsClient,
        *,
        stream_prefix: str = DEFAULT_STREAM_PREFIX,
        block_ms: int = 5000,
    ) -> None:
        if block_ms <= 0:
            raise ValueError("block_ms must be positive")
        self._client = client
        self._stream_prefix = stream_prefix
        self._block_ms = block_ms

    async def read_events(
        self,
        tenant_id: str,
        *,
        since_id: str = "0",
        count: int = 100,
    ) -> list[tuple[str, ConfigChangeEvent]]:
        if count < 1:
            raise ValueError("count must be positive")
        stream = f"{self._stream_prefix}:{tenant_id}"
        raw = await self._client.xread(
            {stream: since_id},
            count=count,
            block=self._block_ms,
        )
        return _parse_stream(raw, tenant_id)


async def poll_config_events(
    subscriber: RedisConfigEventSubscriber,
    *,
    tenant_id: str,
    handler: Callable[[ConfigChangeEvent], object],
    since_id: str = "0",
    count: int = 100,
) -> str:
    """一次性读取并回调，返回最新 event_id 供下一轮作为 since_id 续读。"""
    events = await subscriber.read_events(tenant_id, since_id=since_id, count=count)
    latest = since_id
    for event_id, event in events:
        handler(event)
        latest = event_id
    return latest


def _parse_stream(raw: object, tenant_id: str) -> list[tuple[str, ConfigChangeEvent]]:
    if not isinstance(raw, list):
        return []
    parsed: list[tuple[str, ConfigChangeEvent]] = []
    for stream_entry in raw:
        entries = stream_entry[1] if isinstance(stream_entry, (list, tuple)) else None
        if not entries:
            continue
        for entry in entries:
            event_id, fields = entry[0], entry[1]
            event = _event_from_fields(fields, tenant_id)
            if event is not None:
                parsed.append((str(event_id), event))
    return parsed


def _event_from_fields(fields: object, tenant_id: str) -> ConfigChangeEvent | None:
    if not isinstance(fields, Mapping):
        return None
    kind_value = fields.get("kind")
    version = fields.get("version")
    revision = fields.get("revision")
    resource_id = fields.get("resource_id")
    if not all(isinstance(value, str) for value in (kind_value, version, revision, resource_id)):
        return None
    return ConfigChangeEvent(
        tenant_id=tenant_id,
        kind=ResourceKind(str(kind_value)),
        resource_id=str(resource_id),
        version=str(version),
        revision=int(str(revision)),
    )
