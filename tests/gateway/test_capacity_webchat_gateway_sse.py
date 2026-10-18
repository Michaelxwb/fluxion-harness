"""B-261 Gateway 公开 SSE 游标、分片与缓冲回压（design §3.4 API-07、§3.6、§3.7；TASK-061）。

真实边界（业务路径不 mock）：

- **真实 Gateway 应用与路由 + 真实 HTTP/SSE 协议**：`muad_im_gateway.main.app` 挂载**生产**
  `api/webchat.py` 路由，由真实 uvicorn 进程内服务提供（真实 socket、真实 api-kit 封套/错误目录、
  真实 `api/deps.py` 依赖图与 `ChatStreamGuard` 长流复核）；装配根状态（Console/Runtime 客户端、
  部署租户、传输预算、进程缓冲账本）按生产同形注入。
- **真实 Console HTTP（Owner）**：`ConsoleClient` 打真实本地 uvicorn（真实 socket），按 API-12
  `POST /internal/channel/chat-principal` 应答；CHAT 主体与授权集合只从响应导出。
- **真实 Runtime HTTP/SSE（Owner）**：`RuntimeClient` 打真实本地 uvicorn；`GET /v1/runs/{run}/events`
  是**真实 `text/event-stream`**，按脚本下发真实持久帧（含被过滤的内部帧、合法跳号、分片快照），
  游标越界/过期返回真实错误封套。数据库经 Owner 接口访问：本文件不碰任何数据库，网关零 sqlalchemy。

断言要点（B-261）：

- **游标分型**：`CURSOR_EXPIRED`/`CURSOR_AHEAD` 按 Owner 的真实窗口显式返回（不静默夹取），
  合法游标按 `Last-Event-ID`/`after_seq` 续传；
- **合法跳号不补拉循环**：过滤后 canonical seq 允许不连续，公开流按真实 seq 下发且**只读一次**
  Owner 流（无补拉循环），需要推进过滤水位时下发安全 `checkpoint`（不追加正文、心跳不推进游标）；
- **分片**：`message.snapshot` 超过单帧预算即按 `part_index/part_count` 分片，同 seq 同 event_id
  的全部片到齐才可原子替换（片间不插入其他事件、逐片可被公开契约解析、拼接与原文逐字节一致）；
- **缓冲回压**：单订阅与进程总缓冲都有界，超额**显式关闭**慢订阅（不静默丢帧），客户端按持久
  游标重连补齐缺失区间（不丢不重、不重建 Run）。
"""

from __future__ import annotations

import ast
import asyncio
import json
import socket
import threading
import time
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest
import uvicorn
from fakes import StubConsole
from fastapi import Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from muad_contracts.webchat_events import parse_public_event
from muad_im_gateway.api.webchat import router as webchat_router
from muad_im_gateway.application.chat_principal import CHAT_SESSION_COOKIE
from muad_im_gateway.application.console_client import CHAT_PRINCIPAL_PATH, ConsoleClient
from muad_im_gateway.application.runtime_client import RuntimeClient
from muad_im_gateway.application.webchat_streams import SseBufferLedger
from muad_im_gateway.main import app

from tests.internal_service import TOKEN as INTERNAL_TOKEN
from tests.internal_service import internal_service_token  # noqa: F401  (fixture 注册)

pytestmark = pytest.mark.integration

GATEWAY_TENANT = "tenant-b261"
PLATFORM_USER_ID = UUID("7d2f9b31-4c8a-4e5d-9f10-6a3b8c2d4e51")
SESSION_ID = UUID("c1a4e8f2-6b30-4d7a-8e59-0f2b3c4d5e61")
AGENT_ID = UUID("a1b2c3d4-5e6f-4a7b-8c9d-0e1f2a3b4c5d")
OTHER_AGENT_ID = UUID("b2c3d4e5-6f70-4b8c-9d0e-1f2a3b4c5d6e")
ENTRY_ID = UUID("e1f2a3b4-c5d6-4e7f-8a9b-0c1d2e3f4a5b")
CONVERSATION_ID = UUID("b4c5d6e7-f8a9-4b0c-9d1e-2f3a4b5c6d7e")
RUN_ID = UUID("c5d6e7f8-a9b0-4c1d-8e2f-3a4b5c6d7e8f")
#: CHAT 会话原文 canary：只允许出现在发往 Console 的核验请求体里。
SESSION_TOKEN = f"chat-session-{uuid.uuid4().hex}"
#: 内部内容 canary：内部事件/工具参数/推理/未知事件不得进入公开 SSE。
INTERNAL_CANARY = "internal-canary-b261-tool-arguments"

EVENTS_URL = f"/api/v1/webchat/runs/{RUN_ID}/events"
HEARTBEAT_SEC = 0.1
#: 单帧预算：分片与单帧上限都由它裁决（API-19 的 `sse_event_max_bytes` 同源）。
EVENT_MAX_BYTES = 4096
#: 单订阅缓冲：慢消费者显式关闭的边界（API-19 之外，进程启动预算 §3.7）。
SUBSCRIBER_BUFFER_BYTES = 65536
TOTAL_BUFFER_BYTES = 1048576
#: 回压用例的帧形状：真实增量窗口量级 + 真实窗口节拍（探针按 Runtime 的形状下发）。
BURST_FRAME_BYTES = 3072
BURST_FRAMES = 1200
#: 停读阶段的节拍：比消费端更快（生产端跑在前面 ⇒ 有界缓冲到界即显式关闭）。
BURST_STALL_INTERVAL_SEC = 0.0005
#: 恢复阶段的节拍：回到真实增量窗口节拍（消费端跟得上 ⇒ 一次补齐、不丢不重）。
BURST_RECOVERY_INTERVAL_SEC = 0.001
BUFFER_TEST_EVENT_MAX_BYTES = 65536

