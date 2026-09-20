from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fakes import FakeConsoleClient
from httpx import ASGITransport, AsyncClient
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import BotSnapshotResponse
from muad_im_gateway.api.health import dedupe_mode
from muad_im_gateway.application.bot_snapshot import BotSnapshotCache
from muad_im_gateway.channels.base import ChannelRegistry
from muad_im_gateway.channels.fake import FakeChannelAdapter
from muad_im_gateway.infrastructure.dedupe import (
    InMemoryDedupeStore,
    NullDedupeStore,
    RedisDedupeStore,
)
from muad_im_gateway.main import app


class _StubRedis:
    async def set(self, key: str, value: str, *, nx: bool, ex: int) -> bool:
        return True

    async def aclose(self) -> None:
        return None


class _UnhealthyAdapter(FakeChannelAdapter):
    def healthy(self) -> bool:
        return False


@asynccontextmanager
async def api_client(
    registry: ChannelRegistry,
    dedupe: Any,
    snapshot: BotSnapshotCache,
) -> AsyncIterator[AsyncClient]:
    # 探针由 api-kit 原语注册（无 DI），依赖来自 app.state —— 与真实 lifespan 的装配方式一致
    app.state.registry = registry
    app.state.dedupe_store = dedupe
    app.state.bot_snapshot = snapshot
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        for name in ("registry", "dedupe_store", "bot_snapshot"):
            setattr(app.state, name, None)


async def _registry(*, started: bool) -> ChannelRegistry:
    registry = ChannelRegistry()
    registry.register(FakeChannelAdapter())
    if started:
        await registry.start_all()
    return registry


async def test_healthz_reports_ok() -> None:
    snapshot = BotSnapshotCache(FakeConsoleClient(), tenant_id="t1")
    async with api_client(await _registry(started=False), InMemoryDedupeStore(), snapshot) as client:
        response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["data"] == {"status": "ok"}


async def test_readyz_ready_with_started_adapter_and_console() -> None:
    console = FakeConsoleClient()
    console.bot_snapshot = BotSnapshotResponse(revision="rev-1")
    snapshot = BotSnapshotCache(console, tenant_id="t1")
    await snapshot.refresh()
    async with api_client(await _registry(started=True), InMemoryDedupeStore(), snapshot) as client:
        response = await client.get("/readyz")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data == {
        "status": "ready",
        "adapters": {"WECOM": True},
        "dedupe": "memory",
        "bots_revision": "rev-1",
    }


async def test_readyz_503_without_started_adapter() -> None:
    console = FakeConsoleClient()
    snapshot = BotSnapshotCache(console, tenant_id="t1")
    await snapshot.refresh()
    async with api_client(await _registry(started=False), InMemoryDedupeStore(), snapshot) as client:
        response = await client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["data"]["adapters"] == {"WECOM": False}


async def test_readyz_503_when_adapter_unhealthy() -> None:
    console = FakeConsoleClient()
    console.bot_snapshot = BotSnapshotResponse(revision="rev-1")
    snapshot = BotSnapshotCache(console, tenant_id="t1")
    await snapshot.refresh()
    registry = ChannelRegistry()
    registry.register(_UnhealthyAdapter())
    await registry.start_all()
    async with api_client(registry, InMemoryDedupeStore(), snapshot) as client:
        response = await client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["data"]["adapters"] == {"WECOM": True}


async def test_readyz_503_when_console_unreachable_and_cache_empty() -> None:
    console = FakeConsoleClient()
    console.bots_error = AppError(ErrorCode.COMMON_INTERNAL_ERROR)
    snapshot = BotSnapshotCache(console, tenant_id="t1")
    await snapshot.refresh()
    async with api_client(await _registry(started=True), InMemoryDedupeStore(), snapshot) as client:
        response = await client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["data"]["bots_revision"] is None


async def test_readyz_stays_ready_with_stale_cache_when_console_unreachable() -> None:
    console = FakeConsoleClient()
    console.bot_snapshot = BotSnapshotResponse(revision="rev-2")
    snapshot = BotSnapshotCache(console, tenant_id="t1")
    await snapshot.refresh()
    console.bots_error = AppError(ErrorCode.COMMON_INTERNAL_ERROR)
    await snapshot.refresh()

    async with api_client(await _registry(started=True), InMemoryDedupeStore(), snapshot) as client:
        response = await client.get("/readyz")

    assert response.status_code == 200
    assert response.json()["data"]["bots_revision"] == "rev-2"


def test_dedupe_mode_maps_concrete_stores() -> None:
    assert dedupe_mode(NullDedupeStore()) == "disabled"
    assert dedupe_mode(InMemoryDedupeStore()) == "memory"
    assert dedupe_mode(RedisDedupeStore(_StubRedis())) == "redis"  # type: ignore[arg-type]


def test_dedupe_mode_maps_unknown_store_to_disabled() -> None:
    class _CustomStore:
        async def set_if_absent(self, key: str, ttl_sec: int) -> bool:
            return False

        async def aclose(self) -> None:
            return None

    assert dedupe_mode(_CustomStore()) == "disabled"
