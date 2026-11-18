"""TASK-003：Gateway 多附件消息交付协议（DEGRADED 不折叠为 DELIVERED）。

覆盖两条真实路径：

- 本进程直发（`LocalDeliverySurface`）：正文 + 多附件逐条交给适配器；
- `im.outbound` Stream（Connector）：命令携带 `attachments[]`，Stream 回 `DEGRADED`
  时响应保留 `outcome=DEGRADED` + `fallback_url`。

附件归属仍走真实解析端口（测试替身给出权威引用）；存储键不一致必须被拒绝。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fakes import FakeConsoleClient
from httpx import ASGITransport, AsyncClient
from muad_contracts import AttachmentRef, ChannelResolveResponse
from muad_im_gateway.api.deps import (
    get_artifact_resolver,
    get_console_client,
    get_dedupe_store,
    get_delivery_stream_wait_sec,
    get_delivery_streams,
    get_gateway_tenant,
    get_registry,
)
from muad_im_gateway.channels.base import (
    ArtifactDeliveryError,
    ArtifactDeliveryOutcome,
    ChannelRegistry,
)
from muad_im_gateway.channels.fake import FakeChannelAdapter
from muad_im_gateway.infrastructure.artifact_resolver import ArtifactGoneError
from muad_im_gateway.infrastructure.dedupe import InMemoryDedupeStore
from muad_im_gateway.main import app

from tests.internal_service import TOKEN as INTERNAL_TOKEN
from tests.internal_service import internal_service_token  # noqa: F401  (fixture 注册)

DELIVERIES_URL = "/internal/deliveries"
GATEWAY_TENANT = "tenant-1"
ROUTE = {
    "channel": "WECOM",
    "bot_id": "bot-1",
    "external_user_id": "ext-1",
    "external_conversation_id": "thread-1",
}
SERVICE_HEADERS = {"X-Internal-Service": INTERNAL_TOKEN}


def _authorized_resolve() -> ChannelResolveResponse:
    return ChannelResolveResponse(
        bound=True,
        entry_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        platform_user_id=uuid.uuid4(),
        authorized=True,
    )


def _attachment(name: str) -> AttachmentRef:
    return AttachmentRef(
        storage_key=f"artifacts/{uuid.uuid4().hex}.txt",
        kind="DOCUMENT",
        media_type="text/plain",
        size=10,
        filename=name,
        checksum="sha256:" + uuid.uuid4().hex,
        artifact_id=uuid.uuid4(),
    )


class _Resolver:
    def __init__(self, refs: list[AttachmentRef]) -> None:
        self._refs = {item.artifact_id: item for item in refs}
        self.calls: list[uuid.UUID] = []

    async def resolve(self, artifact_id: uuid.UUID, *, tenant_id: str) -> AttachmentRef:
        self.calls.append(artifact_id)
        ref = self._refs.get(artifact_id)
        if ref is None:
            raise ArtifactGoneError(str(artifact_id))
        return ref

    async def aclose(self) -> None:
        return None


class _ArtifactAdapter(FakeChannelAdapter):
    """支持多附件的本地适配器：按脚本返回结局并记录每条引用。"""

    def __init__(self, outcomes: list[ArtifactDeliveryOutcome] | None = None) -> None:
        super().__init__()
        self._outcomes = list(
            outcomes or [ArtifactDeliveryOutcome(outcome="DELIVERED")]
        )
        self.received: list[AttachmentRef] = []

    def route_key(self, route: Any) -> str:
        return f"{route.bot_id}:{route.external_user_id}"

    async def deliver_artifact(
        self, route: Any, artifact: AttachmentRef, *, tenant_id: str, run_id: str | None = None
    ) -> ArtifactDeliveryOutcome:
        self.received.append(artifact)
        return self._outcomes.pop(0) if self._outcomes else ArtifactDeliveryOutcome(
            outcome="DELIVERED"
        )


class _FailingArtifactAdapter(_ArtifactAdapter):
    async def deliver_artifact(
        self, route: Any, artifact: AttachmentRef, *, tenant_id: str, run_id: str | None = None
    ) -> ArtifactDeliveryOutcome:
        raise ArtifactDeliveryError("ARTIFACT_DELIVERY_FAILED")


class _Streams:
    def __init__(
        self,
        *,
        status: str = "DEGRADED",
        fallback_url: str | None = "https://example.test/f/1",
    ) -> None:
        self.status = status
        self.fallback_url = fallback_url
        self.commands: list[Any] = []

    async def enqueue(self, command: Any) -> str:
        self.commands.append(command)
        return "0-1"

    async def await_result(
        self, command_id: Any, *, timeout_sec: float
    ) -> dict[str, Any] | None:
        return {
            "command_id": str(command_id),
            "status": self.status,
            "code": "ARTIFACT_DEGRADED" if self.status == "DEGRADED" else "",
            "fallback_url": self.fallback_url,
            "route_key": "bot:ext",
        }

    async def aclose(self) -> None:
        return None


@asynccontextmanager
async def _client(
    registry: ChannelRegistry,
    resolver: _Resolver,
    streams: _Streams | None = None,
) -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[get_registry] = lambda: registry
    app.dependency_overrides[get_dedupe_store] = lambda: InMemoryDedupeStore()
    console = FakeConsoleClient()
    console.resolve_response = _authorized_resolve()
    app.dependency_overrides[get_console_client] = lambda: console
    app.dependency_overrides[get_gateway_tenant] = lambda: GATEWAY_TENANT
    app.dependency_overrides[get_artifact_resolver] = lambda: resolver
    app.dependency_overrides[get_delivery_streams] = lambda: streams or _Streams()
    app.dependency_overrides[get_delivery_stream_wait_sec] = lambda: 0.1
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers=SERVICE_HEADERS,
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def _body(attachments: list[AttachmentRef], *, text: str = "结果正文") -> dict[str, Any]:
    run_id = uuid.uuid4()
    return {
        "tenant_id": GATEWAY_TENANT,
        "delivery_key": f"run:{run_id}:message:{uuid.uuid4()}",
        "route": ROUTE,
        "message": {
            "type": "message",
            "text": text,
            "attachments": [
                item.model_dump(mode="json") for item in attachments
            ],
        },
        "artifact_ids": [
            str(item.artifact_id) for item in attachments if item.artifact_id
        ],
        "message_id": "msg-1",
    }


async def test_message_with_multiple_attachments_sends_text_then_each_file() -> None:
    first, second = _attachment("a.txt"), _attachment("b.txt")
    registry = ChannelRegistry()
    adapter = _ArtifactAdapter()
    registry.register(adapter)
    async with _client(registry, _Resolver([first, second])) as client:
        response = await client.post(DELIVERIES_URL, json=_body([first, second]))
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["delivered"] is True and data.get("outcome") is None
    assert adapter.sent and adapter.sent[0][1].text == "结果正文"
    assert [item.artifact_id for item in adapter.received] == [
        first.artifact_id,
        second.artifact_id,
    ]


async def test_degraded_attachment_stays_degraded_in_the_response() -> None:
    first, second = _attachment("a.txt"), _attachment("b.txt")
    registry = ChannelRegistry()
    adapter = _ArtifactAdapter(
        [
            ArtifactDeliveryOutcome(outcome="DELIVERED"),
            ArtifactDeliveryOutcome(
                outcome="DEGRADED", fallback_url="https://example.test/f/2"
            ),
        ]
    )
    registry.register(adapter)
    async with _client(registry, _Resolver([first, second])) as client:
        response = await client.post(DELIVERIES_URL, json=_body([first, second]))
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["outcome"] == "DEGRADED"
    assert data["fallback_url"] == "https://example.test/f/2"
    assert data["delivered"] is True


async def test_stream_command_carries_attachments_and_degraded_outcome() -> None:
    first, second = _attachment("a.txt"), _attachment("b.txt")
    streams = _Streams(status="DEGRADED", fallback_url="https://example.test/f/9")
    # 空 registry ⇒ WECOM 经 im.outbound 交给 Connector（不在请求面直发）。
    async with _client(ChannelRegistry(), _Resolver([first, second]), streams) as client:
        response = await client.post(DELIVERIES_URL, json=_body([first, second]))
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["outcome"] == "DEGRADED"
    assert data["fallback_url"] == "https://example.test/f/9"
    assert len(streams.commands) == 1
    command = streams.commands[0]
    assert [item.artifact_id for item in command.attachments] == [
        first.artifact_id,
        second.artifact_id,
    ]
    assert command.text == "结果正文"


async def test_storage_key_must_match_the_resolved_attachment() -> None:
    first = _attachment("a.txt")
    forged = first.model_copy(update={"storage_key": "artifacts/someone-else.bin"})
    registry = ChannelRegistry()
    registry.register(_ArtifactAdapter())
    async with _client(registry, _Resolver([first])) as client:
        response = await client.post(DELIVERIES_URL, json=_body([forged]))
    assert response.status_code == 404
    assert response.json()["code"] == "COMMON_NOT_FOUND"