#: 生产路由在真实应用上的注册（TASK-096 装配根只做同一件事）。
app.include_router(webchat_router)


def _envelope(data: Any = None) -> dict[str, Any]:
    return {
        "code": "0",
        "msg": "ok",
        "data": data,
        "trace_id": "trace-b261",
        "request_id": "request-b261",
        "timestamp": "2026-10-18T07:00:00Z",
    }


def _json(data: Any, status_code: int = 200) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=_envelope(data))


def _error(code: str, status_code: int, data: Any = None) -> JSONResponse:
    body = _envelope(data)
    body["code"] = code
    body["msg"] = code
    return JSONResponse(status_code=status_code, content=body)


def _frame(event_type: str, data: dict[str, Any], *, seq: int, run_id: UUID = RUN_ID) -> str:
    """Owner 出站帧：与 Runtime `SseEmitter` 同形的真实 canonical 事件封套。"""
    envelope = {
        "run_id": str(run_id),
        "seq": seq,
        "timestamp": "2026-10-18T07:00:00Z",
        "type": event_type,
        "data": data,
    }
    return f"event: {event_type}\ndata: {json.dumps(envelope)}\n\n"


@dataclass(frozen=True)
class _WireFrame:
    """浏览器侧真实收到的 SSE 帧：`id` 游标 + `event` 类型 + `data` 公开 DTO。"""

    cursor: str | None
    event: str | None
    data: dict[str, Any] | None
    raw: str

    @property
    def is_heartbeat(self) -> bool:
        return self.event is None and self.cursor is None


def _parse_sse(text: str) -> list[_WireFrame]:
    """真实 SSE 字节 → 帧列表（注释帧/heartbeat 不参与事件判定）。"""
    frames: list[_WireFrame] = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        cursor: str | None = None
        event: str | None = None
        data_lines: list[str] = []
        for line in block.split("\n"):
            if line.startswith(":"):
                continue
            field, _, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if field == "id":
                cursor = value
            elif field == "event":
                event = value
            elif field == "data":
                data_lines.append(value)
        payload: dict[str, Any] | None = None
        if data_lines:
            decoded = json.loads("\n".join(data_lines))
            assert isinstance(decoded, dict)
            payload = decoded
        frames.append(_WireFrame(cursor=cursor, event=event, data=payload, raw=block))
    return frames


def _public_events(frames: list[_WireFrame]) -> list[dict[str, Any]]:
    """真实帧 → 公开契约事件（逐帧经 `parse_public_event` 校验：非法/内部类型即失败）。"""
    parsed: list[dict[str, Any]] = []
    for frame in frames:
        if frame.is_heartbeat:
            continue
        assert frame.event is not None and frame.data is not None, frame.raw
        event = parse_public_event(frame.data)
        assert event is not None, f"非公开类型出现在公开流上：{frame.raw}"
        assert event.type == frame.event, frame.raw
        parsed.append(event.model_dump(mode="json"))
    return parsed


async def _read_all(response: httpx.Response) -> str:
    chunks: list[str] = []
    async for chunk in response.aiter_text():
        chunks.append(chunk)
    return "".join(chunks)


# --- 真实 Console Owner 接口桩（真实 socket + 真实封套） ----------------------------------


class _ConsoleProbe:
    """API-12 的真实 HTTP 桩：chat-principal；记录每次调用的头/体。"""

    def __init__(self, stub: StubConsole) -> None:
        self.calls: list[tuple[str, dict[str, str], dict[str, Any]]] = []
        self.revoked_code: str | None = None
        self.agents: list[dict[str, str]] = [
            {"agent_id": str(AGENT_ID), "key": "webchat-agent", "name": "网页助手"}
        ]
        stub.handlers[CHAT_PRINCIPAL_PATH] = self._principal

    async def _principal(self, request: Request) -> Response:
        body = json.loads(await request.body() or b"{}")
        self.calls.append((CHAT_PRINCIPAL_PATH, dict(request.headers), body))
        if self.revoked_code is not None:
            return _error(self.revoked_code, 401)
        return _json(
            {
                "principal": {
                    "tenant_id": GATEWAY_TENANT,
                    "platform_user_id": str(PLATFORM_USER_ID),
                    "session_id": str(SESSION_ID),
                    "auth_method": "USER_TOKEN",
                },
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                "agents": list(self.agents),
            }
        )


# --- 真实 Runtime Owner 接口桩（真实 socket + 真实 SSE） -----------------------------------


@dataclass(frozen=True)
class _Call:
    path: str
    query: dict[str, str]
    headers: dict[str, str]


@dataclass
class _StreamPlan:
    """一次 Owner 事件流脚本：真实持久帧 + 游标窗口 + 是否保持打开（活跃 Run）。"""

    frames: list[tuple[int, str, dict[str, Any]]]
    watermark: int
    floor_seq: int = 0
    hold_open_sec: float = 0.0
    #: 帧间节拍：真实 Runtime 的增量有界窗口节拍（探针按同一形状下发，不是瞬间灌满）。
    frame_interval_sec: float = 0.0


