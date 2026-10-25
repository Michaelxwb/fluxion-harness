"""投递 facade 的 Stream 分支（API-04/05/06）：入队 + 有界等待，不把占位当成功。

本进程没有通道适配器（或适配器没有进程内直发能力）时，`/internal/deliveries` 必须
`校验 → XADD im.outbound → 有界等待结果`；超时/失败回**可重试错误**并释放占位。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fakes import FakeConsoleClient
from httpx import ASGITransport, AsyncClient
from muad_contracts import AttachmentRef
from muad_im_gateway.api.deps import (
    get_artifact_resolver,
    get_console_client,
    get_dedupe_store,
    get_delivery_stream_wait_sec,
    get_delivery_streams,
    get_gateway_tenant,
    get_registry,
)
from muad_im_gateway.channels.base import ChannelRegistry
from muad_im_gateway.channels.webchat import WebchatChannelAdapter
from muad_im_gateway.channels.webhook import WebhookChannelAdapter, WebhookChannelConfig
from muad_im_gateway.infrastructure.dedupe import InMemoryDedupeStore
from muad_im_gateway.infrastructure.streams import DeliveryStreamsError
from muad_im_gateway.main import app

from tests.internal_service import TOKEN as INTERNAL_TOKEN
from tests.internal_service import internal_service_token  # noqa: F401  (fixture 注册)

GATEWAY_TENANT = "tenant-1"
SERVICE_HEADERS = {"X-Internal-Service": INTERNAL_TOKEN}
DELIVERIES_URL = "/internal/deliveries"


class _Streams:
    def __init__(
        self,
        *,
        status: str = "DELIVERED",
        code: str = "",
        fallback_url: str | None = None,
        route_key: str = "bot-1:ext-1",
        timeout: bool = False,
        error: Exception | None = None,
    ) -> None:
        self.status = status
        self.code = code
        self.fallback_url = fallback_url
        self.route_key = route_key
        self.timeout = timeout
        self.error = error
        self.commands: list[Any] = []

    async def enqueue(self, command: Any) -> str:
        if self.error is not None:
            raise self.error
        self.commands.append(command)
        return "1-1"

    async def await_result(self, command_id: Any, *, timeout_sec: float) -> dict[str, Any] | None:
        if self.timeout:
            return None
        return {
            "status": self.status,
            "code": self.code,
            "fallback_url": self.fallback_url,
            "route_key": self.route_key,
        }

    async def aclose(self) -> None:
        return None


class _Resolver:
    def __init__(self, ref: AttachmentRef | None = None) -> None:
        self.ref = ref

    async def resolve(self, artifact_id: uuid.UUID, *, tenant_id: str) -> AttachmentRef:
        assert self.ref is not None
        return self.ref

    async def aclose(self) -> None:
        return None


class _Sink:
    def __init__(self) -> None:
        self.delivered: list[Any] = []

    async def deliver(self, route: Any, message: Any) -> None:
        self.delivered.append((route, message))

    async def deliver_stream(self, route: Any, chunks: Any) -> None:
        self.delivered.append((route, "".join([chunk async for chunk in chunks])))


def _body(*, text: str = "任务已完成") -> dict[str, Any]:
    task_id = uuid.uuid4()
    return {
        "tenant_id": GATEWAY_TENANT,
        "task_id": str(task_id),
        "delivery_key": f"task:{task_id}:final",
        "route": {
            "channel": "WECOM",
            "bot_id": "bot-1",
            "external_user_id": "ext-1",
            "external_conversation_id": "conv-1",
        },
        "message": {"type": "text", "text": text},
        "artifact_ids": [],
    }


def _artifact_body(ref: AttachmentRef) -> dict[str, Any]:
    return {
        "tenant_id": GATEWAY_TENANT,
        "delivery_key": f"run:{uuid.uuid4()}:{ref.artifact_id}",
        "route": {"channel": "WECOM", "bot_id": "bot-1", "external_user_id": "ext-1"},
        "message": {"type": "artifact", "text": "", "artifact": ref.model_dump(mode="json")},
        "artifact_ids": [str(ref.artifact_id)],
    }


def _artifact() -> AttachmentRef:
    return AttachmentRef(
        artifact_id=uuid.uuid4(),
        storage_key="artifacts/out.bin",
        kind="DOCUMENT",
        media_type="application/octet-stream",
        size=4,
        filename="out.bin",
        checksum="sha256:" + "b" * 64,
    )


@asynccontextmanager
async def _client(
    registry: ChannelRegistry,
    streams: _Streams,
    *,
    dedupe: InMemoryDedupeStore | None = None,
    resolver: _Resolver | None = None,
    console: FakeConsoleClient | None = None,
) -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[get_registry] = lambda: registry
    app.dependency_overrides[get_dedupe_store] = lambda: dedupe or InMemoryDedupeStore()
    app.dependency_overrides[get_console_client] = lambda: console or FakeConsoleClient()
    app.dependency_overrides[get_gateway_tenant] = lambda: GATEWAY_TENANT
    app.dependency_overrides[get_artifact_resolver] = lambda: resolver or _Resolver()
    app.dependency_overrides[get_delivery_streams] = lambda: streams
    app.dependency_overrides[get_delivery_stream_wait_sec] = lambda: 0.05
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(
            transport=transport, base_url="http://test", headers=SERVICE_HEADERS
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


async def test_channel_without_local_adapter_enqueues_one_command() -> None:
    streams = _Streams()
    async with _client(ChannelRegistry(), streams) as client:
        response = await client.post(DELIVERIES_URL, json=_body())

    assert response.status_code == 200, response.text
    assert response.json()["data"] == {
        "accepted": True,
        "duplicate": False,
        "delivered": True,
        "deduplicated": False,
    }
    assert len(streams.commands) == 1
    command = streams.commands[0]
    assert command.channel == "WECOM"
    assert command.tenant_id == GATEWAY_TENANT
    assert command.bot_id == "bot-1"
    assert command.kind.value == "TEXT"
    assert command.text == "任务已完成"
    assert command.created_at is not None


async def test_non_local_adapter_uses_the_stream() -> None:
    """有适配器但不具备进程内直发能力（webhook）⇒ 同样交给 im.outbound。"""
    streams = _Streams()
    registry = ChannelRegistry()
    registry.register(WebhookChannelAdapter(WebhookChannelConfig(channel="WECOM", secret="s")))
    async with _client(registry, streams) as client:
        response = await client.post(DELIVERIES_URL, json=_body())

    assert response.status_code == 200, response.text
    assert len(streams.commands) == 1


async def test_stream_timeout_returns_retryable_error_and_releases_placeholder() -> None:
    dedupe = InMemoryDedupeStore()
    streams = _Streams(timeout=True)
    body = _body()
    async with _client(ChannelRegistry(), streams, dedupe=dedupe) as client:
        response = await client.post(DELIVERIES_URL, json=body)

    assert response.status_code == 500
    assert response.json()["code"] == "COMMON_INTERNAL_ERROR"
    assert await dedupe.exists(f"delivery:dedupe:{body['delivery_key']}") is False


async def test_stream_failed_result_is_not_reported_as_delivered() -> None:
    dedupe = InMemoryDedupeStore()
    streams = _Streams(status="FAILED", code="COMMAND_EXECUTION_FAILED")
    body = _body()
    async with _client(ChannelRegistry(), streams, dedupe=dedupe) as client:
        response = await client.post(DELIVERIES_URL, json=body)

    assert response.status_code == 500
    assert response.json()["code"] == "COMMON_INTERNAL_ERROR"
    assert await dedupe.exists(f"delivery:dedupe:{body['delivery_key']}") is False


async def test_stream_enqueue_failure_is_explicit() -> None:
    streams = _Streams(error=DeliveryStreamsError("redis down"))
    async with _client(ChannelRegistry(), streams) as client:
        response = await client.post(DELIVERIES_URL, json=_body())

    assert response.status_code == 500
    assert response.json()["code"] == "COMMON_INTERNAL_ERROR"


async def test_degraded_artifact_result_surfaces_outcome_and_adapter_route_key() -> None:
    ref = _artifact()
    streams = _Streams(
        status="DEGRADED",
        code="ARTIFACT_DEGRADED",
        fallback_url="https://example.invalid/fetch",
        route_key="bot-1:ext-1",
    )
    console = FakeConsoleClient()
    async with _client(
        ChannelRegistry(), streams, resolver=_Resolver(ref), console=console
    ) as client:
        response = await client.post(DELIVERIES_URL, json=_artifact_body(ref))

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["outcome"] == "DEGRADED"
    assert data["fallback_url"] == "https://example.invalid/fetch"
    assert len(streams.commands) == 1
    assert streams.commands[0].kind.value == "ARTIFACT"
    assert console.delivery_audit_calls, "产物交付必须写审计"
    audit, tenant_id = console.delivery_audit_calls[-1]
    assert tenant_id == GATEWAY_TENANT
    assert audit.outcome == "DEGRADED"
    assert audit.route_key == "bot-1:ext-1"


async def test_webchat_local_adapter_does_not_enter_the_stream() -> None:
    """WEBCHAT 的持久入箱/SSE 面是进程内能力：走既有路径，不进 Stream。"""
    sink = _Sink()
    registry = ChannelRegistry()
    registry.register(WebchatChannelAdapter(sink=sink))
    streams = _Streams()
    body = _body()
    body["route"]["channel"] = "WEBCHAT"
    async with _client(registry, streams) as client:
        await registry.get("WEBCHAT").start()
        response = await client.post(DELIVERIES_URL, json=body)

    assert response.status_code == 200, response.text
    assert streams.commands == []
    assert len(sink.delivered) == 1


@pytest.mark.parametrize("status", ["FAILED", "EXPIRED", "FENCED"])
async def test_non_success_connector_statuses_are_not_delivered(status: str) -> None:
    streams = _Streams(status=status, code="X")
    async with _client(ChannelRegistry(), streams) as client:
        response = await client.post(DELIVERIES_URL, json=_body())

    assert response.status_code == 500
    assert response.json()["code"] == "COMMON_INTERNAL_ERROR"
