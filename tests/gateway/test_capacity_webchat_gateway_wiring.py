"""B-296：Gateway 装配 HTTP 聊天、SSE 及四类生命周期（design §3.2/§3.7/§4.1；TASK-096）。

真实边界（不 mock 装配、路由、预算与生命周期）：

- **真实 Gateway 装配根**：`muad_im_gateway.main.lifespan`（真实 `validate_startup`、真实
  `build_channel_registry`、真实 Console/Runtime HTTP 客户端、真实分池、真实 uvicorn socket）；
- **真实 Console HTTP（Owner）**：`ConsoleClient`/`ConsolePlatformSettingsClient` 打真实本地
  uvicorn（真实 socket），按 API-12/API-19 的内部端点应答（chat-principal / bots /
  platform-settings）；
- **真实 Runtime HTTP/SSE（Owner）**：`RuntimeClient` 打真实本地 uvicorn；受理裁决在
  `text/event-stream` 响应头里一次带回（Run/会话/状态/阶段），公开 SSE 是真实持久帧流
  （含心跳与保持打开），游标按真实窗口裁决；
- **真实企微协议探针**：官方 SDK 经真实 WSS 连接本地探针（`WeComProbe`），单机器人连接的
  建立/替换/回收都是真实 socket 事实；
- **数据库经 Owner 接口访问**：本文件不碰任何数据库，Gateway 零 sqlalchemy（源码扫描 + 真实
  lifespan 只校验配置、从不连接 DB）。

关键断言（B-296 契约）：

1. **生产 app 实际注册 Webchat/企微、身份复核和公开 SSE**：路由表与依赖图（`ChatIdentityDep`/
   `ChatStreamGuardDep`）真实存在，真实 HTTP 请求（client-config / entries / SSE）走通；
2. **注入独立控制/stream/上传/业务预算**：四个池由装配根建、预算唯一来源是启动设置、调用方
   消费同一实例、退出即关；本进程不建的池（model/mcp）显式失败；
3. **四类生命周期**（机器人连接/快照刷新/入站消费/公开 SSE 订阅）各自独立成任务、退出即停，
   启动失败也收尾；
4. **停服排空不重建 Run**：持有订阅时关服 → 订阅被排空、账本归零、重启后重连只做 GET 重放，
   受理 POST 始终恰一次；
5. **单机器人无连接重叠**：快照换密钥先停旧连接再起新连接（同一 bot 至多一条活连接），退出全关；
6. **SSE 订阅上限显式拒绝**：到 `GATEWAY_MAX_SSE_SUBSCRIBERS` 的新订阅在打开 Owner 流之前被
   显式 429 拒绝（不静默丢、不占上游连接），释放后可再订。
"""

from __future__ import annotations

import ast
import asyncio
import json
import socket
import threading
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

import httpx
import pytest
import redis.asyncio as redis
import uvicorn
from fakes import StubConsole
from fastapi import Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from muad_api.error_codes import ErrorCode
from muad_common import SharedSettings
from muad_common.session_store import (
    SessionAgent,
    SessionRecord,
    hash_session_token,
    session_key,
)
from muad_contracts.webchat import (
    ADMISSION_CONVERSATION_ID_HEADER,
    ADMISSION_EXECUTION_STATUS_HEADER,
    ADMISSION_RESUMED_HEADER,
    ADMISSION_RUN_ID_HEADER,
    ADMISSION_RUN_STATUS_HEADER,
)
from muad_im_gateway.application.chat_principal import CHAT_SESSION_COOKIE
from muad_im_gateway.application.console_client import CHAT_CONTEXT_HEADER, CHAT_PRINCIPAL_PATH
from muad_im_gateway.channels.webchat import WebchatChannelAdapter
from muad_im_gateway.channels.webhook import WebhookChannelAdapter
from muad_im_gateway.infrastructure.http_clients import (
    GATEWAY_HTTP_POOLS,
    GatewayHttpPools,
    pool_limits,
    pool_timeout,
)
from muad_im_gateway.main import app

from tests.e2e.wecom_probe_app import WeComProbe
from tests.internal_service import TOKEN as INTERNAL_TOKEN
from tests.internal_service import internal_service_token  # noqa: F401  (fixture 注册)

pytestmark = pytest.mark.integration

GATEWAY_TENANT = "tenant-b296"
PLATFORM_USER_ID = UUID("7d2f9b31-4c8a-4e5d-9f10-6a3b8c2d4e51")
SESSION_ID = UUID("c1a4e8f2-6b30-4d7a-8e59-0f2b3c4d5e61")
AGENT_ID = UUID("a1b2c3d4-5e6f-4a7b-8c9d-0e1f2a3b4c5d")
ENTRY_ID = UUID("e1f2a3b4-c5d6-4e7f-8a9b-0c1d2e3f4a5b")
CONVERSATION_ID = UUID("b4c5d6e7-f8a9-4b0c-9d1e-2f3a4b5c6d7e")
RUN_ID = UUID("c5d6e7f8-a9b0-4c1d-8e2f-3a4b5c6d7e8f")
CHAT_CONTEXT = SESSION_ID
#: CHAT 会话原文 canary：只允许出现在发往 Console 的核验请求体里。
SESSION_TOKEN = f"chat-session-{uuid4().hex}"
#: 内部内容 canary：内部事件不得进入公开 SSE。
INTERNAL_CANARY = "internal-canary-b296-tool-arguments"

BOTS_PATH = "/internal/channel/bots"
PLATFORM_SETTINGS_PATH = "/internal/v1/platform-settings"
AGENT_NAME = "网页助手"
WEBCHAT_BOT_ID = "webchat-entry-b296"
WECOM_BOT_ID = "bot-b296-wecom"
WECOM_SECRET_1 = "b296-secret-1"
WECOM_SECRET_2 = "b296-secret-2"
SETTINGS_REVISION = 7

#: 启动预算（唯一来源是 SharedSettings；装配根不复制默认值）。
GATEWAY_MAX_CONCURRENT_HANDLERS = 3
GATEWAY_MAX_SSE_SUBSCRIBERS = 1100
#: 排队上限（全局/每路由/命令）：与模块常量默认不同，用来钉住装配根真的注入了设置值。
PENDING_LIMITS = (7, 3, 5)
CONTROL_LIMITS = (6, 3)
STREAM_LIMITS = (9, 4)
UPLOAD_LIMITS = (5, 2)
EGRESS_LIMITS = (8, 4)
CONTROL_READ_SEC = 7.0
STREAM_READ_SEC = 11.0
UPLOAD_READ_SEC = 13.0
EGRESS_READ_SEC = 17.0
CONNECT_TIMEOUT_SEC = 4.0
POOL_TIMEOUT_SEC = 3.0
EVENT_MAX_BYTES = 4096
#: 心跳是整秒 typed 字段（SharedSettings）；断言值同源。
RUN_EVENT_HEARTBEAT_SEC = 1
HEARTBEAT_SEC = float(RUN_EVENT_HEARTBEAT_SEC)
SUBSCRIBER_BUFFER_BYTES = 65536
TOTAL_BUFFER_BYTES = 1048576
CHAT_MESSAGE_HARD_MAX_BYTES = 1048576
CHAT_UPLOAD_HARD_MAX_BYTES = 52428800
CHAT_AUTH_RECHECK_SEC = 60.0