class _RuntimeProbe:
    """Runtime Owner 端点桩：真实 HTTP/SSE；按脚本裁决游标与事件，记录每次调用。

    `GET /v1/runs/{run_id}/events` 只按 `after_seq` 下发 `seq > after_seq` 的真实帧（与 Runtime
    `tail_run_events` 同语义），并在游标越界/过期时返回真实错误封套；慢消费者由 Gateway 主动断开，
    断开的连接在这里留痕（`closed_streams`）。
    """

    def __init__(self) -> None:
        self.calls: dict[str, list[_Call]] = defaultdict(list)
        self.plan = _StreamPlan(frames=[], watermark=0)
        self.run_owner = PLATFORM_USER_ID
        self.scope_agent_id = AGENT_ID
        self.run_status = "RUNNING"
        #: 每个被客户端（Gateway）断开的事件流留痕：after_seq + 已下发帧数。
        self.closed_streams: list[tuple[int, int]] = []
        self._lock = threading.Lock()
        runtime_app = self._build_app()
        self._app = runtime_app
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self.url = ""

    # --- 记录 -----------------------------------------------------------------------
    def _record(self, path: str, request: Request) -> None:
        with self._lock:
            self.calls[path].append(
                _Call(path=path, query=dict(request.query_params), headers=dict(request.headers))
            )

    def event_calls(self) -> list[_Call]:
        return self.calls[f"/v1/runs/{RUN_ID}/events"]

    # --- 端点 -----------------------------------------------------------------------
    def _build_app(self) -> Any:
        from fastapi import FastAPI

        runtime_app = FastAPI()

        @runtime_app.post("/v1/runs")
        async def _create_run(request: Request) -> Response:
            # 公开 SSE 永远不得创建 Run（B-261「不重建 Run」）：真被调用即失败。
            self._record("/v1/runs", request)
            return _error("COMMON_INTERNAL_ERROR", 500)

        @runtime_app.get("/v1/runs/{run_id}")
        async def _get_run(run_id: str, request: Request) -> Response:
            self._record(f"/v1/runs/{run_id}", request)
            return _json(
                {
                    "run_id": run_id,
                    "conversation_id": str(CONVERSATION_ID),
                    "agent_id": str(AGENT_ID),
                    "user_id": str(self.run_owner),
                    "status": self.run_status,
                    "execution_status": {"stage": "RUNNING"},
                    "cancel_requested": False,
                }
            )

        @runtime_app.get("/internal/webchat/conversations/{conversation_id}/scope")
        async def _scope(conversation_id: str, request: Request) -> Response:
            self._record(f"/internal/webchat/conversations/{conversation_id}/scope", request)
            return _json(
                {
                    "conversation_id": conversation_id,
                    "entry_id": str(ENTRY_ID),
                    "agent_id": str(self.scope_agent_id),
                }
            )

        runtime_app.get("/v1/runs/{run_id}/events")(self._events_response)
        return runtime_app

    async def _events_response(self, run_id: str, request: Request) -> Response:
        """持久事件流端点：游标先分型（与 Runtime `tail_run_events` 同语义），再按 seq 下发。"""
        self._record(f"/v1/runs/{run_id}/events", request)
        after_seq = int(request.query_params.get("after_seq", "0"))
        plan = self.plan
        if after_seq > plan.watermark:
            return _error(
                "CURSOR_AHEAD", 400, {"after_seq": after_seq, "through_seq": plan.watermark}
            )
        if plan.floor_seq and after_seq + 1 < plan.floor_seq:
            return _error(
                "CURSOR_EXPIRED", 410, {"after_seq": after_seq, "earliest_seq": plan.floor_seq}
            )
        return StreamingResponse(self._event_stream(after_seq), media_type="text/event-stream")

    async def _event_stream(self, after_seq: int) -> AsyncIterator[str]:
        """真实 SSE：按 seq 升序下发（`seq <= 游标` 不重投，过滤跳号合法）。"""
        sent = 0
        try:
            for seq, event_type, payload in self.plan.frames:
                if seq <= after_seq:
                    continue
                if sent and self.plan.frame_interval_sec:
                    await asyncio.sleep(self.plan.frame_interval_sec)
                sent += 1
                yield _frame(event_type, payload, seq=seq)
            deadline = time.monotonic() + self.plan.hold_open_sec
            while time.monotonic() < deadline:
                await asyncio.sleep(0.05)
                yield ": heartbeat\n\n"
        finally:
            with self._lock:
                self.closed_streams.append((after_seq, sent))

    def start(self) -> None:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
        config = uvicorn.Config(self._app, host="127.0.0.1", port=port, log_level="error")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 15.0
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


# --- 真实 Gateway 服务（真实 socket + 真实 SSE 下发） ---------------------------------------


class _GatewayServer:
    """真实 Gateway 应用进程内服务：`main.app` + 生产 webchat 路由，真实 uvicorn（无 lifespan）。"""

    READY_TIMEOUT_SEC = 15.0

    def __init__(self) -> None:
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self.url = ""

    def start(self) -> None:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
        config = uvicorn.Config(
            app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + self.READY_TIMEOUT_SEC
        while not self._server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        if not self._server.started:
            raise RuntimeError("gateway did not start")
        self.url = f"http://127.0.0.1:{port}"

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)


# --- 夹具：真实 HTTP 桩 + 真实 Gateway 服务（按生产装配根状态注入） --------------------------

_STATE_NAMES = (
    "console_client",
    "runtime_client",
    "tenant_id",
    "sse_event_max_bytes",
    "run_event_heartbeat_sec",
    "sse_subscriber_buffer_bytes",
    "sse_total_buffer_bytes",
    "chat_auth_recheck_sec",
    "webchat_sse_ledger",
)


@pytest.fixture
async def stub_console() -> AsyncIterator[StubConsole]:
    stub = StubConsole()
    stub.start()
    try:
        yield stub
    finally:
        stub.stop()


