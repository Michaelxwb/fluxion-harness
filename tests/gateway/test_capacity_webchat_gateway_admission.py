"""B-248 Gateway 受理与订阅解耦及精确来源路由（design §3.2 架构与执行边界、§3.4 接口设计；TASK-048）。

真实边界（业务路径不 mock）：

- **真实 Gateway 应用与路由**：直连 `muad_im_gateway.main.app`，HTTP 提交走真实 ASGI/HTTP 封套；
  探针路由与 TASK-052 的生产消息路由同形（正文按 `WebchatMessageRequest` 契约解析、
  经 `app.state.registry` 取适配器）。
- **真实通道适配器 + 真实管线语义**：真实 `WebchatChannelAdapter`（TASK-047）与真实
  `WeComAdapter`（官方 SDK 端口为进程内探针）同装配在一个注册表里。TASK-010 起 WEBCHAT
  入站是 SYNC（无进程内队列）：网页探针经生产同形的 Stream 消费入口
  `InboundPipeline.handle_stream_event` 驱动管线；企微仍经 `iter_events()` + `consume`。
- **真实 HTTP/SSE 协议探针**：Runtime 侧是真实本地 HTTP 服务（真实 `text/event-stream` 应答、
  按文本放行长流、记录 Run 请求与链路头）；Console 侧是真实 HTTP 桩（Owner 接口封套）。
- 网关不持库：本文件不碰任何数据库（DB 只经 Owner 接口访问，网关只做 HTTP 调用）。

断言要点（B-248）：

- **受理槽只覆盖受理**：一个 Run 的长流还挂着时，受理名额已经还回去了——上限=1 时另一条
  消息仍能被受理，长流不占 handler；
- **精确来源路由**：同一用户的不同会话/不同入口各自成路由（互不阻塞、互不串台），回复只回到
  发起它的来源；
- **Webchat 显式会话 / 企微原入口指针**：网页 Run 请求带显式 `conversation_id`（RULE-10 不允许
  隐式最新指针），企微带入口账号与会话指针、不带显式会话；
- **HTTP 重投沿用 message/幂等身份**：重投不新造身份、不改显式会话；已受理的重投按同一
  message 身份被去重；
- `new`/`stop` 等命令回到自己的来源，不串另一入口的 Run。
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from typing import Any
from uuid import UUID

import pytest
import uvicorn
from fakes import FakeWeComSdkFactory, StubConsole
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from httpx import ASGITransport, AsyncClient
from muad_api import ApiResponse, ok
from muad_contracts import BotSnapshotItem, ChannelEnvelope, DeliveryMessage, DeliveryRouteInput
from muad_contracts.webchat import WebchatMessageRequest
from muad_im_connector.channels.wecom.adapter import WeComAdapter
from muad_im_connector.channels.wecom.sdk_port import WeComInboundMessage
from muad_im_gateway.application.console_client import ConsoleClient
from muad_im_gateway.application.inbound import (
    MAX_CONCURRENT_HANDLERS,
    STOP_ACCEPTED_TEXT,
    InboundPipeline,
)
from muad_im_gateway.application.runtime_client import RuntimeClient
from muad_im_gateway.channels.base import ChannelAdapterUnavailable, ChannelRegistry
from muad_im_gateway.channels.webchat import WebchatChannelAdapter
from muad_im_gateway.infrastructure.dedupe import (
    DEDUPE_VALUE,
    DedupeStore,
    InMemoryDedupeStore,
    NullDedupeStore,
)
from muad_im_gateway.main import app

pytestmark = pytest.mark.integration

GATEWAY_TENANT = "tenant-b248"
ENTRY_ID = "entry-b248"
PLATFORM_USER_ID = UUID("9f4a1c22-6d3b-4c8e-9a10-2f5b7d8e0a11")
AGENT_ID = UUID("a1b2c3d4-5e6f-4a7b-8c9d-0e1f2a3b4c5d")
CONVERSATION_A = UUID("3c7d5e91-0b24-4a6f-8d33-5e9a1c4b7f20")
CONVERSATION_B = UUID("b0a1f4c7-2d38-4e59-9a6b-7c8d0e1f2a3b")
WECOM_BOT = "wecom-bot-b248"
WECOM_SECRET = "b248-wecom-secret"
WECOM_CHAT = "chat-b248"
MESSAGE_A = "msg-b248-a"
MESSAGE_B = "msg-b248-b"
TEXT_A = "网页会话 A 的检查请求"
TEXT_B = "网页会话 B 的检查请求"
TEXT_WECOM = "企微入口的检查请求"
TEXT_WEBCHAT = "网页入口的检查请求"

SUBMIT_URL = "/api/v1/webchat/_probe-admission/messages"
RUNS_PATH = "/v1/runs"
CANCEL_ACTIVE_PATH = "/v1/runs/cancel-active"
CONVERSATIONS_PATH = "/v1/conversations"
RESOLVE_PATH = "/internal/channel/resolve"
SKILLS_PATH = "/internal/channel/skills"
AUDIT_PATH = "/internal/channel/audit"
BOTS_PATH = "/internal/channel/bots"

READY_TIMEOUT_SEC = 15.0
WAIT_TIMEOUT_SEC = 10.0
SETTLE_SEC = 0.3


# --- 真实 Gateway 应用上的探针路由（TASK-052 的生产消息路由同形） --------------------------


_probe_router = APIRouter(prefix="/api/v1/webchat/_probe-admission", tags=["webchat-admission-probe"])


@_probe_router.post("/messages")
async def _probe_submit(request: Request) -> ApiResponse[Any]:
    """HTTP 输入 → 统一事件 → **Stream 消费入口**（TASK-010 起 WEBCHAT 入站是 SYNC，
    适配器已无进程内队列；本探针用生产同形的 `handle_stream_event` 驱动管线语义）。"""
    body = WebchatMessageRequest.model_validate(await request.json())
    adapter = request.app.state.registry.get("WEBCHAT")
    assert isinstance(adapter, WebchatChannelAdapter)
    pipeline: InboundPipeline = request.app.state.inbound_pipeline
    envelope = ChannelEnvelope(
        channel="WEBCHAT",
        bot_id=ENTRY_ID,
        external_user_id=str(PLATFORM_USER_ID),
        external_conversation_id=str(body.conversation_id),
        message_id=body.message_id,
        text=body.text,
    )
    asyncio.create_task(pipeline.handle_stream_event(adapter, envelope))
    return ok(request.app.state.message_catalog, envelope.model_dump(mode="json"))


app.include_router(_probe_router)


# --- 真实 HTTP 探针：Console Owner 接口 / Runtime SSE 接收端 / 出站订阅面 -------------------


def _envelope(data: Any) -> JSONResponse:
    return JSONResponse(status_code=200, content={"code": "0", "msg": "stub", "data": data})


def _frame(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


class _OwnerProbe:
    """Console Owner 接口的真实 HTTP 桩：两个入口解析到**同一个** platform_user（同一用户）。"""

    def __init__(self, stub: StubConsole) -> None:
        self.resolve_bodies: list[dict[str, Any]] = []
        self.audit_bodies: list[dict[str, Any]] = []

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
            return _envelope(
                {
                    "revision": "sha256:" + "0" * 64,
                    "items": [],
                    "page": 1,
                    "page_size": 20,
                    "total": 0,
                }
            )

        stub.handlers[RESOLVE_PATH] = resolve
        stub.handlers[SKILLS_PATH] = skills
        stub.handlers[AUDIT_PATH] = audit
        stub.handlers[BOTS_PATH] = bots


class _RuntimeProbe:
    """真实 Runtime HTTP/SSE 接收端：记录 Run 请求与链路头，按文本放行长流。"""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.headers: list[dict[str, str]] = []
        self.cancel_calls: list[dict[str, Any]] = []
        self.conversations: list[dict[str, Any]] = []
        #: 已**真正跑完**（终态帧已发出）的文本：长流是否还挂着，看它。
        self.finished: list[str] = []
        self._gates: dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        runtime_app = FastAPI()

        @runtime_app.post(RUNS_PATH)
        async def _runs(request: Request) -> StreamingResponse:
            body = json.loads(await request.body() or b"{}")
            text = str((body.get("message") or {}).get("text") or "")
            with self._lock:
                self.requests.append(body)
                self.headers.append(dict(request.headers))
                run_id = f"run-b248-{len(self.requests)}"
            gate = self._gate(text)

            async def _stream() -> AsyncIterator[str]:
                yield _frame("run.created", {"run_id": run_id, "resumed": False})
                yield _frame("message.delta", {"delta": f"回答:{text}"})
                while not gate.is_set():
                    await asyncio.sleep(0.01)
                yield _frame("run.completed", {"status": "COMPLETED", "final_text": f"完成:{text}"})
                with self._lock:
                    self.finished.append(text)

            return StreamingResponse(_stream(), media_type="text/event-stream")

        @runtime_app.post(CANCEL_ACTIVE_PATH)
        async def _cancel(request: Request) -> JSONResponse:
            body = json.loads(await request.body() or b"{}")
            with self._lock:
                self.cancel_calls.append(body)
            return _envelope({"run_id": "run-b248-cancel", "status": "CANCELLING"})

        @runtime_app.post(CONVERSATIONS_PATH)
        async def _conversations(request: Request) -> JSONResponse:
            body = json.loads(await request.body() or b"{}")
            with self._lock:
                self.conversations.append(body)
            return _envelope({"conversation_id": str(uuid.uuid4())})

        self._app = runtime_app
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self.url = ""

    def texts(self) -> list[str]:
        with self._lock:
            return [str((body.get("message") or {}).get("text") or "") for body in self.requests]

    def release(self, text: str) -> None:
        self._gate(text).set()

    def _gate(self, text: str) -> threading.Event:
        with self._lock:
            gate = self._gates.get(text)
            if gate is None:
                gate = threading.Event()
                self._gates[text] = gate
            return gate

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
            raise RuntimeError("runtime probe did not start")
        self.url = f"http://127.0.0.1:{port}"

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)


class _RecordingSink:
    """WEBCHAT 出站订阅面探针（TASK-052/061 的 SSE 投影面同形）：只记录渠道中立的投递。"""

    def __init__(self) -> None:
        self.delivered: list[tuple[DeliveryRouteInput, DeliveryMessage]] = []
        self.streamed: list[tuple[DeliveryRouteInput, tuple[str, ...]]] = []

    async def deliver(self, route: DeliveryRouteInput, message: DeliveryMessage) -> None:
        self.delivered.append((route, message))

    async def deliver_stream(
        self, route: DeliveryRouteInput, chunks: AsyncIterator[str]
    ) -> None:
        self.streamed.append((route, tuple([chunk async for chunk in chunks])))


class _RecordingDedupe(InMemoryDedupeStore):
    """进程内去重存储 + 记录：钉住**受理身份**（键）与每次占位的裁决。"""

    def __init__(self) -> None:
        super().__init__()
        self.reservations: list[tuple[str, str | None]] = []

    async def reserve(self, key: str, ttl_sec: int) -> str | None:
        owner = await super().reserve(key, ttl_sec)
        self.reservations.append((key, owner))
        return owner


# --- 夹具与工具 ---------------------------------------------------------------------------


@pytest.fixture()
async def stub_console() -> AsyncIterator[StubConsole]:
    stub = StubConsole()
    stub.start()
    try:
        yield stub
    finally:
        stub.stop()


@pytest.fixture()
async def runtime_probe() -> AsyncIterator[_RuntimeProbe]:
    probe = _RuntimeProbe()
    probe.start()
    try:
        yield probe
    finally:
        probe.stop()


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


async def _wait_for(
    predicate: Callable[[], bool], *, what: str, timeout: float = WAIT_TIMEOUT_SEC
) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError(what)
        await asyncio.sleep(0.02)


def _pipeline(
    console: ConsoleClient,
    runtime: RuntimeClient,
    *,
    dedupe: DedupeStore | None = None,
    max_concurrent_handlers: int = MAX_CONCURRENT_HANDLERS,
) -> InboundPipeline:
    return InboundPipeline(
        dedupe=dedupe if dedupe is not None else NullDedupeStore(),
        console=console,
        runtime=runtime,
        catalog=app.state.message_catalog,
        tenant_id=GATEWAY_TENANT,
        max_concurrent_handlers=max_concurrent_handlers,
    )


async def _post(
    client: AsyncClient, *, message_id: str, conversation_id: UUID, text: str
) -> dict[str, Any]:
    """HTTP 提交（API-06 形状）：返回规范化后的事件形状。"""
    response = await client.post(
        SUBMIT_URL,
        json={
            "conversation_id": str(conversation_id),
            "message_id": message_id,
            "text": text,
        },
    )
    assert response.status_code == 200, response.text
    envelope: dict[str, Any] = response.json()["data"]
    assert envelope["message_id"] == message_id
    assert envelope["external_conversation_id"] == str(conversation_id)
    return envelope


def _wecom_message(*, message_id: str, text: str, reply_id: str) -> WeComInboundMessage:
    return WeComInboundMessage(
        bot_id=WECOM_BOT,
        message_id=message_id,
        external_user_id=str(PLATFORM_USER_ID),
        external_conversation_id=WECOM_CHAT,
        message_type="text",
        text=text,
        reply_id=reply_id,
    )


@asynccontextmanager
async def _webchat_stack(
    stub_console: StubConsole,
    runtime_probe: _RuntimeProbe,
    *,
    dedupe: DedupeStore | None = None,
    max_concurrent_handlers: int = MAX_CONCURRENT_HANDLERS,
) -> AsyncIterator[tuple[ChannelRegistry, _RecordingSink, InboundPipeline]]:
    """真实 WEBCHAT 适配器 + 真实消费循环 + 真实 HTTP 出口（Console/Runtime 都是真实服务）。"""
    sink = _RecordingSink()
    adapter = WebchatChannelAdapter(sink=sink)
    registry = ChannelRegistry()
    registry.register(adapter)
    await adapter.start()
    console = ConsoleClient(stub_console.url)
    runtime = RuntimeClient(runtime_probe.url)
    pipeline = _pipeline(
        console, runtime, dedupe=dedupe, max_concurrent_handlers=max_concurrent_handlers
    )
    previous = getattr(app.state, "inbound_pipeline", None)
    app.state.inbound_pipeline = pipeline
    try:
        yield registry, sink, pipeline
    finally:
        if previous is None:
            with suppress(AttributeError):
                delattr(app.state, "inbound_pipeline")
        else:
            app.state.inbound_pipeline = previous
        await adapter.stop()
        await console.aclose()
        await runtime.aclose()


# --- B-248 --------------------------------------------------------------------------------


async def test_b248_admission_slot_covers_admission_only_and_sources_route_precisely(
    stub_console: StubConsole, runtime_probe: _RuntimeProbe
) -> None:
    """受理槽只覆盖受理（长流不占 handler），且精确来源各自成路由、互不串台。"""
    _OwnerProbe(stub_console)
    async with _webchat_stack(
        stub_console, runtime_probe, max_concurrent_handlers=1
    ) as (registry, sink, pipeline):
        async with _gateway_client(registry) as client:
            await _post(client, message_id=MESSAGE_A, conversation_id=CONVERSATION_A, text=TEXT_A)
            await _wait_for(
                lambda: runtime_probe.texts() == [TEXT_A], what="会话 A 的消息未被受理"
            )

            # ① 受理槽只覆盖受理：A 的长流仍挂着，名额已经还回去了（上限=1）
            await _wait_for(
                lambda: pipeline._handlers._value == 1,  # noqa: SLF001 - 精确断言"名额已归还"
                what="长流仍占着受理名额（handler 槽没有在受理后释放）",
                timeout=5.0,
            )
            assert not runtime_probe.finished, "断言前提：A 的长流必须仍挂着"

            # ② 同用户**另一会话**在 A 的长流未结束时即被受理：不同来源互不阻塞
            await _post(client, message_id=MESSAGE_B, conversation_id=CONVERSATION_B, text=TEXT_B)
            await _wait_for(
                lambda: runtime_probe.texts() == [TEXT_A, TEXT_B],
                what="同用户另一会话被 A 的长流挡住（来源没有分开成路由）",
            )
            assert not runtime_probe.finished, "断言前提：两条长流都仍挂着"

            # ③ 精确来源：各带自己的来源与**显式会话**；幂等身份就是原 message_id
            first, second = runtime_probe.requests
            assert first["channel"] == {
                "type": "WEBCHAT",
                "bot_id": ENTRY_ID,
                "external_user_id": str(PLATFORM_USER_ID),
                "external_conversation_id": str(CONVERSATION_A),
            }
            assert first["conversation_id"] == str(CONVERSATION_A)
            assert second["channel"]["external_conversation_id"] == str(CONVERSATION_B)
            assert second["conversation_id"] == str(CONVERSATION_B)
            assert runtime_probe.headers[0]["idempotency-key"] == MESSAGE_A
            assert runtime_probe.headers[1]["idempotency-key"] == MESSAGE_B

        # ④ 放行：各自的回复只落在自己的来源上（不广播、不串台）
        runtime_probe.release(TEXT_A)
        runtime_probe.release(TEXT_B)
        await _wait_for(lambda: len(sink.streamed) == 2, what="两条回复未出站")
        streamed = {route.external_conversation_id: chunks for route, chunks in sink.streamed}
        assert set(streamed) == {str(CONVERSATION_A), str(CONVERSATION_B)}
        assert streamed[str(CONVERSATION_A)] == (f"回答:{TEXT_A}",)
        assert streamed[str(CONVERSATION_B)] == (f"回答:{TEXT_B}",)
        assert all(
            route.channel == "WEBCHAT"
            and route.bot_id == ENTRY_ID
            and route.external_user_id == str(PLATFORM_USER_ID)
            for route, _ in sink.streamed
        )


async def test_b248_http_redelivery_reuses_message_identity_and_explicit_conversation(
    stub_console: StubConsole, runtime_probe: _RuntimeProbe
) -> None:
    """HTTP 重投沿用 message/幂等身份：不新造身份、不改显式会话（Runtime 才能按幂等键回放原 Run）。

    去重存储不可用（fail-open，at-least-once）时重投**真的**会再发一次——此时能证明发出去的
    还是**同一条消息的身份**，而不是被 Gateway 换成第二个 Run 的身份。
    """
    _OwnerProbe(stub_console)
    async with _webchat_stack(stub_console, runtime_probe) as (registry, _sink, _pipeline):
        async with _gateway_client(registry) as client:
            await _post(client, message_id=MESSAGE_A, conversation_id=CONVERSATION_A, text=TEXT_A)
            await _wait_for(lambda: runtime_probe.texts() == [TEXT_A], what="首投未被受理")
            runtime_probe.release(TEXT_A)
            await _wait_for(
                lambda: runtime_probe.finished == [TEXT_A], what="首投的长流未跑完（同来源串行前提）"
            )

            await _post(client, message_id=MESSAGE_A, conversation_id=CONVERSATION_A, text=TEXT_A)
            await _wait_for(
                lambda: len(runtime_probe.requests) == 2, what="重投未沿用身份送达 Runtime"
            )

        first, second = runtime_probe.requests
        assert first["message"] == second["message"] == {
            "id": MESSAGE_A,
            "type": "text",
            "text": TEXT_A,
            "attachments": [],
            # TASK-053 起 `MessageInput` 的暂存上传引用是契约字段（管道路径恒为空列表）；
            # 重投沿用同一身份时它也必须逐字节一致。
            "upload_ids": [],
        }
        assert first["conversation_id"] == second["conversation_id"] == str(CONVERSATION_A)
        assert first["channel"] == second["channel"]
        assert runtime_probe.headers[0]["idempotency-key"] == MESSAGE_A
        assert runtime_probe.headers[1]["idempotency-key"] == MESSAGE_A


async def test_b248_accepted_redelivery_is_deduped_by_the_same_message_identity(
    stub_console: StubConsole, runtime_probe: _RuntimeProbe
) -> None:
    """已受理的重投按**同一 message 身份**去重：不产生第二个 Run（RULE-02 的网关侧一半）。"""
    _OwnerProbe(stub_console)
    store = _RecordingDedupe()
    async with _webchat_stack(stub_console, runtime_probe, dedupe=store) as (registry, _sink, _pipeline):
        async with _gateway_client(registry) as client:
            await _post(client, message_id=MESSAGE_A, conversation_id=CONVERSATION_A, text=TEXT_A)
            await _wait_for(lambda: runtime_probe.texts() == [TEXT_A], what="首投未被受理")
            runtime_probe.release(TEXT_A)
            await _wait_for(lambda: runtime_probe.finished == [TEXT_A], what="首投的长流未跑完")

            await _post(client, message_id=MESSAGE_A, conversation_id=CONVERSATION_A, text=TEXT_A)
            await _wait_for(
                lambda: len(store.reservations) == 2, what="重投未被受理身份识别（没有第二次占位）"
            )
            await asyncio.sleep(SETTLE_SEC)

        identity = f"im:dedupe:WEBCHAT:{MESSAGE_A}"
        assert [key for key, _ in store.reservations] == [identity, identity]
        # 第二次占位被拒 ⇒ 重投按同一 message 身份判定为已处理，不再提交第二个 Run
        assert store.reservations[0][1] is not None
        assert store.reservations[1][1] is None
        assert runtime_probe.texts() == [TEXT_A]
        assert await store.get_value(identity) == DEDUPE_VALUE


async def test_b248_malformed_explicit_conversation_fails_loudly_without_falling_back(
    stub_console: StubConsole, runtime_probe: _RuntimeProbe
) -> None:
    """显式会话是坏值 ⇒ **显式失败**：绝不退回隐式"最新会话"指针（RULE-10 / RISK-09）。

    悄悄降级成"按入口指针选会话"会把消息写进同用户的另一条会话——那正是多历史会话串写的成因；
    未受理还要释放去重占位，让平台重投能真正重试。
    """
    _OwnerProbe(stub_console)
    sink = _RecordingSink()
    adapter = WebchatChannelAdapter(sink=sink)
    await adapter.start()
    store = _RecordingDedupe()
    console = ConsoleClient(stub_console.url)
    runtime = RuntimeClient(runtime_probe.url)
    pipeline = _pipeline(console, runtime, dedupe=store)
    envelope = ChannelEnvelope(
        channel="WEBCHAT",
        bot_id=ENTRY_ID,
        external_user_id=str(PLATFORM_USER_ID),
        external_conversation_id="not-a-conversation-uuid",
        message_id="msg-b248-malformed",
        text="坏会话的消息",
    )
    try:
        with pytest.raises(ChannelAdapterUnavailable):
            await pipeline.handle(adapter, envelope)
    finally:
        await adapter.stop()
        await console.aclose()
        await runtime.aclose()

    assert runtime_probe.requests == [], "坏会话不得退回隐式指针去建 Run"
    assert sink.delivered == [] and sink.streamed == []
    identity = f"im:dedupe:WEBCHAT:{envelope.message_id}"
    assert [key for key, _ in store.reservations] == [identity]
    assert store.reservations[0][1] is not None, "占位必须占过（否则无从判断重投）"
    assert await store.get_value(identity) is None, "未受理必须释放占位，让重投能真正重试"


async def test_b248_wecom_entry_pointer_and_commands_stay_on_their_own_source(
    stub_console: StubConsole, runtime_probe: _RuntimeProbe
) -> None:
    """企微按**原入口指针**（不带显式会话）；`new`/`stop` 只作用于自己的来源，不串另一入口的 Run。"""
    _OwnerProbe(stub_console)
    factory = FakeWeComSdkFactory()
    wecom = WeComAdapter(
        sdk_factory=factory,
        bots=[
            BotSnapshotItem(
                bot_account_id=uuid.uuid4(),
                bot_id=WECOM_BOT,
                secret=WECOM_SECRET,
                agent_id=AGENT_ID,
            )
        ],
        backoff_base_sec=0.01,
        backoff_max_sec=0.02,
        stream_flush_interval_sec=1000.0,
    )
    sink = _RecordingSink()
    webchat = WebchatChannelAdapter(sink=sink)
    registry = ChannelRegistry()
    registry.register(wecom)
    registry.register(webchat)
    await registry.start_all()

    console = ConsoleClient(stub_console.url)
    runtime = RuntimeClient(runtime_probe.url)
    pipeline = _pipeline(console, runtime)
    # WEBCHAT 入站是 SYNC（无队列）：探针路由经生产同形的 Stream 消费入口驱动管线。
    previous = getattr(app.state, "inbound_pipeline", None)
    app.state.inbound_pipeline = pipeline
    consumers = [asyncio.create_task(pipeline.consume(wecom))]
    try:
        sdk = factory.latest()
        sdk.push_message(
            _wecom_message(message_id="wecom-b248-1", text=TEXT_WECOM, reply_id="req-b248-wecom")
        )
        await _wait_for(
            lambda: runtime_probe.texts() == [TEXT_WECOM], what="企微消息未被受理"
        )

        # ① 企微按**原入口指针**：来源是入口账号 + 会话指针，不带显式会话（Runtime 侧按指针选会话）
        wecom_request = runtime_probe.requests[0]
        assert wecom_request["channel"] == {
            "type": "WECOM",
            "bot_id": WECOM_BOT,
            "external_user_id": str(PLATFORM_USER_ID),
            "external_conversation_id": WECOM_CHAT,
        }
        assert wecom_request.get("conversation_id") is None

        # ② 同一用户的网页会话同时被受理：另一入口不被企微长流阻塞，且带自己的显式会话
        async with _gateway_client(registry) as client:
            await _post(
                client,
                message_id="webchat-b248-1",
                conversation_id=CONVERSATION_A,
                text=TEXT_WEBCHAT,
            )
        await _wait_for(
            lambda: runtime_probe.texts() == [TEXT_WECOM, TEXT_WEBCHAT], what="网页消息未被受理"
        )
        assert runtime_probe.requests[1]["conversation_id"] == str(CONVERSATION_A)
        assert runtime_probe.requests[1]["channel"]["type"] == "WEBCHAT"

        # ③ 企微 `/stop`：命令即时处理（不排在长流后面），且只作用于自己的来源
        sdk.push_message(_wecom_message(message_id="wecom-b248-stop", text="/stop", reply_id="req-b248-stop"))
        await _wait_for(lambda: runtime_probe.cancel_calls, what="/stop 未到达 Runtime")
        await _wait_for(lambda: sdk.replied_texts, what="/stop 回执未回到自己的消息")
        assert runtime_probe.cancel_calls == [
            {"agent_id": str(AGENT_ID), "platform_user_id": str(PLATFORM_USER_ID)}
        ]
        assert sdk.replied_texts == [("req-b248-stop", STOP_ACCEPTED_TEXT)]
        assert runtime_probe.finished == [], "断言前提：两条长流都仍挂着（命令不串别的 Run）"

        # ④ 企微 `/new`：会话创建用**本来源自己的**身份与稳定幂等键，回执回到自己的消息
        sdk.push_message(_wecom_message(message_id="wecom-b248-new", text="/new", reply_id="req-b248-new"))
        await _wait_for(lambda: runtime_probe.conversations, what="/new 未到达 Runtime")
        assert runtime_probe.conversations == [
            {"agent_id": str(AGENT_ID), "platform_user_id": str(PLATFORM_USER_ID)}
        ]
        await _wait_for(
            lambda: len(sdk.replied_texts) == 2, what="/new 回执未回到自己的消息"
        )
        assert sdk.replied_texts[1] == ("req-b248-new", "已创建新会话")

        # ⑤ 放行：各入口的回复只落在自己的来源上（企微回原入口回调，网页回自己的会话）
        runtime_probe.release(TEXT_WECOM)
        runtime_probe.release(TEXT_WEBCHAT)
        await _wait_for(
            lambda: any(reply_id == "req-b248-wecom" for reply_id, *_ in sdk.stream_updates),
            what="企微回复未回到发起它的那条消息",
        )
        await _wait_for(lambda: sink.streamed, what="网页回复未出站")
        assert [route.external_conversation_id for route, _ in sink.streamed] == [
            str(CONVERSATION_A)
        ]
        assert all(
            route.channel == "WEBCHAT" for route, _ in sink.streamed
        ), "企微回复不得落到网页来源"
    finally:
        runtime_probe.release(TEXT_WECOM)
        runtime_probe.release(TEXT_WEBCHAT)
        for consumer in consumers:
            consumer.cancel()
        for consumer in consumers:
            with suppress(asyncio.CancelledError):
                await consumer
        if previous is None:
            with suppress(AttributeError):
                delattr(app.state, "inbound_pipeline")
        else:
            app.state.inbound_pipeline = previous
        await registry.stop_all()
        await console.aclose()
        await runtime.aclose()
