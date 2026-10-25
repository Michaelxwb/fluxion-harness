"""B-247 Gateway 注册 WEBCHAT 标准通道适配器（design §3.2 架构与执行边界、§3.3 数据设计；TASK-047）。

真实边界（业务路径不 mock）：

- **真实 Gateway 应用与路由**：直连 `muad_im_gateway.main.app`（真实路由表、真实 api-kit 封套）；
  HTTP 提交走真实 ASGI/HTTP 封套，探针路由与 TASK-052 的生产消息路由同形（正文按
  `WebchatMessageRequest` 契约解析、像生产一样经 `RegistryDep` 从注册表取适配器）。
- **真实通道消费**：真实 `InboundPipeline.consume(adapter)`——WEBCHAT 入站**只**经
  `iter_events()` 这一个入口进入既有管线；真实 Console HTTP（uvicorn 桩按 Owner 接口封套应答）
  与真实 Runtime HTTP 接收端（真实 `text/event-stream` 应答）。
- **真实装配与快照分发**：真实 `build_channel_registry`（装配根同形）、真实 `BotSnapshotCache`
  与真实 `dispatch_snapshot`；企微侧用真实 `WeComAdapter` + `FakeWeComSdkFactory` 记录它是否
  **真的**去建客户端/读 secret（长连接路径）。
- 网关不持库：本文件不碰任何数据库（DB 只经 Owner 接口访问，网关只做 HTTP 调用）。

断言要点（B-247）：

- HTTP 输入被规范化为统一 `ChannelEnvelope`（channel/bot_id/external_user_id/
  external_conversation_id/message_id 逐项对应）并经 `iter_events` 唯一入口消费成真实 Run；
- 有界缓存：入站队列到上限即**显式拒绝**（专用错误类型），不静默丢；已接受的条目一条不少；
- 快照按 `BotSnapshotItem.channel` 分发：WEBCHAT 条目**不触发**企微长连接/secret 路径；
- 出站经装配注入的订阅面端口映射渠道中立载荷；未装配则**显式失败**，不静默丢。
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

import pytest
import uvicorn
from fakes import FakeWeComSdkFactory, StubConsole
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from httpx import ASGITransport, AsyncClient
from muad_api import ApiResponse, ok
from muad_common import SharedSettings
from muad_contracts import BotSnapshotItem, ChannelEnvelope, DeliveryMessage, DeliveryRouteInput
from muad_contracts.webchat import WebchatMessageRequest
from muad_im_connector.channels.wecom.adapter import WeComAdapter
from muad_im_core import BotSnapshotCache
from muad_im_gateway.api.deps import RegistryDep
from muad_im_gateway.application.console_client import ConsoleClient
from muad_im_gateway.application.inbound import InboundPipeline
from muad_im_gateway.application.runtime_client import RuntimeClient
from muad_im_gateway.channels.base import (
    ChannelAdapter,
    ChannelAdapterUnavailable,
    ChannelRegistry,
)
from muad_im_gateway.channels.registry import build_channel_registry, dispatch_snapshot
from muad_im_gateway.channels.webchat import (
    WEBCHAT_INBOUND_QUEUE_MAX,
    WebchatChannelAdapter,
    WebchatInboundMessage,
    WebchatInboundOverloaded,
)
from muad_im_gateway.channels.webhook import WebhookChannelAdapter
from muad_im_gateway.infrastructure.dedupe import NullDedupeStore
from muad_im_gateway.main import app

pytestmark = pytest.mark.integration

GATEWAY_TENANT = "tenant-b247"
ENTRY_ID = "entry-b247"
PLATFORM_USER_ID = uuid.UUID("9f4a1c22-6d3b-4c8e-9a10-2f5b7d8e0a11")
CONVERSATION_ID = uuid.UUID("3c7d5e91-0b24-4a6f-8d33-5e9a1c4b7f20")
AGENT_ID = uuid.UUID("a1b2c3d4-5e6f-4a7b-8c9d-0e1f2a3b4c5d")
MESSAGE_ID = "msg-b247-http-1"
TEXT = "网页入口的检查请求"
SUBMIT_URL = "/api/v1/webchat/_probe/messages"
RESOLVE_PATH = "/internal/channel/resolve"
SKILLS_PATH = "/internal/channel/skills"
AUDIT_PATH = "/internal/channel/audit"
BOTS_PATH = "/internal/channel/bots"
RUNS_PATH = "/v1/runs"
READY_TIMEOUT_SEC = 15.0
WAIT_TIMEOUT_SEC = 10.0
#: 企微凭据 canary：只允许出现在发给企微 SDK 的 secret 路径上，不得因 WEBCHAT 条目被触碰。
WECOM_SECRET_CANARY = "b247-wecom-secret-canary"


# --- 真实 Gateway 应用上的探针路由（TASK-052 的生产消息路由同形） ------------------------


_probe_router = APIRouter(prefix="/api/v1/webchat/_probe", tags=["webchat-probe"])


@_probe_router.post("/messages")
async def _probe_submit(request: Request, registry: RegistryDep) -> ApiResponse[Any]:
    """HTTP 输入 → 从注册表取 WEBCHAT 适配器 → 规范化入队（返回规范化后的事件形状）。"""
    body = WebchatMessageRequest.model_validate(await request.json())
    adapter = registry.get("WEBCHAT")
    assert isinstance(adapter, WebchatChannelAdapter)
    envelope = adapter.submit(
        WebchatInboundMessage(
            entry_id=ENTRY_ID,
            platform_user_id=str(PLATFORM_USER_ID),
            conversation_id=str(body.conversation_id),
            message_id=body.message_id,
            text=body.text,
        )
    )
    return ok(request.app.state.message_catalog, envelope.model_dump(mode="json"))


app.include_router(_probe_router)


# --- 真实 HTTP 探针：Console Owner 接口 / Runtime SSE 接收端 / 出站订阅面 -----------------


def _envelope(data: Any) -> JSONResponse:
    return JSONResponse(status_code=200, content={"code": "0", "msg": "stub", "data": data})


class _OwnerProbe:
    """Console Owner 接口的真实 HTTP 桩：记录收到的请求体，按契约封套应答。"""

    def __init__(self, stub: StubConsole) -> None:
        self.resolve_bodies: list[dict[str, Any]] = []
        self.audit_bodies: list[dict[str, Any]] = []
        self.snapshot_items: tuple[BotSnapshotItem, ...] = ()

        async def resolve(request: Request) -> Response:
            self.resolve_bodies.append(json.loads(await request.body() or b"{}"))
            return _envelope(
                {
                    "bound": True,
                    "authorized": True,
                    "agent_id": str(AGENT_ID),
                    "platform_user_id": str(PLATFORM_USER_ID),
                }
            )

        async def skills(_: Request) -> Response:
            return _envelope({"items": [], "page": 1, "page_size": 20, "total": 0})

        async def audit(request: Request) -> Response:
            self.audit_bodies.append(json.loads(await request.body() or b"{}"))
            return _envelope(None)

        async def bots(_: Request) -> Response:
            items = [item.model_dump(mode="json") for item in self.snapshot_items]
            return _envelope(
                {
                    "revision": "sha256:" + "0" * 64,
                    "items": items,
                    "page": 1,
                    "page_size": 20,
                    "total": len(items),
                }
            )

        stub.handlers[RESOLVE_PATH] = resolve
        stub.handlers[SKILLS_PATH] = skills
        stub.handlers[AUDIT_PATH] = audit
        stub.handlers[BOTS_PATH] = bots


class _RuntimeReceiver:
    """真实 Runtime HTTP 接收端：记录 Run 请求并以真实 SSE 应答（真实 socket）。"""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.headers: list[dict[str, str]] = []
        runtime_app = FastAPI()

        @runtime_app.post(RUNS_PATH)
        async def _runs(request: Request) -> StreamingResponse:
            self.requests.append(json.loads(await request.body() or b"{}"))
            self.headers.append(dict(request.headers))

            async def _stream() -> AsyncIterator[str]:
                yield 'event: run.created\ndata: {"run_id": "run-b247", "resumed": false}\n\n'
                yield (
                    'event: run.completed\ndata: {"status": "COMPLETED", "final_text": "检查完成"}\n\n'
                )

            return StreamingResponse(_stream(), media_type="text/event-stream")

        self._app = runtime_app
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self.url = ""

    def start(self) -> None:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
        config = uvicorn.Config(self._app, host="127.0.0.1", port=port, log_level="error")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + READY_TIMEOUT_SEC
        while not self._server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        if not self._server.started:
            raise RuntimeError("runtime receiver did not start")
        self.url = f"http://127.0.0.1:{port}"

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)


class _NoLinks:
    """取件直链签发口的最小实现：本文件不交付产物，只满足装配签名的真实类型。"""

    async def issue_fetch_link(self, artifact_id: uuid.UUID, *, tenant_id: str) -> str | None:
        raise AssertionError("b247 must not issue fetch links")


class _RecordingSink:
    """出站订阅面探针（TASK-052/061 的 SSE 投影面同形）：只记录渠道中立的投递。"""

    def __init__(self) -> None:
        self.delivered: list[tuple[DeliveryRouteInput, DeliveryMessage]] = []
        self.streamed: list[tuple[DeliveryRouteInput, tuple[str, ...]]] = []

    async def deliver(self, route: DeliveryRouteInput, message: DeliveryMessage) -> None:
        self.delivered.append((route, message))

    async def deliver_stream(
        self, route: DeliveryRouteInput, chunks: AsyncIterator[str]
    ) -> None:
        self.streamed.append((route, tuple([chunk async for chunk in chunks])))


# --- 夹具与工具 -------------------------------------------------------------------------


@pytest.fixture
async def stub_console() -> AsyncIterator[StubConsole]:
    stub = StubConsole()
    stub.start()
    try:
        yield stub
    finally:
        stub.stop()


@pytest.fixture
async def runtime_receiver() -> AsyncIterator[_RuntimeReceiver]:
    receiver = _RuntimeReceiver()
    receiver.start()
    try:
        yield receiver
    finally:
        receiver.stop()


@asynccontextmanager
async def _gateway_client(registry: ChannelRegistry) -> AsyncIterator[AsyncClient]:
    """真实 Gateway 应用 + 装配根同形的状态注入（`registry`/`tenant_id`）。"""
    previous: dict[str, Any] = {
        name: getattr(app.state, name, None) for name in ("registry", "tenant_id")
    }
    app.state.registry = registry
    app.state.tenant_id = GATEWAY_TENANT
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://gateway.test"
        ) as client:
            yield client
    finally:
        for name, value in previous.items():
            if value is None:
                with suppress(AttributeError):
                    delattr(app.state, name)
            else:
                setattr(app.state, name, value)


async def _wait_for(predicate: Any) -> None:
    deadline = time.monotonic() + WAIT_TIMEOUT_SEC
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition was not met before the deadline")
        await asyncio.sleep(0.02)


def _inbound(message_id: str, *, text: str = "x") -> WebchatInboundMessage:
    return WebchatInboundMessage(
        entry_id=ENTRY_ID,
        platform_user_id=str(PLATFORM_USER_ID),
        conversation_id=str(CONVERSATION_ID),
        message_id=message_id,
        text=text,
    )


def _route() -> DeliveryRouteInput:
    return DeliveryRouteInput(
        channel="WEBCHAT",
        bot_id=ENTRY_ID,
        external_user_id=str(PLATFORM_USER_ID),
        external_conversation_id=str(CONVERSATION_ID),
    )


async def _chunks(*values: str) -> AsyncIterator[str]:
    for value in values:
        yield value


# --- B-247 ------------------------------------------------------------------------------


async def test_b247_http_input_is_normalized_and_consumed_via_iter_events(
    stub_console: StubConsole, runtime_receiver: _RuntimeReceiver
) -> None:
    """HTTP 输入 → 统一事件 → `iter_events` 唯一入口 → 真实 Run（含出站映射）。"""
    owner = _OwnerProbe(stub_console)
    sink = _RecordingSink()
    adapter = WebchatChannelAdapter(sink=sink)
    registry = ChannelRegistry()
    registry.register(adapter)
    await adapter.start()

    console = ConsoleClient(stub_console.url)
    runtime = RuntimeClient(runtime_receiver.url)
    pipeline = InboundPipeline(
        dedupe=NullDedupeStore(),
        console=console,
        runtime=runtime,
        catalog=app.state.message_catalog,
        tenant_id=GATEWAY_TENANT,
    )
    consumer = asyncio.create_task(pipeline.consume(adapter))
    try:
        async with _gateway_client(registry) as client:
            response = await client.post(
                SUBMIT_URL,
                json={
                    "conversation_id": str(CONVERSATION_ID),
                    "message_id": MESSAGE_ID,
                    "text": TEXT,
                },
            )
        assert response.status_code == 200, response.text
        # ① HTTP 输入被规范化为统一事件：字段逐项对应（通道特有形状一步都不上去）
        assert response.json()["data"] == {
            "channel": "WEBCHAT",
            "bot_id": ENTRY_ID,
            "external_user_id": str(PLATFORM_USER_ID),
            "external_conversation_id": str(CONVERSATION_ID),
            "message_id": MESSAGE_ID,
            "text": TEXT,
            "attachments": [],
            "unsupported_media": None,
        }

        # ② 经唯一 iter_events 入口被既有管线消费成真实 Run（真实 HTTP 送达 Runtime）
        await _wait_for(lambda: runtime_receiver.requests)
        assert runtime_receiver.requests[0]["channel"] == {
            "type": "WEBCHAT",
            "bot_id": ENTRY_ID,
            "external_user_id": str(PLATFORM_USER_ID),
            "external_conversation_id": str(CONVERSATION_ID),
        }
        assert runtime_receiver.requests[0]["message"]["id"] == MESSAGE_ID
        assert runtime_receiver.requests[0]["message"]["text"] == TEXT
        # 幂等身份沿链路透传：原 message_id 就是 Runtime 的幂等键
        assert runtime_receiver.headers[0]["idempotency-key"] == MESSAGE_ID
        # 事件已被消费：有界队列里不残留（一条事件只有这一个消费入口）
        assert adapter.pending == 0

        # ③ 出站映射：终态文本经装配注入的订阅面端口（渠道中立 route/message）
        await _wait_for(lambda: sink.delivered)
        route, message = sink.delivered[-1]
        assert route.channel == "WEBCHAT"
        assert route.bot_id == ENTRY_ID
        assert route.external_conversation_id == str(CONVERSATION_ID)
        assert message.text == "检查完成"

        # ④ Owner 接口看到的是同一个通道与入口（同一封套贯穿到 Console）
        assert owner.resolve_bodies == [
            {
                "channel": "WEBCHAT",
                "bot_id": ENTRY_ID,
                "external_user_id": str(PLATFORM_USER_ID),
                "external_conversation_id": str(CONVERSATION_ID),
            }
        ]
    finally:
        consumer.cancel()
        with suppress(asyncio.CancelledError):
            await consumer
        await console.aclose()
        await runtime.aclose()


async def test_b247_inbound_buffer_is_bounded_and_overload_is_explicit() -> None:
    """缓存有界：到上限即**显式拒绝**（专用错误类型），已接受的条目一条不少。"""
    adapter = WebchatChannelAdapter(max_pending=2)
    await adapter.start()

    accepted = [adapter.submit(_inbound(f"msg-b247-{index}")) for index in range(2)]
    assert adapter.pending == 2
    with pytest.raises(WebchatInboundOverloaded):
        adapter.submit(_inbound("msg-b247-overflow"))
    assert adapter.pending == 2, "超出的条目必须被显式拒绝，而不是撑大缓存"

    events = await adapter.iter_events()
    drained = [await anext(events) for _ in range(2)]
    assert drained == accepted, "已接受的条目必须原样可消费（不静默丢）"
    assert all(envelope.channel == "WEBCHAT" for envelope in drained)

    # 未启动/已停用：入站与消费都**显式失败**（不静默吞）
    stopped = WebchatChannelAdapter()
    with pytest.raises(ChannelAdapterUnavailable):
        stopped.submit(_inbound("msg-b247-stopped"))
    with pytest.raises(ChannelAdapterUnavailable):
        await stopped.iter_events()
    await adapter.stop()
    with pytest.raises(ChannelAdapterUnavailable):
        adapter.submit(_inbound("msg-b247-after-stop"))


async def test_b247_snapshot_dispatch_by_channel_never_touches_wecom_secret_path(
    stub_console: StubConsole,
) -> None:
    """快照按 `BotSnapshotItem.channel` 分发：WEBCHAT 条目不进企微的长连接/secret 路径。"""
    owner = _OwnerProbe(stub_console)
    webchat_item = BotSnapshotItem(
        bot_account_id=uuid.uuid4(),
        bot_id=ENTRY_ID,
        channel="WEBCHAT",
        secret=None,
        agent_id=AGENT_ID,
    )
    wecom_item = BotSnapshotItem(
        bot_account_id=uuid.uuid4(),
        bot_id="wecom-bot-b247",
        channel="WECOM",
        secret=WECOM_SECRET_CANARY,
        agent_id=AGENT_ID,
    )
    owner.snapshot_items = (webchat_item, wecom_item)

    factory = FakeWeComSdkFactory()
    wecom = WeComAdapter(sdk_factory=factory)
    webchat = WebchatChannelAdapter()
    registry = ChannelRegistry()
    registry.register(wecom)
    registry.register(webchat)
    await registry.start_all()

    console = ConsoleClient(stub_console.url)
    cache = BotSnapshotCache(
        console,
        tenant_id=GATEWAY_TENANT,
        on_snapshot_changed=dispatch_snapshot(registry),
    )
    try:
        await cache.refresh()
    finally:
        await console.aclose()

    assert cache.revision is not None, "真实 Console HTTP 的快照必须被完整发布"
    # 每个适配器只拿到**自己通道**的条目；WEBCHAT 是稳定入口，无需 secret
    assert webchat.enabled_entries == (webchat_item,)
    assert webchat.enabled_entries[0].secret is None
    # 只有企微 bot 走了 secret/长连接路径：WEBCHAT 条目**从未**被交给企微 SDK
    assert factory.secrets == [("wecom-bot-b247", WECOM_SECRET_CANARY)]
    assert [client.bot_id for client in factory.clients] == ["wecom-bot-b247"]
    assert wecom.connection_states == {"wecom-bot-b247": "CONNECTED"}

    # 企微适配器自己按实际 channel 过滤：整份快照直接给它，WEBCHAT 条目也不开连接
    await wecom.apply_snapshot((webchat_item,))
    assert [client.bot_id for client in factory.clients] == ["wecom-bot-b247"]
    assert wecom.connection_states == {}
    await registry.stop_all()


async def test_b247_registry_registers_webchat_standard_adapter_without_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """装配根同形构建：WEBCHAT + 配置驱动的通用 webhook 通道，均无需 bot secret 即可用。"""
    monkeypatch.setenv(
        "GATEWAY_WEBHOOK_CHANNELS",
        '[{"channel": "WECOM", "secret": "test-webhook-secret", "reply_url": null}]',
    )
    registry = build_channel_registry(SharedSettings())
    assert registry.names() == ("WEBCHAT", "WECOM")
    webhook = registry.get("WECOM")
    assert isinstance(webhook, WebhookChannelAdapter)
    adapter = registry.get("WEBCHAT")
    assert isinstance(adapter, WebchatChannelAdapter)
    standard: ChannelAdapter = adapter
    assert standard.name == "WEBCHAT"

    await adapter.start()
    # 无 bot、无 secret：start() 不依赖任何凭据，也不开长连接
    assert adapter.enabled_entries == ()
    assert adapter.pending == 0
    envelope = adapter.submit(_inbound("msg-b247-no-secret"))
    assert envelope == ChannelEnvelope(
        channel="WEBCHAT",
        bot_id=ENTRY_ID,
        external_user_id=str(PLATFORM_USER_ID),
        external_conversation_id=str(CONVERSATION_ID),
        message_id="msg-b247-no-secret",
        text="x",
    )
    await adapter.stop()
    assert WebchatChannelAdapter().max_pending == WEBCHAT_INBOUND_QUEUE_MAX


async def test_b247_outbound_maps_channel_neutral_payload_and_fails_explicitly_when_unwired() -> None:
    """出站：装配注入的订阅面端口收到渠道中立载荷；未装配则显式失败，不静默丢。"""
    route = _route()
    message = DeliveryMessage(text="hi")

    unwired = WebchatChannelAdapter()
    await unwired.start()
    with pytest.raises(ChannelAdapterUnavailable):
        await unwired.send(route, message)
    with pytest.raises(ChannelAdapterUnavailable):
        await unwired.stream(route, _chunks("a"))

    sink = _RecordingSink()
    wired = WebchatChannelAdapter(sink=sink)
    await wired.start()
    await wired.send(route, message)
    await wired.stream(route, _chunks("a", "b"))
    assert sink.delivered == [(route, message)]
    assert sink.streamed == [(route, ("a", "b"))]