@pytest.fixture
def console_probe(stub_console: StubConsole) -> _ConsoleProbe:
    return _ConsoleProbe(stub_console)


@pytest.fixture
async def runtime_probe() -> AsyncIterator[_RuntimeProbe]:
    probe = _RuntimeProbe()
    probe.start()
    try:
        yield probe
    finally:
        probe.stop()


@dataclass(frozen=True)
class _GatewayEnv:
    """一次用例的 Gateway 环境：真实服务 URL + 进程缓冲账本（断言缓冲有界）。"""

    url: str
    ledger: SseBufferLedger
    subscriber_buffer_bytes: int
    total_buffer_bytes: int


@asynccontextmanager
async def _gateway_env(
    stub_console: StubConsole,
    runtime_probe: _RuntimeProbe,
    *,
    event_max_bytes: int = EVENT_MAX_BYTES,
    subscriber_buffer_bytes: int = SUBSCRIBER_BUFFER_BYTES,
    total_buffer_bytes: int = TOTAL_BUFFER_BYTES,
) -> AsyncIterator[_GatewayEnv]:
    """真实 Gateway 服务 + 真实 Console/Runtime 客户端；只注入装配根会发布的状态值。"""
    previous: dict[str, Any] = {name: getattr(app.state, name, None) for name in _STATE_NAMES}
    console = ConsoleClient(stub_console.url)
    runtime = RuntimeClient(runtime_probe.url)
    ledger = SseBufferLedger(total_bytes=total_buffer_bytes)
    app.state.console_client = console
    app.state.runtime_client = runtime
    app.state.tenant_id = GATEWAY_TENANT
    app.state.sse_event_max_bytes = event_max_bytes
    app.state.run_event_heartbeat_sec = HEARTBEAT_SEC
    app.state.sse_subscriber_buffer_bytes = subscriber_buffer_bytes
    app.state.sse_total_buffer_bytes = total_buffer_bytes
    app.state.chat_auth_recheck_sec = 300.0
    app.state.webchat_sse_ledger = ledger
    server = _GatewayServer()
    server.start()
    try:
        yield _GatewayEnv(
            url=server.url,
            ledger=ledger,
            subscriber_buffer_bytes=subscriber_buffer_bytes,
            total_buffer_bytes=total_buffer_bytes,
        )
    finally:
        server.stop()
        await console.aclose()
        await runtime.aclose()
        for name, value in previous.items():
            if value is None:
                with suppress(AttributeError):
                    delattr(app.state, name)
            else:
                setattr(app.state, name, value)


@asynccontextmanager
async def _chat_client(env: _GatewayEnv) -> AsyncIterator[httpx.AsyncClient]:
    """真实 HTTP 客户端（真实 socket）：带 CHAT Cookie；SSE 读取按需增量。"""
    async with httpx.AsyncClient(
        base_url=env.url,
        cookies={CHAT_SESSION_COOKIE: SESSION_TOKEN},
        timeout=httpx.Timeout(15.0, connect=5.0),
    ) as client:
        yield client


def _chat_headers(context: UUID | str = SESSION_ID) -> dict[str, str]:
    return {"X-Chat-Context": str(context)}


def _principal_calls(console_probe: _ConsoleProbe) -> list[Any]:
    return [call for call in console_probe.calls if call[0] == CHAT_PRINCIPAL_PATH]


# --- B-261：门控、归属与当前授权先于任何 Owner 流 -------------------------------------------


async def test_b261_chat_gate_run_ownership_and_current_authorization_precede_the_owner_stream(
    stub_console: StubConsole,
    console_probe: _ConsoleProbe,
    runtime_probe: _RuntimeProbe,
) -> None:
    """缺凭据/撤销/上下文不符/他人 Run/授权撤销：都在打开 Owner 流之前被拒。"""
    async with _gateway_env(stub_console, runtime_probe) as env:
        async with httpx.AsyncClient(base_url=env.url, timeout=httpx.Timeout(10.0)) as anon:
            no_cookie = await anon.get(EVENTS_URL, headers=_chat_headers())
        async with _chat_client(env) as client:
            no_context = await client.get(EVENTS_URL)
            mismatch = await client.get(EVENTS_URL, headers=_chat_headers(uuid.uuid4()))
    assert no_cookie.status_code == 401 and no_cookie.json()["code"] == "UNAUTHORIZED"
    assert no_context.status_code == 422
    assert mismatch.status_code == 409 and mismatch.json()["code"] == "AUTH_CONTEXT_CHANGED"
    assert runtime_probe.event_calls() == [], "门控未通过不得打开 Owner 流"

    console_probe.revoked_code = "CHAT_SESSION_REVOKED"
    async with _gateway_env(stub_console, runtime_probe) as env:
        async with _chat_client(env) as client:
            revoked = await client.get(EVENTS_URL, headers=_chat_headers())
    assert revoked.status_code == 401 and revoked.json()["code"] == "CHAT_SESSION_REVOKED"
    assert runtime_probe.event_calls() == [], "被撤销的会话不得打开 Owner 流"

    # 他人 Run：归属核验先拒（404 不泄露存在性），零 Owner 事件流。
    console_probe.revoked_code = None
    runtime_probe.run_owner = uuid.uuid4()
    async with _gateway_env(stub_console, runtime_probe) as env:
        async with _chat_client(env) as client:
            foreign = await client.get(EVENTS_URL, headers=_chat_headers())
    assert foreign.status_code == 404 and foreign.json()["code"] == "COMMON_NOT_FOUND"
    assert runtime_probe.event_calls() == []

    # 当前授权集合已不含会话 Agent：SSE 与读取面同口径拒绝，零 Owner 事件流。
    runtime_probe.run_owner = PLATFORM_USER_ID
    runtime_probe.scope_agent_id = OTHER_AGENT_ID
    async with _gateway_env(stub_console, runtime_probe) as env:
        async with _chat_client(env) as client:
            forbidden = await client.get(EVENTS_URL, headers=_chat_headers())
    assert forbidden.status_code == 403 and forbidden.json()["code"] == "FORBIDDEN"
    assert runtime_probe.event_calls() == []
    scope_calls = runtime_probe.calls[
        f"/internal/webchat/conversations/{CONVERSATION_ID}/scope"
    ]
    assert scope_calls and scope_calls[-1].headers["x-internal-service"] == INTERNAL_TOKEN
    assert SESSION_TOKEN not in (
        no_cookie.text + mismatch.text + revoked.text + foreign.text + forbidden.text
    )
    assert len(_principal_calls(console_probe)) >= 1