#: 真实生命周期任务的装配根命名（四类生命周期的可观测面）。
SNAPSHOT_LOOP_NAME = "gateway.snapshot"
INBOUND_LOOP_NAMES = (
    "gateway.inbound.WECOM",
    "gateway.inbound.WEBCHAT",
    "gateway.inbound.stream",
)

WAIT_TIMEOUT_SEC = 20.0
HTTP_TIMEOUT_SEC = 10.0


# --------------------------------------------------------------------------------------
# 真实 HTTP 封套与 SSE 帧（与 api-kit / Runtime SseEmitter 同形）
# --------------------------------------------------------------------------------------


def _envelope(data: Any = None) -> dict[str, Any]:
    return {
        "code": "0",
        "msg": "ok",
        "data": data,
        "trace_id": "trace-b296",
        "request_id": "request-b296",
        "timestamp": "2026-10-18T07:00:00Z",
    }


def _json(data: Any, status_code: int = 200) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=_envelope(data))


def _error(code: str, status_code: int) -> JSONResponse:
    body = _envelope()
    body["code"] = code
    body["msg"] = code
    return JSONResponse(status_code=status_code, content=body)


def _canonical_frame(
    event_type: str, data: dict[str, Any], *, seq: int | None = None, run_id: UUID | None = None
) -> str:
    payload: dict[str, Any] = {
        "type": event_type,
        "data": data,
        "timestamp": "2026-10-18T07:00:00Z",
    }
    if seq is not None:
        payload["seq"] = seq
    if run_id is not None:
        payload["run_id"] = str(run_id)
    return f"event: {event_type}\ndata: {json.dumps(payload)}\n\n"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _wait_for(
    predicate: Callable[[], bool], *, message: str, timeout: float = WAIT_TIMEOUT_SEC
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"condition not met within {timeout}s: {message}")


def _loop_tasks(names: tuple[str, ...]) -> dict[str, asyncio.Task[Any]]:
    found: dict[str, asyncio.Task[Any]] = {}
    for task in asyncio.all_tasks():
        if task.get_name() in names:
            found[task.get_name()] = task
    return found


def _effective_pool_limits(client: httpx.AsyncClient) -> tuple[int, int]:
    """httpx 不暴露池预算：从真实 transport 的 httpcore 池读**有效值**（装配断言的直接证据）。"""
    pool = client._transport._pool  # type: ignore[attr-defined]
    return pool._max_connections, pool._max_keepalive_connections  # type: ignore[attr-defined]


def _dependency_names(dependant: Any) -> set[str]:
    """FastAPI 依赖图里的全部调用名（身份复核/长流复核是否真的挂在路由上）。"""
    names: set[str] = set()

    def walk(node: Any) -> None:
        call = getattr(node, "call", None)
        if call is not None:
            names.add(getattr(call, "__name__", str(call)))
        for child in getattr(node, "dependencies", ()):  # pragma: no branch - 图是有限的
            walk(child)

    walk(dependant)
    return names


def _effective_routes() -> list[Any]:
    """生产 app 的有效路由：FastAPI 0.141 的 `include_router` 惰性展开（`_IncludedRouter`）。"""
    routes: list[Any] = []
    for route in app.routes:
        expand = getattr(route, "effective_route_contexts", None)
        if expand is not None:
            routes.extend(expand())
        else:
            routes.append(route)
    return routes


def _route_methods(path: str) -> set[str]:
    methods: set[str] = set()
    for route in _effective_routes():
        if getattr(route, "path", None) == path:
            methods |= {str(method) for method in getattr(route, "methods", set())}
    if not methods:
        raise AssertionError(f"route not registered: {path}")
    return methods


def _route_dependencies(path: str, method: str) -> set[str]:
    for route in _effective_routes():
        if getattr(route, "path", None) != path:
            continue
        if method not in {str(item) for item in getattr(route, "methods", set())}:
            continue
        return _dependency_names(route.dependant)
    raise AssertionError(f"route not registered: {method} {path}")


# --------------------------------------------------------------------------------------
# 真实 Console Owner 桩（真实 socket + 真实封套）
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _PrincipalCall:
    headers: dict[str, str]
    body: dict[str, Any]


class _ConsoleProbe:
    """API-12/API-19 真实 HTTP 桩：bots + chat-principal + platform-settings；记录每次调用。"""

    def __init__(self, stub: StubConsole) -> None:
        self.principal_calls: list[_PrincipalCall] = []
        self.settings_calls: list[dict[str, str]] = []
        self.bots_calls: list[dict[str, str]] = []
        self.agents: list[dict[str, Any]] = [
            {"agent_id": str(AGENT_ID), "key": "b296-agent", "name": AGENT_NAME}
        ]
        self.items: list[dict[str, Any]] = [
            _bot_payload(WEBCHAT_BOT_ID, None, channel="WEBCHAT", account_id=ENTRY_ID),
        ]
        self.revision = "rev-b296-1"
        stub.handlers[BOTS_PATH] = self._bots
        stub.handlers[CHAT_PRINCIPAL_PATH] = self._principal
        stub.handlers[PLATFORM_SETTINGS_PATH] = self._settings

    async def _bots(self, request: Request) -> Response:
        self.bots_calls.append(dict(request.headers))
        return _json(
            {
                "revision": self.revision,
                "items": self.items,
                "page": 1,
                "page_size": 20,
                "total": len(self.items),
            }
        )

    async def _principal(self, request: Request) -> Response:
        body = json.loads((await request.body()) or b"{}")
        self.principal_calls.append(_PrincipalCall(headers=dict(request.headers), body=body))
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

    async def _settings(self, request: Request) -> Response:
        self.settings_calls.append(dict(request.headers))
        return _json(
            {
                "revision": SETTINGS_REVISION,
                "settings": {
                    "webchat": {
                        "history_page_size": 50,
                        "text_max_bytes": 8192,
                        "upload_max_bytes": 123456,
                        "upload_max_per_message": 5,
                        "notification_poll_sec": 5,
                        "reconnect_base_ms": 500,
                        "reconnect_cap_ms": 30000,
                        "reconnect_max_attempts": 10,
                    }
                },
            }
        )


