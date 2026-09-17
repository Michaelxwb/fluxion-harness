from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from httpx import ASGITransport, AsyncClient
from muad_im_gateway.api.deps import get_dedupe_store, get_registry
from muad_im_gateway.channels.base import ChannelAdapterUnavailable, ChannelRegistry
from muad_im_gateway.channels.fake import FakeChannelAdapter
from muad_im_gateway.infrastructure.dedupe import (
    DedupeStore,
    DedupeStoreError,
    InMemoryDedupeStore,
    NullDedupeStore,
)
from muad_im_gateway.main import app


class _FailingAdapter(FakeChannelAdapter):
    async def send(self, route, message) -> None:  # type: ignore[no-untyped-def]
        raise ChannelAdapterUnavailable("sdk missing")


class _FailingDedupeStore:
    async def set_if_absent(self, key: str, ttl_sec: int) -> bool:
        raise DedupeStoreError("redis down")

    async def exists(self, key: str) -> bool:
        raise DedupeStoreError("redis down")

    async def mark(self, key: str, ttl_sec: int) -> None:
        raise DedupeStoreError("redis down")

    async def aclose(self) -> None:
        return None


def delivery_body(delivery_key: str | None = None) -> dict[str, Any]:
    task_id = uuid.uuid4()
    return {
        "task_id": str(task_id),
        "delivery_key": delivery_key or f"task:{task_id}:final",
        "route": {
            "channel": "WECOM",
            "bot_id": "bot-1",
            "external_user_id": "ext-1",
            "external_conversation_id": "conv-1",
        },
        "message": {"type": "text", "text": "任务已完成：成功 18，失败 2。"},
        "artifact_ids": [],
    }


@asynccontextmanager
async def api_client(
    registry: ChannelRegistry,
    dedupe: DedupeStore,
) -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[get_registry] = lambda: registry
    app.dependency_overrides[get_dedupe_store] = lambda: dedupe
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def _registry_with(adapter: FakeChannelAdapter) -> ChannelRegistry:
    registry = ChannelRegistry()
    registry.register(adapter)
    return registry


async def test_first_delivery_is_accepted_and_sent() -> None:
    adapter = FakeChannelAdapter()
    async with api_client(_registry_with(adapter), InMemoryDedupeStore()) as client:
        response = await client.post("/internal/deliveries", json=delivery_body())

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == "0"
    assert body["data"] == {"accepted": True, "duplicate": False}
    route, message = adapter.sent[0]
    assert route.bot_id == "bot-1"
    assert message.text == "任务已完成：成功 18，失败 2。"


async def test_duplicate_delivery_is_acknowledged_without_resending() -> None:
    adapter = FakeChannelAdapter()
    body = delivery_body()
    async with api_client(_registry_with(adapter), InMemoryDedupeStore()) as client:
        first = await client.post("/internal/deliveries", json=body)
        second = await client.post("/internal/deliveries", json=body)

    assert first.status_code == 200
    assert first.json()["data"] == {"accepted": True, "duplicate": False}
    assert second.status_code == 200
    assert second.json()["data"] == {"accepted": True, "duplicate": True}
    assert len(adapter.sent) == 1


async def test_null_dedupe_store_sends_every_delivery() -> None:
    adapter = FakeChannelAdapter()
    body = delivery_body()
    async with api_client(_registry_with(adapter), NullDedupeStore()) as client:
        first = await client.post("/internal/deliveries", json=body)
        second = await client.post("/internal/deliveries", json=body)

    assert first.json()["data"] == {"accepted": True, "duplicate": False}
    assert second.json()["data"] == {"accepted": True, "duplicate": False}
    assert len(adapter.sent) == 2


async def test_adapter_failure_returns_internal_error_and_keeps_retry_available() -> None:
    failing = _FailingAdapter()
    store = InMemoryDedupeStore()
    body = delivery_body()
    async with api_client(_registry_with(failing), store) as client:
        response = await client.post("/internal/deliveries", json=body)

    assert response.status_code == 500
    assert response.json()["code"] == "COMMON_INTERNAL_ERROR"
    assert await store.exists("delivery:dedupe:" + body["delivery_key"]) is False

    working = FakeChannelAdapter()
    async with api_client(_registry_with(working), store) as client:
        retry = await client.post("/internal/deliveries", json=body)

    assert retry.status_code == 200
    assert retry.json()["data"] == {"accepted": True, "duplicate": False}
    assert len(working.sent) == 1


async def test_unknown_adapter_returns_internal_error() -> None:
    async with api_client(ChannelRegistry(), InMemoryDedupeStore()) as client:
        response = await client.post("/internal/deliveries", json=delivery_body())

    assert response.status_code == 500
    assert response.json()["code"] == "COMMON_INTERNAL_ERROR"


async def test_dedupe_failure_returns_internal_error_for_retry() -> None:
    adapter = FakeChannelAdapter()
    async with api_client(_registry_with(adapter), _FailingDedupeStore()) as client:
        response = await client.post("/internal/deliveries", json=delivery_body())

    assert response.status_code == 500
    assert response.json()["code"] == "COMMON_INTERNAL_ERROR"
    assert adapter.sent == ()


async def test_invalid_delivery_key_is_rejected() -> None:
    adapter = FakeChannelAdapter()
    async with api_client(_registry_with(adapter), InMemoryDedupeStore()) as client:
        response = await client.post("/internal/deliveries", json=delivery_body("not-a-key"))

    assert response.status_code == 422
    assert response.json()["code"] == "COMMON_VALIDATION_ERROR"