# --- B-261：游标分型与续传 -------------------------------------------------------------------


async def test_b261_cursor_errors_are_typed_expired_and_ahead_not_clamped(
    stub_console: StubConsole,
    console_probe: _ConsoleProbe,
    runtime_probe: _RuntimeProbe,
) -> None:
    """过期（410）/超前（400）按 Owner 真实窗口显式返回，不在网关侧夹取或补造窗口字段。"""
    runtime_probe.plan = _StreamPlan(
        frames=[(9, "message.delta", {"delta": "续传", "reply_generation": 0})],
        watermark=40,
        floor_seq=5,
    )
    async with _gateway_env(stub_console, runtime_probe) as env:
        async with _chat_client(env) as client:
            expired = await client.get(EVENTS_URL, headers=_chat_headers(), params={"after_seq": 2})
            ahead = await client.get(EVENTS_URL, headers=_chat_headers(), params={"after_seq": 99})
            bad_last_event_id = await client.get(
                EVENTS_URL, headers=_chat_headers() | {"Last-Event-ID": "not-a-seq"}
            )

    # 过期游标：保留期外显式 410（不静默夹取成最早可用），并带 Owner 的真实最早可用水位。
    assert expired.status_code == 410, expired.text
    assert expired.json()["code"] == "CURSOR_EXPIRED"
    assert expired.json()["data"] == {"after_seq": 2, "earliest_seq": 5}
    # 超前游标：显式 400 + Owner 的真实持久水位。
    assert ahead.status_code == 400, ahead.text
    assert ahead.json()["code"] == "CURSOR_AHEAD"
    assert ahead.json()["data"] == {"after_seq": 99, "through_seq": 40}
    # 坏游标显式拒绝，不猜测意图。
    assert bad_last_event_id.status_code == 422
    assert bad_last_event_id.json()["code"] == "COMMON_VALIDATION_ERROR"
    assert [call.query for call in runtime_probe.event_calls()] == [
        {"after_seq": "2"},
        {"after_seq": "99"},
    ], "游标原样交给 Owner 裁决（坏 Last-Event-ID 不触达 Owner）"
    assert all(call.headers["x-tenant-id"] == GATEWAY_TENANT for call in runtime_probe.event_calls())


async def test_b261_legal_cursor_resumes_by_after_seq_and_last_event_id(
    stub_console: StubConsole,
    console_probe: _ConsoleProbe,
    runtime_probe: _RuntimeProbe,
) -> None:
    """保留期内（含过滤跳号）不是过期：合法游标真实续传，游标排他且流结束不占进程预算。"""
    runtime_probe.plan = _StreamPlan(
        frames=[(9, "message.delta", {"delta": "续传", "reply_generation": 0})],
        watermark=40,
        floor_seq=5,
    )
    async with _gateway_env(stub_console, runtime_probe) as env:
        async with _chat_client(env) as client:
            resumed = await client.get(EVENTS_URL, headers=_chat_headers(), params={"after_seq": 7})
            by_last_event_id = await client.get(
                EVENTS_URL, headers=_chat_headers() | {"Last-Event-ID": "7"}
            )
        # 流结束后不占进程预算（账本不漂移：订阅退出即释放）。
        assert env.ledger.stats().active_subscribers == 0
        assert env.ledger.stats().total_pending_bytes == 0

    assert resumed.status_code == 200, resumed.text
    assert resumed.headers["cache-control"] == "no-store"
    assert resumed.headers["x-accel-buffering"] == "no"
    # 游标排他：after_seq=7 ⇒ 下一条是 9（已应用的水位不重投）。
    assert [frame.cursor for frame in _parse_sse(resumed.text)] == ["9"]
    assert by_last_event_id.status_code == 200, by_last_event_id.text
    assert [frame.cursor for frame in _parse_sse(by_last_event_id.text)] == ["9"]
    assert [call.query for call in runtime_probe.event_calls()] == [
        {"after_seq": "7"},
        {"after_seq": "7"},
    ]


# --- B-261：合法跳号、checkpoint 与心跳不推进游标 --------------------------------------------


