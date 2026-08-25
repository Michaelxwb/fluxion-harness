from __future__ import annotations

from collections.abc import Mapping

import pytest

from fluxion.resources import ResourceKind
from fluxion.services.redis_streams import (
    RedisConfigEventSubscriber,
    RedisStreamsClient,
    poll_config_events,
)


class FakeRedisAsyncClient:
    """模拟 redis.asyncio.Redis 的 Stream 子集，用于验证生产接线与解析逻辑。"""

    def __init__(self) -> None:
        self.streams: dict[str, dict[str, dict[str, str]]] = {}

    async def xadd(self, name: str, fields: Mapping[str, str], id: str) -> object:
        entries = self.streams.setdefault(name, {})
        entries[id] = dict(fields)
        return id

    async def xrange(
        self, name: str, min: str, max: str, count: int | None
    ) -> list[object]:
        del count
        entry = self.streams.get(name, {}).get(min)
        return [] if min != max or entry is None else [(min, entry)]

    async def xread(
        self, streams: Mapping[str, str], count: int | None, block: int | None
    ) -> list[object]:
        del block
        result: list[object] = []
        for stream_name, since_id in streams.items():
            entries = self.streams.get(stream_name, {})
            items = [(eid, fields) for eid, fields in entries.items() if eid > since_id]
            if count is not None:
                items = items[:count]
            if items:
                result.append([stream_name, items])
        return result

    async def aclose(self) -> None:
        return None


def _config_event_fields(revision: int) -> dict[str, str]:
    return {
        "event_type": "config.changed",
        "tenant_id": "tenant-a",
        "kind": "runtime_profile",
        "resource_id": "assistant",
        "version": "2",
        "revision": str(revision),
    }


@pytest.mark.asyncio
async def test_redis_streams_client_wires_xadd_and_xrange() -> None:
    fake = FakeRedisAsyncClient()
    client = RedisStreamsClient(fake)

    await client.xadd("fluxion:config-changed:tenant-a", _config_event_fields(9), id="9-0")

    rows = await client.xrange(
        "fluxion:config-changed:tenant-a",
        min="9-0",
        max="9-0",
        count=1,
    )
    assert len(rows) == 1
    await client.close()


@pytest.mark.asyncio
async def test_subscriber_parses_stream_into_config_events() -> None:
    fake = FakeRedisAsyncClient()
    await fake.xadd("fluxion:config-changed:tenant-a", _config_event_fields(9), id="9-0")
    await fake.xadd("fluxion:config-changed:tenant-a", _config_event_fields(10), id="10-0")

    subscriber = RedisConfigEventSubscriber(RedisStreamsClient(fake), block_ms=10)
    events = await subscriber.read_events("tenant-a", since_id="0")

    assert [event_id for event_id, _ in events] == ["9-0", "10-0"]
    first = events[0][1]
    assert first.kind is ResourceKind.RUNTIME_PROFILE
    assert first.resource_id == "assistant"
    assert first.version == "2"
    assert first.revision == 9


@pytest.mark.asyncio
async def test_poll_config_events_invokes_handler_and_returns_latest_id() -> None:
    fake = FakeRedisAsyncClient()
    await fake.xadd("fluxion:config-changed:tenant-a", _config_event_fields(9), id="9-0")
    await fake.xadd("fluxion:config-changed:tenant-a", _config_event_fields(10), id="10-0")

    subscriber = RedisConfigEventSubscriber(RedisStreamsClient(fake), block_ms=10)
    seen: list[int] = []

    latest = await poll_config_events(
        subscriber,
        tenant_id="tenant-a",
        handler=lambda event: seen.append(event.revision),
    )

    assert seen == [9, 10]
    assert latest == "10-0"


@pytest.mark.asyncio
async def test_subscriber_rejects_non_positive_block() -> None:
    with pytest.raises(ValueError):
        RedisConfigEventSubscriber(RedisStreamsClient(FakeRedisAsyncClient()), block_ms=0)