def _bot_payload(
    bot_id: str,
    secret: str | None,
    *,
    channel: str = "WECOM",
    account_id: UUID | None = None,
) -> dict[str, Any]:
    return {
        "bot_account_id": str(account_id or uuid4()),
        "bot_id": bot_id,
        "channel": channel,
        "secret": secret,
        "agent_id": str(AGENT_ID),
        "enabled": True,
    }


# --------------------------------------------------------------------------------------
# 真实 Runtime Owner 桩（真实 socket + 真实 SSE）
# --------------------------------------------------------------------------------------


@dataclass
class _StreamPlan:
    """一次 Owner 事件流脚本：真实持久帧 + 是否保持打开（活跃 Run）。"""

    frames: list[tuple[int, str, dict[str, Any]]]
    watermark: int
    hold_open_sec: float = 0.0


@dataclass
class _RuntimeProbe:
    """Runtime Owner 端点桩：真实 HTTP/SSE；记录每次调用（含受理 POST 与事件 GET）。"""

    plan: _StreamPlan = field(default_factory=lambda: _StreamPlan(frames=[], watermark=0))
    run_posts: list[dict[str, Any]] = field(default_factory=list)
    event_calls: list[dict[str, Any]] = field(default_factory=list)
    get_run_calls: int = 0
    scope_calls: int = 0
    _app: Any = None
    _server: uvicorn.Server | None = None
    _thread: Any = None
    url: str = ""

    def build(self) -> None:
        from fastapi import FastAPI

        runtime_app = FastAPI()

        @runtime_app.post("/v1/runs")
        async def _create_run(request: Request) -> Response:
            self.run_posts.append(json.loads((await request.body()) or b"{}"))
            return StreamingResponse(
                _admission_stream(),
                media_type="text/event-stream",
                headers={
                    ADMISSION_RUN_ID_HEADER: str(RUN_ID),
                    ADMISSION_CONVERSATION_ID_HEADER: str(CONVERSATION_ID),
                    ADMISSION_RESUMED_HEADER: "0",
                    ADMISSION_RUN_STATUS_HEADER: "RUNNING",
                    ADMISSION_EXECUTION_STATUS_HEADER: json.dumps(
                        {"stage": "RUNNING"}, ensure_ascii=True
                    ),
                },
            )

        @runtime_app.get("/v1/runs/{run_id}")
        async def _get_run(run_id: str, request: Request) -> Response:
            self.get_run_calls += 1
            return _json(
                {
                    "run_id": run_id,
                    "conversation_id": str(CONVERSATION_ID),
                    "agent_id": str(AGENT_ID),
                    "user_id": str(PLATFORM_USER_ID),
                    "status": "RUNNING",
                    "execution_status": {"stage": "RUNNING"},
                    "cancel_requested": False,
                }
            )

        @runtime_app.get("/v1/runs/{run_id}/scope")
        async def _run_scope(run_id: str, request: Request) -> Response:
            self.scope_calls += 1
            return _json(
                {
                    "run": {
                        "run_id": run_id,
                        "conversation_id": str(CONVERSATION_ID),
                        "agent_id": str(AGENT_ID),
                        "user_id": str(PLATFORM_USER_ID),
                        "status": "RUNNING",
                        "execution_status": {"stage": "RUNNING"},
                        "cancel_requested": False,
                    },
                    "conversation": {
                        "conversation_id": str(CONVERSATION_ID),
                        "entry_id": str(ENTRY_ID),
                        "agent_id": str(AGENT_ID),
                    },
                }
            )

        @runtime_app.get("/internal/webchat/conversations/{conversation_id}/scope")
        async def _scope(conversation_id: str, request: Request) -> Response:
            self.scope_calls += 1
            return _json(
                {
                    "conversation_id": conversation_id,
                    "entry_id": str(ENTRY_ID),
                    "agent_id": str(AGENT_ID),
                }
            )

        runtime_app.get("/v1/runs/{run_id}/events")(self._events_response)
        self._app = runtime_app

    async def _events_response(self, run_id: str, request: Request) -> Response:
        after_seq = int(request.query_params.get("after_seq", "0"))
        call = {"after_seq": after_seq, "headers": dict(request.headers), "run_id": run_id}
        self.event_calls.append(call)
        if after_seq > self.plan.watermark:
            return _error("CURSOR_AHEAD", 400)
        return StreamingResponse(
            self._event_stream(after_seq, call), media_type="text/event-stream"
        )

    async def _event_stream(
        self, after_seq: int, call: dict[str, Any]
    ) -> AsyncIterator[str]:
        sent = 0
        try:
            for seq, event_type, payload in self.plan.frames:
                if seq <= after_seq:
                    continue
                sent += 1
                yield _canonical_frame(event_type, payload, seq=seq, run_id=RUN_ID)
            deadline = time.monotonic() + self.plan.hold_open_sec
            while time.monotonic() < deadline:
                await asyncio.sleep(0.05)
                yield ": heartbeat\n\n"
        finally:
            call["sent"] = sent

    def start(self) -> None:
        self.build()
        config = uvicorn.Config(
            self._app, host="127.0.0.1", port=_free_port(), log_level="error"
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 15
        while not self._server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        if not self._server.started:
            raise RuntimeError("runtime probe did not start")
        self.url = f"http://127.0.0.1:{config.port}"

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)


async def _admission_stream() -> AsyncIterator[str]:
    """受理帧：真实 SSE（受理裁决已在响应头，Gateway 读到响应头即返回 202，不持长流）。"""
    yield _canonical_frame(
        "run.created",
        {"run_id": str(RUN_ID), "conversation_id": str(CONVERSATION_ID), "resumed": False},
        run_id=RUN_ID,
    )


# --------------------------------------------------------------------------------------
# 夹具：真实 Gateway 环境（真实 lifespan / 真实 uvicorn / 真实协议探针）
# --------------------------------------------------------------------------------------


def _isolated_redis_url() -> str:
    """测试专用 Redis DB 9：不碰 `.env` 的 0 号库，也不依赖不可达端口。"""
    parts = urlsplit(SharedSettings().require_redis_url())
    return urlunsplit((parts.scheme, parts.netloc, "/9", parts.query, parts.fragment))


async def _flush_redis(redis_url: str) -> None:
    client = redis.from_url(redis_url)
    try:
        await client.flushdb()
    finally:
        await client.aclose()