async def test_b261_legal_filtered_gaps_stream_once_with_checkpoints_and_no_repull(
    stub_console: StubConsole,
    console_probe: _ConsoleProbe,
    runtime_probe: _RuntimeProbe,
) -> None:
    """过滤跳号按真实 seq 下发、只读一次 Owner 流；checkpoint 推进已扫描水位；心跳不推进游标。"""
    runtime_probe.plan = _StreamPlan(
        frames=[
            (1, "tool.completed", {"arguments": INTERNAL_CANARY, "result": INTERNAL_CANARY}),
            (3, "message.delta", {"delta": "第一段", "reply_generation": 0}),
            (7, "model.started", {"model": "internal-canary-b261"}),
            (9, "message.delta", {"delta": "第二段", "reply_generation": 0}),
            (11, "run.completed", {"status": "COMPLETED", "final_text": "第一段第二段"}),
        ],
        watermark=11,
        hold_open_sec=0.6,
    )
    async with _gateway_env(stub_console, runtime_probe) as env:
        async with _chat_client(env) as client:
            async with client.stream("GET", EVENTS_URL, headers=_chat_headers()) as response:
                assert response.status_code == 200, await response.aread()
                assert response.headers["content-type"].startswith("text/event-stream")
                text = await _read_all(response)

    frames = _parse_sse(text)
    events = _public_events(frames)
    # 过滤跳号：公开流只出 3/9/11，内部帧与 canary 一律不出现。
    assert [event["seq"] for event in events if event["type"] != "checkpoint"] == [3, 9, 11]
    assert [event["type"] for event in events if event["type"] != "checkpoint"] == [
        "message.delta",
        "message.delta",
        "run.terminal",
    ]
    assert INTERNAL_CANARY not in text and "internal-canary-b261" not in text
    # 安全 checkpoint：只推进已扫描水位（严格小于下一条公开事件的 seq），不追加正文。
    checkpoints = [event for event in events if event["type"] == "checkpoint"]
    assert [checkpoint["seq"] for checkpoint in checkpoints] == [1, 7]
    assert all(checkpoint["data"] == {} for checkpoint in checkpoints)
    assert all("event_id" not in checkpoint for checkpoint in checkpoints)
    # 帧 id 就是 canonical seq：游标只在真实产出已提交行时前进。
    assert [frame.cursor for frame in frames if not frame.is_heartbeat] == ["1", "3", "7", "9", "11"]
    # 心跳不推进业务游标（无 id、无事件）。
    heartbeats = [frame for frame in frames if frame.is_heartbeat]
    assert len(heartbeats) >= 2 and all(frame.cursor is None for frame in heartbeats)
    # 合法跳号不触发补拉循环：整个订阅只读一次 Owner 流。
    assert len(runtime_probe.event_calls()) == 1
    assert runtime_probe.event_calls()[0].query == {"after_seq": "0"}
    # 被过滤的行留痕（过滤与坏帧可区分），且没有触发任何补拉。
    assert env.ledger.stats().filtered_events == 2


# --- B-261：snapshot 分片 --------------------------------------------------------------------


def _assert_snapshot_group(frames: list[_WireFrame], events: list[dict[str, Any]], text: str) -> None:
    """分片断言：逐帧在预算内、同 seq/event_id/生成号、片间无插入、拼接逐字节等于原文。"""
    parts = [event for event in events if event["type"] == "message.snapshot"]
    assert len(parts) > 2, "超过单帧预算的 snapshot 必须分片"
    for frame in frames:
        assert len(frame.raw.encode("utf-8")) + 2 <= EVENT_MAX_BYTES, frame.raw[:200]
    assert len({(part["run_id"], part["event_id"], part["seq"]) for part in parts}) == 1
    assert {part["seq"] for part in parts} == {9}
    assert {part["data"]["reply_generation"] for part in parts} == {1}
    assert [part["data"]["part_index"] for part in parts] == list(range(len(parts)))
    assert {part["data"]["part_count"] for part in parts} == {len(parts)}
    assert "".join(part["data"]["text"] for part in parts) == text
    positions = [index for index, event in enumerate(events) if event["type"] == "message.snapshot"]
    assert positions == list(range(positions[0], positions[-1] + 1)), "片间不得插入其他事件"


async def test_b261_snapshot_parts_share_identity_and_only_complete_groups_apply(
    stub_console: StubConsole,
    console_probe: _ConsoleProbe,
    runtime_probe: _RuntimeProbe,
) -> None:
    """大 snapshot 按同 seq 同 event_id 分片：逐片可解析、片间无插入、拼接逐字节等于原文。"""
    text = "正文🌏" * 1200
    assert len(text.encode("utf-8")) > EVENT_MAX_BYTES * 2
    runtime_probe.plan = _StreamPlan(
        frames=[
            (5, "message.delta", {"delta": "旧代正文", "reply_generation": 0}),
            (7, "run.stage", {"stage": "WAITING_MODEL", "reason": "NO_CAPACITY",
                              "queued_at": "2026-10-18T06:59:00Z",
                              "deadline_at": "2026-10-18T07:00:00Z"}),
            (9, "message.snapshot", {"reply_generation": 1, "text": text}),
            (11, "message.delta", {"delta": "续写", "reply_generation": 1}),
        ],
        watermark=11,
    )
    async with _gateway_env(stub_console, runtime_probe) as env:
        async with _chat_client(env) as client:
            response = await client.get(EVENTS_URL, headers=_chat_headers(), params={"after_seq": 4})
            assert response.status_code == 200, response.text
            body = response.text

    frames = [frame for frame in _parse_sse(body) if not frame.is_heartbeat]
    events = _public_events(frames)
    _assert_snapshot_group(frames, events, text)
    assert [event["seq"] for event in events if event["type"] != "message.snapshot"] == [5, 7, 11]
    # 阶段事件按持久投影原样公开（排队原因与期限是安全 DTO，不掺内部内容）。
    stage = next(event for event in events if event["type"] == "run.stage")
    assert stage["data"] == {
        "stage": "WAITING_MODEL",
        "reason": "NO_CAPACITY",
        "queued_at": "2026-10-18T06:59:00Z",
        "deadline_at": "2026-10-18T07:00:00Z",
        "started_at": None,
    }
    # 分片不改变公开身份：旧代正文先于新代 snapshot，续写在整组之后。
    assert events[-1]["data"] == {"reply_generation": 1, "text": "续写"}
    assert runtime_probe.event_calls()[0].query == {"after_seq": "4"}


# --- B-261：单订阅缓冲有界 + 慢消费者断开可恢复 ------------------------------------------------


def _burst_plan(interval_sec: float) -> _StreamPlan:
    """慢消费者脚本：真实增量窗口量级的连续帧（节拍决定生产端是否跑在消费端前面）。"""
    return _StreamPlan(
        frames=[
            (seq, "message.delta", {"delta": "x" * BURST_FRAME_BYTES, "reply_generation": 0})
            for seq in range(1, BURST_FRAMES + 1)
        ],
        watermark=BURST_FRAMES,
        frame_interval_sec=interval_sec,
    )


async def _consume_with_cursor_recovery(
    client: httpx.AsyncClient,
    *,
    probe: _RuntimeProbe,
    total_frames: int,
    stall_first_sec: float = 0.0,
    recovery_interval_sec: float | None = None,
    max_streams: int = 6,
) -> tuple[list[int], int]:
    """像真实客户端一样消费公开流：被断开就按持久游标重连，直到覆盖全部序号。

    返回（逐帧应用的 seq 列表，第一条流实际收到的帧数）——第二条流起按 `Last-Event-ID`
    续传；`stall_first_sec` 让第一条流先不读（慢消费者），`recovery_interval_sec` 让重连后的
    上游节拍回到消费端能跟上的真实窗口节拍。
    """
    applied: list[int] = []
    cursor = 0
    first_stream_frames = 0
    for index in range(max_streams):
        if index == 1 and recovery_interval_sec is not None:
            probe.plan = _burst_plan(recovery_interval_sec)
        headers = _chat_headers()
        if cursor:
            headers = headers | {"Last-Event-ID": str(cursor)}
        async with client.stream("GET", EVENTS_URL, headers=headers) as response:
            assert response.status_code == 200, await response.aread()
            if index == 0 and stall_first_sec:
                await asyncio.sleep(stall_first_sec)
            text = await _read_all(response)
        frames = [frame for frame in _parse_sse(text) if not frame.is_heartbeat]
        if index == 0:
            first_stream_frames = len(frames)
        if not frames:
            break
        applied.extend(int(frame.cursor or 0) for frame in frames)
        cursor = applied[-1]
        if cursor >= total_frames:
            break
    return applied, first_stream_frames


async def test_b261_slow_consumer_is_closed_within_the_subscriber_buffer_and_recovers_by_cursor(
    stub_console: StubConsole,
    console_probe: _ConsoleProbe,
    runtime_probe: _RuntimeProbe,
) -> None:
    """慢消费者在单订阅缓冲到界时被显式断开；按持久游标重连补齐区间，不丢不重、不重建 Run。"""
    runtime_probe.plan = _burst_plan(BURST_STALL_INTERVAL_SEC)
    async with _gateway_env(
        stub_console, runtime_probe, event_max_bytes=BUFFER_TEST_EVENT_MAX_BYTES
    ) as env:
        async with _chat_client(env) as client:
            applied, first_stream_frames = await _consume_with_cursor_recovery(
                client,
                probe=runtime_probe,
                total_frames=BURST_FRAMES,
                stall_first_sec=1.0,
                recovery_interval_sec=BURST_RECOVERY_INTERVAL_SEC,
            )

    # 慢消费者必须在预算内被断开（第一条流远没读到终态），随后按游标补齐。
    assert 0 < first_stream_frames < BURST_FRAMES
    # 不丢不重：整个会话恰好覆盖 {1..终态}，重连从游标之后开始。
    assert applied == list(range(1, BURST_FRAMES + 1)), (first_stream_frames, len(applied))
    # 缓冲有界：单订阅从未超过配置上限，且关闭是显式的（计数可见）。
    stats = env.ledger.stats()
    assert stats.max_pending_bytes <= env.subscriber_buffer_bytes
    assert stats.slow_subscriber_closed >= 1
    # 订阅退出后不占进程预算（慢消费者被断开也不留占用）。
    assert stats.active_subscribers == 0 and stats.total_pending_bytes == 0
    # 慢消费者断开不重建 Run：只重订阅同一 Run（无 POST /v1/runs），游标单调前进。
    assert runtime_probe.calls["/v1/runs"] == []
    after_seqs = [int(call.query["after_seq"]) for call in runtime_probe.event_calls()]
    assert after_seqs[0] == 0 and after_seqs == sorted(set(after_seqs))
    assert len(after_seqs) >= 2, "慢消费者至少经历一次按游标重连"
    assert set(after_seqs[1:]) <= set(applied), "重连游标必须是客户端真实应用过的 seq"