async def _seed_chat_session(redis_url: str) -> None:
    """按 Console exchange 的写入口径预置会话，验证 Gateway 只读 Redis、不回源 Console。"""
    client = redis.from_url(redis_url)
    try:
        now = datetime.now(UTC)
        record = SessionRecord(
            tenant_id=GATEWAY_TENANT,
            platform_user_id=PLATFORM_USER_ID,
            session_id=SESSION_ID,
            auth_method="USER_TOKEN",
            expires_at=now + timedelta(hours=1),
            idle_ttl_sec=3600,
            agents=(SessionAgent(agent_id=AGENT_ID, key="b296-agent", name=AGENT_NAME),),
        )
        await client.set(
            session_key(hash_session_token(SESSION_TOKEN)), record.to_json(), ex=3600
        )
    finally:
        await client.aclose()


@asynccontextmanager
async def _running_gateway() -> AsyncIterator[str]:
    """真实 Gateway：真实 uvicorn socket + 真实 lifespan（四类生命周期都在跑）。"""
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=_free_port(),
        log_level="error",
        timeout_graceful_shutdown=5,
    )
    server = uvicorn.Server(config)
    serving = asyncio.create_task(server.serve())
    try:
        await _wait_for(lambda: server.started, message="gateway 未在超时内监听")
        yield f"http://127.0.0.1:{config.port}"
    finally:
        server.should_exit = True
        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(serving, timeout=15)


def _install_gateway_env(
    monkeypatch: pytest.MonkeyPatch,
    *,
    console_url: str,
    runtime_url: str,
    artifact_root: Path,
    wecom_probe: WeComProbe | None = None,
    redis_url: str = "redis://127.0.0.1:1/0",
    **extra: str,
) -> None:
    """真实配置入口：Gateway 只从环境读配置，lifespan 启动时快照这些值。"""
    artifact_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ARTIFACT_ROOT", str(artifact_root))
    monkeypatch.setenv("CONSOLE_PLATFORM_URL", console_url)
    monkeypatch.setenv("AGENT_RUNTIME_URL", runtime_url)
    # 单租户部署：网关的部署租户与 Console 返回的主体租户必须一致（门控在这里比对）。
    monkeypatch.setenv("DEFAULT_TENANT_ID", GATEWAY_TENANT)
    # 网关不持库：DATABASE_URL 只过配置校验，指向不可达地址（从不连接）。
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://muad:muad@127.0.0.1:1/muad_b296")
    monkeypatch.setenv("REDIS_URL", redis_url)
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", INTERNAL_TOKEN)
    monkeypatch.setenv("CHANNEL_PROBE_URL", "")
    # 请求面通道：WEBCHAT（进程内）+ 配置驱动的 WECOM webhook（无长连接/无 bot secret）。
    monkeypatch.setenv(
        "GATEWAY_WEBHOOK_CHANNELS",
        '[{"channel": "WECOM", "secret": "b296-webhook-secret", "reply_url": null}]',
    )
    monkeypatch.setenv("WECOM_WS_URL", wecom_probe.ws_url if wecom_probe else "")
    monkeypatch.setenv(
        "WECOM_WS_CA_FILE", str(wecom_probe.cert_path) if wecom_probe else ""
    )
    monkeypatch.setenv("GATEWAY_MAX_CONCURRENT_HANDLERS", str(GATEWAY_MAX_CONCURRENT_HANDLERS))
    monkeypatch.setenv("GATEWAY_MAX_PENDING_HANDLERS", str(PENDING_LIMITS[0]))
    monkeypatch.setenv("GATEWAY_MAX_PENDING_PER_ROUTE", str(PENDING_LIMITS[1]))
    monkeypatch.setenv("GATEWAY_MAX_PENDING_COMMANDS", str(PENDING_LIMITS[2]))
    monkeypatch.setenv("GATEWAY_MAX_SSE_SUBSCRIBERS", str(GATEWAY_MAX_SSE_SUBSCRIBERS))
    monkeypatch.setenv("CONTROL_HTTP_MAX_CONNECTIONS", str(CONTROL_LIMITS[0]))
    monkeypatch.setenv("CONTROL_HTTP_MAX_KEEPALIVE", str(CONTROL_LIMITS[1]))
    monkeypatch.setenv("STREAM_HTTP_MAX_CONNECTIONS", str(STREAM_LIMITS[0]))
    monkeypatch.setenv("STREAM_HTTP_MAX_KEEPALIVE", str(STREAM_LIMITS[1]))
    monkeypatch.setenv("UPLOAD_HTTP_MAX_CONNECTIONS", str(UPLOAD_LIMITS[0]))
    monkeypatch.setenv("UPLOAD_HTTP_MAX_KEEPALIVE", str(UPLOAD_LIMITS[1]))
    monkeypatch.setenv("EGRESS_HTTP_MAX_CONNECTIONS", str(EGRESS_LIMITS[0]))
    monkeypatch.setenv("EGRESS_HTTP_MAX_KEEPALIVE", str(EGRESS_LIMITS[1]))
    monkeypatch.setenv("CONTROL_HTTP_READ_TIMEOUT_SEC", str(CONTROL_READ_SEC))
    monkeypatch.setenv("STREAM_HTTP_READ_TIMEOUT_SEC", str(STREAM_READ_SEC))
    monkeypatch.setenv("UPLOAD_HTTP_READ_TIMEOUT_SEC", str(UPLOAD_READ_SEC))
    monkeypatch.setenv("EGRESS_HTTP_READ_TIMEOUT_SEC", str(EGRESS_READ_SEC))
    monkeypatch.setenv("HTTP_CONNECT_TIMEOUT_SEC", str(CONNECT_TIMEOUT_SEC))
    monkeypatch.setenv("HTTP_POOL_TIMEOUT_SEC", str(POOL_TIMEOUT_SEC))
    monkeypatch.setenv("SSE_EVENT_MAX_BYTES", str(EVENT_MAX_BYTES))
    monkeypatch.setenv("RUN_EVENT_HEARTBEAT_SEC", str(RUN_EVENT_HEARTBEAT_SEC))
    monkeypatch.setenv("SSE_SUBSCRIBER_BUFFER_BYTES", str(SUBSCRIBER_BUFFER_BYTES))
    monkeypatch.setenv("SSE_TOTAL_BUFFER_BYTES", str(TOTAL_BUFFER_BYTES))
    monkeypatch.setenv("CHAT_MESSAGE_HARD_MAX_BYTES", str(CHAT_MESSAGE_HARD_MAX_BYTES))
    monkeypatch.setenv("CHAT_UPLOAD_HARD_MAX_BYTES", str(CHAT_UPLOAD_HARD_MAX_BYTES))
    monkeypatch.setenv("CHAT_AUTH_RECHECK_SEC", str(CHAT_AUTH_RECHECK_SEC))
    for name, value in extra.items():
        monkeypatch.setenv(name, value)


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
def runtime_probe() -> AsyncIterator[_RuntimeProbe]:
    probe = _RuntimeProbe()
    probe.start()
    try:
        yield probe
    finally:
        probe.stop()