async def test_b261_process_total_buffer_closes_slow_subscribers_without_losing_the_body(
    stub_console: StubConsole,
    console_probe: _ConsoleProbe,
    runtime_probe: _RuntimeProbe,
) -> None:
    """进程总缓冲也有界：两个慢订阅并发时按总预算关闭最慢的一个，正文仍可按游标恢复。"""
    runtime_probe.plan = _burst_plan(BURST_STALL_INTERVAL_SEC)
    budget = SUBSCRIBER_BUFFER_BYTES
    async with _gateway_env(
        stub_console,
        runtime_probe,
        event_max_bytes=BUFFER_TEST_EVENT_MAX_BYTES,
        subscriber_buffer_bytes=budget,
        total_buffer_bytes=budget,
    ) as env:
        async with _chat_client(env) as first, _chat_client(env) as second:
            async with (
                first.stream("GET", EVENTS_URL, headers=_chat_headers()) as first_response,
                second.stream("GET", EVENTS_URL, headers=_chat_headers()) as second_response,
            ):
                assert first_response.status_code == 200 and second_response.status_code == 200
                await asyncio.sleep(1.0)
                first_text = await _read_all(first_response)
                second_text = await _read_all(second_response)
            first_frames = [frame for frame in _parse_sse(first_text) if not frame.is_heartbeat]
            second_frames = [frame for frame in _parse_sse(second_text) if not frame.is_heartbeat]
            # 两个订阅都在预算内被断开；正文不丢：各自按游标重连即可补齐。
            assert first_frames and second_frames
            assert int(first_frames[-1].cursor or 0) < BURST_FRAMES
            assert int(second_frames[-1].cursor or 0) < BURST_FRAMES
            recovered, _ = await _consume_with_cursor_recovery(
                first,
                probe=runtime_probe,
                total_frames=BURST_FRAMES,
                recovery_interval_sec=BURST_RECOVERY_INTERVAL_SEC,
                max_streams=10,
            )

    stats = env.ledger.stats()
    assert stats.max_total_pending_bytes <= env.total_buffer_bytes
    assert stats.slow_subscriber_closed + stats.total_buffer_closed >= 1
    assert stats.total_buffer_closed >= 1, "总预算必须在两个慢订阅并发时生效"
    assert stats.active_subscribers == 0 and stats.total_pending_bytes == 0
    assert recovered == list(range(1, BURST_FRAMES + 1)), len(recovered)
    assert runtime_probe.calls["/v1/runs"] == []


# --- B-261：坏帧/超限帧的显式关闭（不静默丢帧） ------------------------------------------------


async def test_b261_malformed_public_payload_closes_the_subscription_explicitly(
    stub_console: StubConsole,
    console_probe: _ConsoleProbe,
    runtime_probe: _RuntimeProbe,
) -> None:
    """已知公开事件的坏载荷：显式关闭订阅（计数可见），不伪装成过滤、不吞掉。"""
    runtime_probe.plan = _StreamPlan(
        frames=[
            (3, "message.delta", {"reply_generation": 0}),
            (5, "message.delta", {"delta": "不该到达", "reply_generation": 0}),
        ],
        watermark=5,
    )
    async with _gateway_env(stub_console, runtime_probe) as env:
        async with _chat_client(env) as client:
            response = await client.get(EVENTS_URL, headers=_chat_headers())
            body = response.text
    assert response.status_code == 200
    frames = [frame for frame in _parse_sse(body) if not frame.is_heartbeat]
    assert frames == [], "坏帧不得进入公开流"
    assert env.ledger.stats().malformed_closed == 1
    assert env.ledger.stats().frames_sent == 0


async def test_b261_oversized_non_shardable_frame_closes_explicitly_without_truncation(
    stub_console: StubConsole,
    console_probe: _ConsoleProbe,
    runtime_probe: _RuntimeProbe,
) -> None:
    """无法分片的超限帧（大增量）：显式关闭并计数，绝不截断或静默丢弃。"""
    runtime_probe.plan = _StreamPlan(
        frames=[
            (3, "message.delta", {"delta": "z" * (EVENT_MAX_BYTES * 2), "reply_generation": 0}),
            (5, "message.delta", {"delta": "不该到达", "reply_generation": 0}),
        ],
        watermark=5,
    )
    async with _gateway_env(stub_console, runtime_probe) as env:
        async with _chat_client(env) as client:
            response = await client.get(EVENTS_URL, headers=_chat_headers())
            body = response.text
    frames = [frame for frame in _parse_sse(body) if not frame.is_heartbeat]
    assert frames == [], "超限帧不得以截断形式下发"
    assert env.ledger.stats().oversized_closed == 1


# --- B-261：网关不持库与公开词表 --------------------------------------------------------------


def test_b261_stream_modules_hold_no_database_and_only_speak_the_public_contract() -> None:
    """网关不持库（零 sqlalchemy）；公开流只使用 TASK-006 契约里的公开事件词表。"""
    root = Path(__file__).resolve().parents[2]
    for relative in (
        "apps/im-gateway/src/muad_im_gateway/api/webchat.py",
        "apps/im-gateway/src/muad_im_gateway/application/webchat_streams.py",
    ):
        source = (root / relative).read_text(encoding="utf-8")
        assert "sqlalchemy" not in source, relative
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all("sqlalchemy" not in alias.name for alias in node.names), relative
            elif isinstance(node, ast.ImportFrom):
                assert "sqlalchemy" not in (node.module or ""), relative

    from muad_im_gateway.application import webchat_streams
    from muad_im_gateway.application.sse import KNOWN_EVENT_TYPES

    mapped = set(webchat_streams.PUBLIC_STREAM_EVENT_TYPES.values())
    assert mapped <= {
        "message.delta",
        "message.snapshot",
        "run.stage",
        "run.terminal",
        "run.error",
        "artifact.created",
        "confirmation.required",
    }
    internal = {
        "tool.completed",
        "tool.started",
        "tool.result",
        "model.started",
        "model.completed",
        "reasoning.delta",
        "run.created",
    }
    assert internal.isdisjoint(webchat_streams.PUBLIC_STREAM_EVENT_TYPES)
    # 公开词表的 canonical 键必须先被 Owner 流解析器认识：否则公开事件会被解析层静默丢弃。
    assert set(webchat_streams.PUBLIC_STREAM_EVENT_TYPES) <= KNOWN_EVENT_TYPES