@asynccontextmanager
async def _running_gateway() -> AsyncIterator[str]:
    """真实 Gateway：真实 uvicorn socket + 真实 lifespan（四类生命周期都在跑）。"""
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=_free_port(),
        log_level="error",
        timeout_graceful_shutdown=5,
    )
    server = uvicorn.Server(config)
    serving = asyncio.create_task(server.serve())
    try:
        await _wait_for(lambda: server.started, message="gateway 未在超时内监听")
        yield f"http://127.0.0.1:{config.port}"
    finally:
        server.should_exit = True
        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(serving, timeout=15)


def _chat_cookies() -> dict[str, str]:
    return {CHAT_SESSION_COOKIE: SESSION_TOKEN}


def _chat_headers() -> dict[str, str]:
    return {CHAT_CONTEXT_HEADER: str(CHAT_CONTEXT)}


async def _read_stream_text(stream: httpx.Response, *, timeout: float) -> str:
    """读取一段流直到连接关闭/超时（排空证据：关闭后必须立即结束，不能悬挂）。"""
    chunks: list[str] = []
    try:
        async with asyncio.timeout(timeout):
            async for chunk in stream.aiter_text():
                chunks.append(chunk)
    except (TimeoutError, httpx.HTTPError):
        pass
    return "".join(chunks)


# --------------------------------------------------------------------------------------
# 1. 生产 app 实际注册 Webchat/企微、身份复核与公开 SSE
# --------------------------------------------------------------------------------------


async def test_b296_production_app_registers_webchat_wecom_recheck_and_public_sse(
    stub_console: StubConsole,
    console_probe: _ConsoleProbe,
    runtime_probe: _RuntimeProbe,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """[B-296] 真实 lifespan：路由表/依赖图 + 真实 HTTP（client-config/entries/SSE）。"""
    probe = WeComProbe(expected_bots={WECOM_BOT_ID: WECOM_SECRET_1})
    await probe.start()
    console_probe.items = [
        _bot_payload(WEBCHAT_BOT_ID, None, channel="WEBCHAT", account_id=ENTRY_ID),
        _bot_payload(WECOM_BOT_ID, WECOM_SECRET_1),
    ]
    runtime_probe.plan = _StreamPlan(
        frames=[(1, "message.delta", {"reply_generation": 0, "delta": "你好"})],
        watermark=1,
    )
    redis_url = _isolated_redis_url()
    await _flush_redis(redis_url)
    await _seed_chat_session(redis_url)
    _install_gateway_env(
        monkeypatch,
        console_url=stub_console.url,
        runtime_url=runtime_probe.url,
        artifact_root=tmp_path / "artifacts",
        wecom_probe=probe,
        redis_url=redis_url,
    )
    try:
        async with _running_gateway() as env:
            # ① 生产 app 实际注册：路由表 + 依赖图（身份复核/长流复核）。
            assert _route_methods("/api/v1/webchat/client-config") == {"GET"}
            assert _route_methods("/api/v1/webchat/entries") == {"GET"}
            assert _route_methods("/api/v1/webchat/entries/{entry_id}/messages") == {"POST"}
            assert _route_methods("/api/v1/webchat/entries/{entry_id}/conversations") == {
                "GET",
                "POST",
            }
            assert _route_methods("/api/v1/webchat/entries/{entry_id}/attachments") == {"POST"}
            assert _route_methods("/api/v1/webchat/conversations/{conversation_id}") == {"GET"}
            assert _route_methods("/api/v1/webchat/conversations/{conversation_id}/messages") == {
                "GET"
            }
            assert _route_methods("/api/v1/webchat/runs/{run_id}/events") == {"GET"}
            assert _route_methods("/api/v1/webchat/runs/{run_id}/cancel") == {"POST"}
            assert _route_methods("/api/v1/webchat/runs/{run_id}/resume") == {"POST"}
            assert _route_methods("/api/v1/webchat/artifacts/{artifact_id}/content") == {"GET"}
            assert "get_chat_identity" in _route_dependencies(
                "/api/v1/webchat/entries", "GET"
            ), "读写面必须经 ChatIdentityDep 收口主体"
            stream_deps = _route_dependencies("/api/v1/webchat/runs/{run_id}/events", "GET")
            assert "get_chat_identity" in stream_deps, "SSE 也要经主体门控"
            assert "get_chat_stream_guard" in stream_deps, "长流必须挂复核定时器"

            # ② 真实通道注册：WEBCHAT 稳定入口 + 配置驱动的通用 webhook（无长连接/secret）。
            registry = app.state.registry
            assert registry.names() == ("WEBCHAT", "WECOM")
            assert isinstance(registry.get("WEBCHAT"), WebchatChannelAdapter)
            assert isinstance(registry.get("WECOM"), WebhookChannelAdapter)
            assert registry.adapter_states == {"WEBCHAT": True, "WECOM": True}
            assert probe.connections == [], "请求面不得建立渠道长连接"

            # ③ 真实 HTTP：client-config（设置源 + 传输预算装配根注入）。
            async with httpx.AsyncClient(base_url=env, timeout=HTTP_TIMEOUT_SEC) as client:
                config = await client.get(
                    "/api/v1/webchat/client-config",
                    cookies=_chat_cookies(),
                    headers=_chat_headers(),
                )
                assert config.status_code == 200, config.text
                transport = config.json()["data"]["transport"]
                assert transport["sse_event_max_bytes"] == EVENT_MAX_BYTES
                assert transport["sse_heartbeat_sec"] == HEARTBEAT_SEC
                assert config.json()["data"]["settings_revision"] == SETTINGS_REVISION

                # ④ 真实 HTTP：entries（入口快照 + 部署租户装配根注入）。
                entries = await client.get(
                    "/api/v1/webchat/entries",
                    cookies=_chat_cookies(),
                    headers=_chat_headers(),
                )
                assert entries.status_code == 200, entries.text
                items = entries.json()["data"]["items"]
                assert [item["entry_id"] for item in items] == [str(ENTRY_ID)]
                assert items[0]["display_name"] == AGENT_NAME

                # ⑤ 真实 HTTP/SSE：公开事件流（Runtime 客户端 + 进程账本装配根注入）。
                async with client.stream(
                    "GET",
                    f"/api/v1/webchat/runs/{RUN_ID}/events",
                    cookies=_chat_cookies(),
                    headers=_chat_headers(),
                ) as stream:
                    assert stream.status_code == 200, stream.text
                    assert stream.headers["content-type"].startswith("text/event-stream")
                    body = await _read_stream_text(stream, timeout=HTTP_TIMEOUT_SEC)
            assert "event: message.delta" in body
            assert INTERNAL_CANARY not in body

            # ⑥ 身份门控真实发生：Redis 会话命中后只读本地存储，不再逐请求回源 Console。
            assert console_probe.principal_calls == [], "Redis 命中时不得回源 Console"
            assert console_probe.bots_calls, "入口快照未经 Console 读取"
            # ⑦ 公开 SSE 的 Owner 调用：GET 重放，绝不 POST 重建 Run。
            assert runtime_probe.run_posts == []
            assert runtime_probe.event_calls
            assert runtime_probe.event_calls[0]["after_seq"] == 0
            assert runtime_probe.event_calls[0]["run_id"] == str(RUN_ID)
            # ⑧ 订阅生命周期：流结束后账本归零（无泄漏的活跃订阅）。
            await _wait_for(
                lambda: app.state.webchat_sse_ledger.stats().active_subscribers == 0,
                message="订阅结束后账本未归零",
            )
    finally:
        await probe.stop()
        await _flush_redis(redis_url)


def test_b296_gateway_holds_no_database() -> None:
    """[B-296] 网关零 sqlalchemy 依赖（全部源码的导入面，不只是路由/流两个模块）。"""
    root = Path(__file__).resolve().parents[2]
    package = root / "apps/im-gateway/src/muad_im_gateway"
    sources = sorted(package.rglob("*.py"))
    assert sources
    for path in sources:
        source = path.read_text(encoding="utf-8")
        assert "sqlalchemy" not in source, path
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all("sqlalchemy" not in alias.name for alias in node.names), path
            elif isinstance(node, ast.ImportFrom):
                assert "sqlalchemy" not in (node.module or ""), path


# --------------------------------------------------------------------------------------
# 2. 装配根注入分池预算、受理上限与四类生命周期任务
# --------------------------------------------------------------------------------------


async def test_b296_assembly_root_injects_pools_handler_limit_and_lifecycle_loops(
    stub_console: StubConsole,
    console_probe: _ConsoleProbe,
    runtime_probe: _RuntimeProbe,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """[B-296] 真实 lifespan：四池预算/受理上限来自启动设置，四类生命周期独立、退出即停。"""
    console_probe.items = [
        _bot_payload(WEBCHAT_BOT_ID, None, channel="WEBCHAT", account_id=ENTRY_ID)
    ]
    _install_gateway_env(
        monkeypatch,
        console_url=stub_console.url,
        runtime_url=runtime_probe.url,
        artifact_root=tmp_path / "artifacts",
    )
    settings = SharedSettings()
    baseline = set(asyncio.all_tasks())
    async with app.router.lifespan_context(app):
        pools = app.state.http_pools
        assert isinstance(pools, GatewayHttpPools)
        assert len({id(pools.control), id(pools.stream), id(pools.upload), id(pools.egress)}) == 4, (
            "控制/stream/上传/业务各自建池，不共享连接"
        )

        # 有效池大小 = 启动设置值（装配根不复制默认值；两处派生自同一 settings）
        for pool_name in GATEWAY_HTTP_POOLS:
            client = getattr(pools, pool_name)
            derived = pool_limits(settings, pool_name)  # type: ignore[arg-type]
            assert _effective_pool_limits(client) == (
                derived.max_connections,
                derived.max_keepalive_connections,
            )
        assert _effective_pool_limits(pools.control) == CONTROL_LIMITS
        assert _effective_pool_limits(pools.stream) == STREAM_LIMITS
        assert _effective_pool_limits(pools.upload) == UPLOAD_LIMITS
        assert _effective_pool_limits(pools.egress) == EGRESS_LIMITS

        # 期限：连接/池超时全进程统一，读超时按调用类别
        for client, read in (
            (pools.control, CONTROL_READ_SEC),
            (pools.stream, STREAM_READ_SEC),
            (pools.upload, UPLOAD_READ_SEC),
            (pools.egress, EGRESS_READ_SEC),
        ):
            assert (client.timeout.read, client.timeout.connect, client.timeout.pool) == (
                read,
                CONNECT_TIMEOUT_SEC,
                POOL_TIMEOUT_SEC,
            )

        # 调用方消费装配根注入的**同一实例**（不是各自再造一个池）
        assert app.state.console_client._client is pools.control
        assert app.state.settings_client._client is pools.control
        assert app.state.runtime_client._stream_client is pools.stream
        assert app.state.runtime_client._upload_client is pools.upload
        assert app.state.runtime_client._client is pools.egress
        assert app.state.artifact_resolver._client is pools.egress

        # 受理上限取 GATEWAY_MAX_CONCURRENT_HANDLERS（TASK-048 的 deferred 接线）；
        # 排队上限（全局/每路由/命令）取 GATEWAY_MAX_PENDING_*（同一处启动预算）。
        assert app.state.inbound_pipeline._handlers._value == GATEWAY_MAX_CONCURRENT_HANDLERS
        limits = app.state.inbound_pipeline._backlog_limits
        assert (limits.max_pending, limits.max_per_route, limits.max_commands) == PENDING_LIMITS

        # 四类生命周期各自独立成任务（连接=注册表启停；快照/入站=独立循环；SSE=进程账本）
        loops = _loop_tasks((SNAPSHOT_LOOP_NAME, *INBOUND_LOOP_NAMES))
        assert set(loops) == {SNAPSHOT_LOOP_NAME, *INBOUND_LOOP_NAMES}, (
            f"缺少独立生命周期任务: {set(loops) ^ {SNAPSHOT_LOOP_NAME, *INBOUND_LOOP_NAMES}}"
        )
        assert all(not task.done() for task in loops.values())
        assert app.state.registry.adapter_states == {"WECOM": True, "WEBCHAT": True}
        assert app.state.webchat_sse_ledger.stats().active_subscribers == 0
        assert not pools.control.is_closed and not pools.egress.is_closed
    assert pools.control.is_closed and pools.stream.is_closed
    assert pools.upload.is_closed and pools.egress.is_closed, "装配根建的池随进程关闭"
    assert app.state.registry.adapter_states == {"WECOM": False, "WEBCHAT": False}
    leftover = [
        task
        for task in asyncio.all_tasks() - baseline
        if not task.done() and task.get_name() in {SNAPSHOT_LOOP_NAME, *INBOUND_LOOP_NAMES}
    ]
    assert leftover == [], f"存在悬挂的生命周期任务: {leftover}"


def test_b296_pool_budgets_come_from_settings_and_unused_pools_fail_explicitly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[B-296] 池预算派生自启动设置；本进程不建的池（model/mcp）显式拒绝。"""
    monkeypatch.setenv("CONTROL_HTTP_MAX_CONNECTIONS", "6")
    monkeypatch.setenv("CONTROL_HTTP_MAX_KEEPALIVE", "3")
    monkeypatch.setenv("CONTROL_HTTP_READ_TIMEOUT_SEC", "7")
    monkeypatch.setenv("STREAM_HTTP_MAX_CONNECTIONS", "9")
    monkeypatch.setenv("STREAM_HTTP_MAX_KEEPALIVE", "4")
    monkeypatch.setenv("STREAM_HTTP_READ_TIMEOUT_SEC", "11")
    monkeypatch.setenv("RUN_EVENT_HEARTBEAT_SEC", "1")
    monkeypatch.setenv("UPLOAD_HTTP_MAX_CONNECTIONS", "5")
    monkeypatch.setenv("UPLOAD_HTTP_MAX_KEEPALIVE", "2")
    monkeypatch.setenv("UPLOAD_HTTP_READ_TIMEOUT_SEC", "13")
    monkeypatch.setenv("EGRESS_HTTP_MAX_CONNECTIONS", "8")
    monkeypatch.setenv("EGRESS_HTTP_MAX_KEEPALIVE", "4")
    monkeypatch.setenv("EGRESS_HTTP_READ_TIMEOUT_SEC", "17")
    settings = SharedSettings()

    assert GATEWAY_HTTP_POOLS == ("control", "stream", "upload", "egress")
    assert {
        name for name in GatewayHttpPools.__dataclass_fields__ if name != "aclose"
    } == {"control", "stream", "upload", "egress"}, (
        "Gateway 只建实际调用的池（design §3.7「实例只创建实际需要的池」）"
    )
    for pool, limits, read in (
        ("control", (6, 3), 7.0),
        ("stream", (9, 4), 11.0),
        ("upload", (5, 2), 13.0),
        ("egress", (8, 4), 17.0),
    ):
        derived = pool_limits(settings, pool)  # type: ignore[arg-type]
        assert (derived.max_connections, derived.max_keepalive_connections) == limits
        assert pool_timeout(settings, pool).read == read  # type: ignore[arg-type]
    for unused in ("model", "mcp"):
        with pytest.raises(ValueError, match=unused):
            pool_limits(settings, unused)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match=unused):
            pool_timeout(settings, unused)  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------
# 3. 停服排空不重建 Run（重启后重连只做 GET 重放）
# --------------------------------------------------------------------------------------


async def test_b296_shutdown_drains_subscription_and_restart_never_rebuilds_the_run(
    stub_console: StubConsole,
    console_probe: _ConsoleProbe,
    runtime_probe: _RuntimeProbe,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """[B-296] 持有订阅时关服：订阅排空、账本归零、无重投；重启重连 GET 重放同一 Run。"""
    runtime_probe.plan = _StreamPlan(
        frames=[(1, "message.delta", {"reply_generation": 0, "delta": "部分"})],
        watermark=1,
        hold_open_sec=60.0,
    )
    redis_url = _isolated_redis_url()
    await _flush_redis(redis_url)
    await _seed_chat_session(redis_url)
    _install_gateway_env(
        monkeypatch,
        console_url=stub_console.url,
        runtime_url=runtime_probe.url,
        artifact_root=tmp_path / "artifacts",
        redis_url=redis_url,
    )
    baseline = set(asyncio.all_tasks())
    first_server = _GatewayServer()
    await first_server.start()
    stream_text = ""
    try:
        async with httpx.AsyncClient(
            base_url=first_server.url, timeout=HTTP_TIMEOUT_SEC
        ) as client:
            accepted = await client.post(
                f"/api/v1/webchat/entries/{ENTRY_ID}/messages",
                json={
                    "conversation_id": str(CONVERSATION_ID),
                    "message_id": "b296-msg-1",
                    "text": "你好",
                    "upload_ids": [],
                },
                headers={**_chat_headers(), "Idempotency-Key": "b296-msg-1"},
                cookies=_chat_cookies(),
            )
            assert accepted.status_code == 202, accepted.text
            assert accepted.json()["data"]["run_id"] == str(RUN_ID)
            assert len(runtime_probe.run_posts) == 1

            stream_cm = client.stream(
                "GET",
                f"/api/v1/webchat/runs/{RUN_ID}/events",
                cookies=_chat_cookies(),
                headers=_chat_headers(),
            )
            stream = await stream_cm.__aenter__()
            assert stream.status_code == 200, stream.text
            await _wait_for(
                lambda: app.state.webchat_sse_ledger.stats().active_subscribers == 1,
                message="订阅未计入进程账本",
            )
            try:
                # 关服（真实 uvicorn 排空）：持有中的订阅被关闭、租约释放。
                await first_server.stop()
                stream_text = await _read_stream_text(stream, timeout=10)
                await _wait_for(
                    lambda: app.state.webchat_sse_ledger.stats().active_subscribers == 0,
                    message="关服后订阅未排空",
                )
            finally:
                with suppress(Exception):
                    await stream_cm.__aexit__(None, None, None)
    finally:
        await first_server.stop()

    assert "event: message.delta" in stream_text
    assert len(runtime_probe.run_posts) == 1, "关服路径不得重建/重投 Run"
    pools = app.state.http_pools
    assert pools.control.is_closed and pools.stream.is_closed
    assert pools.upload.is_closed and pools.egress.is_closed
    assert app.state.registry.adapter_states == {"WECOM": False, "WEBCHAT": False}
    leftover = [
        task
        for task in asyncio.all_tasks() - baseline
        if not task.done()
        and task.get_name() in {SNAPSHOT_LOOP_NAME, *INBOUND_LOOP_NAMES}
    ]
    assert leftover == [], f"存在悬挂的生命周期任务: {leftover}"

    # 重启：浏览器重连（Last-Event-ID）只做 GET 重放，绝不 POST 重建 Run。
    runtime_probe.plan = _StreamPlan(
        frames=[(1, "message.delta", {"reply_generation": 0, "delta": "部分"})],
        watermark=1,
        hold_open_sec=0.5,
    )
    second_server = _GatewayServer()
    await second_server.start()
    try:
        async with httpx.AsyncClient(
            base_url=second_server.url, timeout=HTTP_TIMEOUT_SEC
        ) as client:
            async with client.stream(
                "GET",
                f"/api/v1/webchat/runs/{RUN_ID}/events",
                cookies=_chat_cookies(),
                headers={**_chat_headers(), "Last-Event-ID": "1"},
            ) as stream:
                assert stream.status_code == 200, stream.text
                replayed = await _read_stream_text(stream, timeout=HTTP_TIMEOUT_SEC)
    finally:
        await second_server.stop()

    assert "event: message.delta" not in replayed, "游标之后无新帧：重放不重投已应用事件"
    assert len(runtime_probe.run_posts) == 1, "重连重放不得重建 Run（受理 POST 恰一次）"
    assert [call["after_seq"] for call in runtime_probe.event_calls][-1] == 1
    assert runtime_probe.scope_calls >= 2  # 两次打开都核了归属（第一次订阅 + 重启重连）
    await _flush_redis(redis_url)


class _GatewayServer:
    """真实 Gateway 应用进程内服务：真实 uvicorn + 真实 lifespan（四类生命周期都在跑）。"""

    def __init__(self) -> None:
        self._config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=_free_port(),
            log_level="error",
            timeout_graceful_shutdown=2,
        )
        self._server = uvicorn.Server(self._config)
        self._task: asyncio.Task[None] | None = None
        self.url = f"http://127.0.0.1:{self._config.port}"

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._server.serve())
        await _wait_for(lambda: self._server.started, message="gateway 未在超时内监听")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._server.should_exit = True
        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._task, timeout=15)
        self._task = None


# --------------------------------------------------------------------------------------
# 4. 单机器人无连接重叠（快照换密钥先停旧再起新；退出全关）
# --------------------------------------------------------------------------------------


async def test_b296_request_plane_opens_no_channel_connections(
    stub_console: StubConsole,
    console_probe: _ConsoleProbe,
    runtime_probe: _RuntimeProbe,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """[B-296] 请求面不持有渠道长连接：快照刷新/密钥轮换都不开 WS，退出全关。"""
    probe = WeComProbe(expected_bots={WECOM_BOT_ID: WECOM_SECRET_1})
    await probe.start()
    console_probe.items = [_bot_payload(WECOM_BOT_ID, WECOM_SECRET_1)]
    _install_gateway_env(
        monkeypatch,
        console_url=stub_console.url,
        runtime_url=runtime_probe.url,
        artifact_root=tmp_path / "artifacts",
        wecom_probe=probe,
    )
    try:
        async with app.router.lifespan_context(app):
            assert probe.connections == [], "Gateway 不应建立企微长连接"
            console_probe.revision = "rev-b296-2"
            console_probe.items = [_bot_payload(WECOM_BOT_ID, WECOM_SECRET_2)]
            await app.state.bot_snapshot.refresh()
            assert probe.connections == [], "快照刷新也不得开渠道连接"
            adapter = app.state.registry.get("WECOM")
            assert isinstance(adapter, WebhookChannelAdapter)
        assert app.state.registry.adapter_states == {"WECOM": False, "WEBCHAT": False}
        assert probe.connections == []
    finally:
        await probe.stop()


# --------------------------------------------------------------------------------------
# 5. SSE 订阅上限显式拒绝（不静默丢、不占上游连接）
# --------------------------------------------------------------------------------------


async def test_b296_sse_subscriber_limit_rejects_explicitly_before_opening_the_owner_stream(
    stub_console: StubConsole,
    console_probe: _ConsoleProbe,
    runtime_probe: _RuntimeProbe,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """[B-296] 到 GATEWAY_MAX_SSE_SUBSCRIBERS 的新订阅显式 429；释放后可再订。"""
    console_probe.items = [
        _bot_payload(WEBCHAT_BOT_ID, None, channel="WEBCHAT", account_id=ENTRY_ID)
    ]
    runtime_probe.plan = _StreamPlan(
        frames=[(1, "message.delta", {"reply_generation": 0, "delta": "流"})],
        watermark=1,
        hold_open_sec=60.0,
    )
    redis_url = _isolated_redis_url()
    await _flush_redis(redis_url)
    await _seed_chat_session(redis_url)
    _install_gateway_env(
        monkeypatch,
        console_url=stub_console.url,
        runtime_url=runtime_probe.url,
        artifact_root=tmp_path / "artifacts",
        redis_url=redis_url,
        GATEWAY_MAX_SSE_SUBSCRIBERS="1",
    )
    async with _running_gateway() as env:
        async with httpx.AsyncClient(base_url=env, timeout=HTTP_TIMEOUT_SEC) as client:
            first_cm = client.stream(
                "GET",
                f"/api/v1/webchat/runs/{RUN_ID}/events",
                cookies=_chat_cookies(),
                headers=_chat_headers(),
            )
            first = await first_cm.__aenter__()
            assert first.status_code == 200, first.text
            await _wait_for(
                lambda: app.state.webchat_sse_ledger.stats().active_subscribers == 1,
                message="首个订阅未计入账本",
            )
            try:
                second = await client.get(
                    f"/api/v1/webchat/runs/{RUN_ID}/events",
                    cookies=_chat_cookies(),
                    headers=_chat_headers(),
                )
                assert second.status_code == 429, second.text
                assert second.json()["code"] == str(ErrorCode.RUN_CAPACITY_EXCEEDED)
                assert len(runtime_probe.event_calls) == 1, (
                    "被拒订阅不得打开 Owner 流（先裁决再占上游连接）"
                )
                assert app.state.webchat_sse_ledger.stats().rejected_subscribers == 1
            finally:
                with suppress(Exception):
                    await first_cm.__aexit__(None, None, None)
            await _wait_for(
                lambda: app.state.webchat_sse_ledger.stats().active_subscribers == 0,
                message="释放后账本未归零",
            )
            runtime_probe.plan.hold_open_sec = 0.5
            async with client.stream(
                "GET",
                f"/api/v1/webchat/runs/{RUN_ID}/events",
                cookies=_chat_cookies(),
                headers=_chat_headers(),
            ) as retry:
                assert retry.status_code == 200, retry.text
                assert retry.headers["content-type"].startswith("text/event-stream")
                await _wait_for(
                    lambda: app.state.webchat_sse_ledger.stats().active_subscribers == 1,
                    message="释放后重订未计入账本",
                )
            await _wait_for(
                lambda: app.state.webchat_sse_ledger.stats().active_subscribers == 0,
                message="重订流结束后账本未归零",
            )
            await _flush_redis(redis_url)
