"""B-252 Gateway 入口/会话/消息 HTTP 资源接口（design §3.4 API-05/06/08/09/14/15；TASK-052）。

真实边界（业务路径不 mock）：

- **真实 Gateway 应用与路由**：直连 `muad_im_gateway.main.app`（真实路由表、真实 api-kit 封套/
  错误目录、真实 `api/deps.py` 依赖图），并挂载**生产** `api/webchat.py` 路由（TASK-096 只做
  装配根注册）。装配根状态（Console/Runtime 客户端、部署租户、入口快照）按生产同形注入。
- **真实 Console HTTP（Owner）**：`ConsoleClient` 打真实本地 uvicorn 服务（真实 socket），
  按 API-12 `POST /internal/channel/chat-principal` 与 bot 快照契约应答；CHAT 主体与授权集合
  只从响应导出。
- **真实 Runtime HTTP/SSE（Owner）**：`RuntimeClient` 打真实本地 uvicorn 服务；`POST /v1/runs`
  是真实 `text/event-stream` 且**保持打开**直到测试放行——网关必须在受理帧后立即返回、不持流。
- **不读写 DB**：本文件不碰任何数据库；`tests/architecture/test_im_gateway_boundaries.py` 机检
  网关零 sqlalchemy，本文件另对资源接口模块做同样的静态断言。

断言要点（B-252）：

- 六个 API 完整转 Owner：入口/会话/消息/取消/恢复只经 Console/Runtime HTTP 取得事实，
  响应逐字段等于 Owner 的公开投影（不编造 run_id、不伪造状态）；
- **核当前 CHAT/授权/X-Chat-Context 后才查幂等回执**：缺凭据/被撤销/上下文不符/未授权入口
  在触达 Runtime 之前就被拒；通过门控后原样转发 `Idempotency-Key`（Owner 据此回放原 Run）；
- 返回**真实** 202/201/忙碌（RUN_BUSY 409）/容量（RUN_CAPACITY_EXCEEDED 429）状态；
- CHAT 凭据不进响应/日志；响应不含 Console 角色字段。
"""

from __future__ import annotations

import ast
import asyncio
import json
import logging
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

import pytest
import uvicorn
from fakes import StubConsole
from fastapi import Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from httpx import ASGITransport, AsyncClient
from muad_contracts import BotSnapshotItem, BotSnapshotResponse
from muad_contracts.webchat import (
    ADMISSION_CONVERSATION_ID_HEADER,
    ADMISSION_EXECUTION_STATUS_HEADER,
    ADMISSION_RESUMED_HEADER,
    ADMISSION_RUN_ID_HEADER,
    ADMISSION_RUN_STATUS_HEADER,
)
from muad_im_core import BotSnapshotCache
from muad_im_gateway.api.webchat import router as webchat_router
from muad_im_gateway.application.chat_principal import CHAT_SESSION_COOKIE
from muad_im_gateway.application.console_client import CHAT_PRINCIPAL_PATH, ConsoleClient
from muad_im_gateway.application.runtime_client import RuntimeClient
from muad_im_gateway.main import app

from tests.internal_service import TOKEN as INTERNAL_TOKEN
from tests.internal_service import internal_service_token  # noqa: F401  (fixture 注册)

pytestmark = pytest.mark.integration

GATEWAY_TENANT = "tenant-b252"
PLATFORM_USER_ID = UUID("7d2f9b31-4c8a-4e5d-9f10-6a3b8c2d4e51")
SESSION_ID = UUID("c1a4e8f2-6b30-4d7a-8e59-0f2b3c4d5e61")
AGENT_ID = UUID("a1b2c3d4-5e6f-4a7b-8c9d-0e1f2a3b4c5d")
OTHER_AGENT_ID = UUID("b2c3d4e5-6f70-4b8c-9d0e-1f2a3b4c5d6e")
ENTRY_ID = UUID("e1f2a3b4-c5d6-4e7f-8a9b-0c1d2e3f4a5b")
OTHER_ENTRY_ID = UUID("f2a3b4c5-d6e7-4f8a-9b0c-1d2e3f4a5b6c")
DISABLED_ENTRY_ID = UUID("a3b4c5d6-e7f8-4a9b-8c0d-1e2f3a4b5c6d")
CONVERSATION_ID = UUID("b4c5d6e7-f8a9-4b0c-9d1e-2f3a4b5c6d7e")
RUN_ID = UUID("c5d6e7f8-a9b0-4c1d-8e2f-3a4b5c6d7e8f")
SUBMISSION_ID = UUID("d6e7f8a9-b0c1-4d2e-9f3a-4b5c6d7e8f90")
SOURCE_EVENT_ID = UUID("e7f8a9b0-c1d2-4e3f-8a4b-5c6d7e8f9012")
MESSAGE_ID = "webchat-b252-message-1"
IDEMPOTENCY_KEY = "webchat-b252-idem-1"
RESUME_MESSAGE_ID = "webchat-b252-resume-1"
#: CHAT 会话原文 canary：只允许出现在发往 Console 的请求体里。
SESSION_TOKEN = f"chat-session-{uuid.uuid4().hex}"

ENTRIES_URL = "/api/v1/webchat/entries"
MESSAGES_URL = f"/api/v1/webchat/entries/{ENTRY_ID}/messages"
CONVERSATIONS_URL = f"/api/v1/webchat/entries/{ENTRY_ID}/conversations"
CONVERSATION_URL = f"/api/v1/webchat/conversations/{CONVERSATION_ID}"
HISTORY_URL = f"{CONVERSATION_URL}/messages"
CANCEL_URL = f"/api/v1/webchat/runs/{RUN_ID}/cancel"
RESUME_URL = f"/api/v1/webchat/runs/{RUN_ID}/resume"

PRINCIPAL_FIELDS = {"tenant_id", "platform_user_id", "session_id", "auth_method"}
CONSOLE_ROLE_FIELDS = {"role", "roles", "permissions", "username", "account_id", "user_code"}
EXECUTION_STATUS = {
    "stage": "WAITING_MODEL",
    "reason": "NO_CAPACITY",
    "queued_at": "2026-10-18T07:00:00Z",
    "deadline_at": "2026-10-18T07:00:30Z",
    "started_at": None,
}

#: 生产路由在真实应用上的注册（TASK-096 装配根只做同一件事）。
app.include_router(webchat_router)


def _envelope(data: Any = None) -> dict[str, Any]:
    return {
        "code": "0",
        "msg": "ok",
        "data": data,
        "trace_id": "trace-b252",
        "request_id": "request-b252",
        "timestamp": "2026-10-18T07:00:00Z",
    }


def _json(data: Any, status_code: int = 200) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=_envelope(data))


def _error(code: str, status_code: int) -> JSONResponse:
    body = _envelope(None)
    body["code"] = code
    body["msg"] = code
    return JSONResponse(status_code=status_code, content=body)


def _frame(event_type: str, data: dict[str, Any], *, run_id: UUID, seq: int) -> str:
    envelope = {
        "run_id": str(run_id),
        "seq": seq,
        "timestamp": "2026-10-18T07:00:00Z",
        "type": event_type,
        "data": data,
    }
    return f"event: {event_type}\ndata: {json.dumps(envelope)}\n\n"


# --- 真实 Console Owner 接口桩（真实 socket + 真实封套） ----------------------------------


class _ConsoleProbe:
    """API-12 的真实 HTTP 桩：chat-principal + bot 快照；记录每次调用的头/体。"""

    def __init__(self, stub: StubConsole) -> None:
        self.calls: list[tuple[str, dict[str, str], dict[str, Any]]] = []
        self.revoked_code: str | None = None
        self.agents: list[dict[str, str]] = [
            {"agent_id": str(AGENT_ID), "key": "webchat-agent", "name": "网页助手"}
        ]
        self.items: list[BotSnapshotItem] = [
            BotSnapshotItem(
                bot_account_id=ENTRY_ID, bot_id=str(ENTRY_ID), channel="WEBCHAT", agent_id=AGENT_ID
            ),
            BotSnapshotItem(
                bot_account_id=OTHER_ENTRY_ID,
                bot_id=str(OTHER_ENTRY_ID),
                channel="WEBCHAT",
                agent_id=OTHER_AGENT_ID,
            ),
            BotSnapshotItem(
                bot_account_id=DISABLED_ENTRY_ID,
                bot_id=str(DISABLED_ENTRY_ID),
                channel="WEBCHAT",
                agent_id=AGENT_ID,
                enabled=False,
            ),
            BotSnapshotItem(
                bot_account_id=uuid.uuid4(),
                bot_id="wecom-b252",
                channel="WECOM",
                agent_id=AGENT_ID,
            ),
        ]
        stub.handlers[CHAT_PRINCIPAL_PATH] = self._principal
        stub.handlers["/internal/channel/bots"] = self._bots

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

    async def _bots(self, request: Request) -> Response:
        self.calls.append(("/internal/channel/bots", dict(request.headers), {}))
        return _json(
            BotSnapshotResponse(
                revision="rev-b252", items=self.items, page=1, page_size=20, total=len(self.items)
            ).model_dump(mode="json")
        )


# --- 真实 Runtime Owner 接口桩（真实 socket + 真实 SSE） -----------------------------------


@dataclass(frozen=True)
class _Call:
    path: str
    query: dict[str, str]
    headers: dict[str, str]
    body: dict[str, Any]


class _RuntimeProbe:
    """Runtime Owner 端点桩：真实 HTTP/SSE；记录调用并可按脚本裁决。

    `POST /v1/runs` 与 `.../resume` 是**真实长流**：受理帧发出后保持打开，直到测试放行——
    网关若持流等待终态，202 就不会返回（B-252 的"受理与订阅解耦"由它裁决）。
    """

    def __init__(self) -> None:
        self.calls: dict[str, list[_Call]] = defaultdict(list)
        self.busy = False
        self.capacity_full = False
        self.run_status = "RUNNING"
        self.run_owner = PLATFORM_USER_ID
        self.scope_agent_id = AGENT_ID
        self.streams_released: list[str] = []
        self.streams_closed_before_terminal: list[str] = []
        self._release = threading.Event()
        self._run_counter = 0
        self._runs_by_key: dict[str, UUID] = {}
        self._lock = threading.Lock()
        runtime_app = self._build_app()
        self._app = runtime_app
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self.url = ""

    # --- 记录与放行 -----------------------------------------------------------------
    def _record(self, path: str, request: Request, body: dict[str, Any] | None = None) -> None:
        with self._lock:
            self.calls[path].append(
                _Call(
                    path=path,
                    query=dict(request.query_params),
                    headers=dict(request.headers),
                    body=body or {},
                )
            )

    def release(self) -> None:
        self._release.set()

    def next_run_id(self, idempotency_key: str) -> UUID:
        """同幂等键回放原 Run（真实 Runtime 的受理重放语义）；不同键才是新 Run。"""
        with self._lock:
            existing = self._runs_by_key.get(idempotency_key)
            if existing is not None:
                return existing
            self._run_counter += 1
            run_id = uuid.uuid5(uuid.NAMESPACE_URL, f"muad:b252:run:{self._run_counter}")
            self._runs_by_key[idempotency_key] = run_id
            return run_id

    # --- 端点 -----------------------------------------------------------------------
    def _admission_headers(self, run_id: UUID, *, resumed: bool = False) -> dict[str, str]:
        """真实受理裁决响应头：Run/会话/状态/阶段一次带回（Gateway 不再 get_run）。"""
        return {
            ADMISSION_RUN_ID_HEADER: str(run_id),
            ADMISSION_CONVERSATION_ID_HEADER: str(CONVERSATION_ID),
            ADMISSION_RESUMED_HEADER: "1" if resumed else "0",
            ADMISSION_RUN_STATUS_HEADER: self.run_status,
            ADMISSION_EXECUTION_STATUS_HEADER: json.dumps(EXECUTION_STATUS, ensure_ascii=True),
        }

    def _build_app(self) -> Any:
        from fastapi import FastAPI

        runtime_app = FastAPI()

        @runtime_app.post("/v1/runs")
        async def _create_run(request: Request) -> Response:
            body = json.loads(await request.body() or b"{}")
            self._record("/v1/runs", request, body)
            if self.capacity_full:
                return _error("RUN_CAPACITY_EXCEEDED", 429)
            if self.busy:
                return _error("RUN_BUSY", 409)
            run_id = self.next_run_id(request.headers.get("Idempotency-Key", ""))
            return StreamingResponse(
                self._run_stream(run_id),
                media_type="text/event-stream",
                headers=self._admission_headers(run_id),
            )

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
                    "execution_status": dict(EXECUTION_STATUS),
                    "cancel_requested": False,
                }
            )

        @runtime_app.post("/v1/runs/{run_id}/cancel")
        async def _cancel(run_id: str, request: Request) -> Response:
            self._record(f"/v1/runs/{run_id}/cancel", request)
            return _json({"run_id": run_id, "status": "CANCELLING", "cancel_requested": True})

        @runtime_app.post("/v1/runs/{run_id}/resume")
        async def _resume(run_id: str, request: Request) -> Response:
            body = json.loads(await request.body() or b"{}")
            self._record(f"/v1/runs/{run_id}/resume", request, body)
            return StreamingResponse(
                self._run_stream(UUID(run_id)),
                media_type="text/event-stream",
                headers=self._admission_headers(UUID(run_id), resumed=True),
            )

        @runtime_app.post("/v1/conversations")
        async def _conversation(request: Request) -> Response:
            body = json.loads(await request.body() or b"{}")
            self._record("/v1/conversations", request, body)
            return _json(
                {
                    "conversation_id": str(CONVERSATION_ID),
                    "agent_id": body.get("agent_id"),
                    "user_id": body.get("platform_user_id"),
                    "entry_id": body.get("entry_id"),
                    "title": "新会话",
                    "status": "ACTIVE",
                    "last_seq": 0,
                    "last_run_id": None,
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

        @runtime_app.get("/internal/webchat/entries/{entry_id}/conversations")
        async def _conversations(entry_id: str, request: Request) -> Response:
            self._record(f"/internal/webchat/entries/{entry_id}/conversations", request)
            return _json(
                {
                    "items": [
                        {
                            "conversation_id": str(CONVERSATION_ID),
                            "title": "容量检查",
                            "create_time": "2026-10-18T06:00:00Z",
                            "last_message_at": "2026-10-18T06:30:00Z",
                            "last_run_id": str(RUN_ID),
                            "status": "ACTIVE",
                            "active_run_status": "RUNNING",
                        }
                    ],
                    "page": 2,
                    "page_size": 10,
                    "total": 11,
                }
            )

        @runtime_app.get("/internal/webchat/conversations/{conversation_id}")
        async def _detail(conversation_id: str, request: Request) -> Response:
            self._record(f"/internal/webchat/conversations/{conversation_id}", request)
            return _json(
                {
                    "conversation_id": conversation_id,
                    "entry_id": str(ENTRY_ID),
                    "agent_id": str(AGENT_ID),
                    "title": "容量检查",
                    "status": "ACTIVE",
                    "last_seq": 12,
                    "last_run_id": str(RUN_ID),
                    "active_run": {
                        "id": str(RUN_ID),
                        "status": "WAITING_INPUT",
                        "execution_status": {
                            "stage": "WAITING_INPUT",
                            "reason": None,
                            "queued_at": None,
                            "deadline_at": None,
                            "started_at": None,
                        },
                        "confirmation": None,
                    },
                }
            )

        @runtime_app.get("/internal/webchat/conversations/{conversation_id}/messages")
        async def _messages(conversation_id: str, request: Request) -> Response:
            self._record(f"/internal/webchat/conversations/{conversation_id}/messages", request)
            return _json(
                {
                    "items": [
                        {
                            "event_id": str(SOURCE_EVENT_ID),
                            "seq": 7,
                            "run_id": str(RUN_ID),
                            "role": "USER",
                            "text": "帮我总结附件",
                            "attachments": [],
                        }
                    ],
                    "page": 1,
                    "page_size": 50,
                    "total": 1,
                    "through_seq": 7,
                }
            )

        return runtime_app

    async def _run_stream(self, run_id: UUID) -> AsyncIterator[str]:
        """受理帧后保持打开：网关必须在受理后立即返回，不能持流等待终态。"""
        terminal = False
        try:
            yield _frame("run.created", {"resumed": False, "conversation_id": str(CONVERSATION_ID)},
                         run_id=run_id, seq=1)
            while not self._release.is_set():
                await asyncio.sleep(0.02)
            yield _frame("run.completed", {"status": "COMPLETED", "final_text": "完成"},
                         run_id=run_id, seq=2)
            terminal = True
            with self._lock:
                self.streams_released.append(str(run_id))
        finally:
            if not terminal:
                with self._lock:
                    self.streams_closed_before_terminal.append(str(run_id))

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
        self._release.set()
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)


# --- 夹具：真实 HTTP 桩 + 真实 Gateway 应用（按生产装配根状态注入） --------------------------


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


@asynccontextmanager
async def _gateway_client(
    stub_console: StubConsole,
    runtime_probe: _RuntimeProbe,
    *,
    cookies: dict[str, str] | None = None,
) -> AsyncIterator[AsyncClient]:
    """真实 Gateway 应用 + 真实 Console/Runtime 客户端；只注入装配根会发布的状态值。"""
    previous: dict[str, Any] = {
        name: getattr(app.state, name, None)
        for name in ("console_client", "runtime_client", "bot_snapshot", "tenant_id")
    }
    console = ConsoleClient(stub_console.url)
    runtime = RuntimeClient(runtime_probe.url)
    snapshot = BotSnapshotCache(console, tenant_id=GATEWAY_TENANT)
    app.state.console_client = console
    app.state.runtime_client = runtime
    app.state.bot_snapshot = snapshot
    app.state.tenant_id = GATEWAY_TENANT
    try:
        await snapshot.refresh()
        assert snapshot.is_ready(), "真实 Console 快照未就绪"
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://gateway.test",
            cookies={CHAT_SESSION_COOKIE: SESSION_TOKEN} if cookies is None else cookies,
        ) as client:
            yield client
    finally:
        for name, value in previous.items():
            if value is None:
                with suppress(AttributeError):
                    delattr(app.state, name)
            else:
                setattr(app.state, name, value)
        await console.aclose()
        await runtime.aclose()


def _chat_headers(context: UUID | str = SESSION_ID) -> dict[str, str]:
    return {"X-Chat-Context": str(context)}


def _chat_credentials_ok(probe: _RuntimeProbe) -> None:
    assert probe.calls == {}, "断言前提：门控未通过时 Runtime 零触达"


def _principal_calls(console_probe: _ConsoleProbe) -> list[Any]:
    return [call for call in console_probe.calls if call[0] == CHAT_PRINCIPAL_PATH]


# --- B-252：主体/授权/上下文门控先于任何 Owner 数据与幂等回执 --------------------------------


async def test_b252_chat_gate_and_entry_authorization_precede_every_owner_call(
    stub_console: StubConsole,
    console_probe: _ConsoleProbe,
    runtime_probe: _RuntimeProbe,
) -> None:
    """缺凭据/被撤销/未授权入口：都在 Runtime（幂等回执）之前被拒。"""
    rejected: list[Any] = []
    async with _gateway_client(stub_console, runtime_probe, cookies={}) as client:
        rejected.append(await client.get(ENTRIES_URL, headers=_chat_headers()))
        rejected.append(
            await client.post(
                MESSAGES_URL,
                headers=_chat_headers() | {"Idempotency-Key": IDEMPOTENCY_KEY},
                json={"conversation_id": str(CONVERSATION_ID), "message_id": MESSAGE_ID, "text": "你好"},
            )
        )
    async with _gateway_client(stub_console, runtime_probe) as client:
        rejected.append(await client.get(ENTRIES_URL))
        rejected.append(await client.get(ENTRIES_URL, headers=_chat_headers("not-a-uuid")))

    assert [response.json()["code"] for response in rejected] == [
        "UNAUTHORIZED",
        "UNAUTHORIZED",
        "COMMON_VALIDATION_ERROR",
        "COMMON_VALIDATION_ERROR",
    ]
    _chat_credentials_ok(runtime_probe)
    assert _principal_calls(console_probe) == [], "缺凭据不得触达 Console"

    # 被撤销的 CHAT 会话：Console 明确拒绝，网关原样上抛，不查任何幂等回执。
    console_probe.revoked_code = "CHAT_SESSION_REVOKED"
    async with _gateway_client(stub_console, runtime_probe) as client:
        revoked = await client.post(
            MESSAGES_URL,
            headers=_chat_headers() | {"Idempotency-Key": IDEMPOTENCY_KEY},
            json={"conversation_id": str(CONVERSATION_ID), "message_id": MESSAGE_ID, "text": "你好"},
        )
    assert revoked.status_code == 401 and revoked.json()["code"] == "CHAT_SESSION_REVOKED"
    _chat_credentials_ok(runtime_probe)
    assert len(_principal_calls(console_probe)) == 1, "撤销判定只经 Console"

    # 上下文不符：网关自拒（409），不触达 Runtime。
    console_probe.revoked_code = None
    async with _gateway_client(stub_console, runtime_probe) as client:
        mismatch = await client.get(ENTRIES_URL, headers=_chat_headers(uuid.uuid4()))
    assert mismatch.status_code == 409 and mismatch.json()["code"] == "AUTH_CONTEXT_CHANGED"
    _chat_credentials_ok(runtime_probe)

    # 未授权入口（入口的 Agent 不在当前授权集合）：授权判定先于 Runtime，越权 FORBIDDEN。
    async with _gateway_client(stub_console, runtime_probe) as client:
        forbidden = await client.get(
            f"/api/v1/webchat/entries/{OTHER_ENTRY_ID}/conversations", headers=_chat_headers()
        )
    assert forbidden.status_code == 403 and forbidden.json()["code"] == "FORBIDDEN"
    _chat_credentials_ok(runtime_probe)

    # 未知/停用入口：不泄露存在性，同样零 Runtime 触达。
    async with _gateway_client(stub_console, runtime_probe) as client:
        unknown = await client.get(
            f"/api/v1/webchat/entries/{uuid.uuid4()}/conversations", headers=_chat_headers()
        )
        disabled = await client.get(
            f"/api/v1/webchat/entries/{DISABLED_ENTRY_ID}/conversations", headers=_chat_headers()
        )
    assert unknown.status_code == 404 and unknown.json()["code"] == "COMMON_NOT_FOUND"
    assert disabled.status_code == 404 and disabled.json()["code"] == "COMMON_NOT_FOUND"
    _chat_credentials_ok(runtime_probe)
    assert SESSION_TOKEN not in forbidden.text + unknown.text + disabled.text


async def test_b252_entries_list_projects_only_enabled_authorized_webchat_entries(
    stub_console: StubConsole,
    console_probe: _ConsoleProbe,
    runtime_probe: _RuntimeProbe,
) -> None:
    """API-05：只列已启用且获授权的 WEBCHAT 入口；字段恰三个、无角色/凭据。"""
    async with _gateway_client(stub_console, runtime_probe) as client:
        response = await client.get(ENTRIES_URL, headers=_chat_headers())

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store", "私有聊天资源必须 no-store"
    data = response.json()["data"]
    assert data["total"] == 1 and data["page"] == 1
    assert data["items"] == [
        {"entry_id": str(ENTRY_ID), "agent_id": str(AGENT_ID), "display_name": "网页助手"}
    ]
    assert set(data["items"][0]) == {"entry_id", "agent_id", "display_name"}
    assert CONSOLE_ROLE_FIELDS.isdisjoint(set(data["items"][0]))
    assert SESSION_TOKEN not in response.text
    # 授权/入口事实来自 Console（主体+授权集合）与快照；不触达 Runtime。
    assert _principal_calls(console_probe)
    _chat_credentials_ok(runtime_probe)
    # 一次请求只核验一次主体。
    assert len(_principal_calls(console_probe)) == 1


# --- B-252：API-06 消息提交 ---------------------------------------------------------------


async def test_b252_message_submit_returns_real_accepted_run_without_holding_the_stream(
    stub_console: StubConsole,
    console_probe: _ConsoleProbe,
    runtime_probe: _RuntimeProbe,
) -> None:
    """202 携带 Owner 的真实 Run 身份与阶段；受理后立即返回，不持流等终态。"""
    started = time.monotonic()
    async with _gateway_client(stub_console, runtime_probe) as client:
        response = await client.post(
            MESSAGES_URL,
            headers=_chat_headers() | {"Idempotency-Key": IDEMPOTENCY_KEY},
            json={
                "conversation_id": str(CONVERSATION_ID),
                "message_id": MESSAGE_ID,
                "text": "帮我总结附件",
                "upload_ids": [str(SUBMISSION_ID)],
            },
        )
    elapsed = time.monotonic() - started

    assert response.status_code == 202, response.text
    accepted = response.json()["data"]
    run_id = accepted["run_id"]
    assert UUID(run_id).version == 5, "run_id 必须来自 Owner 的真实受理帧"
    assert accepted == {
        "conversation_id": str(CONVERSATION_ID),
        "run_id": run_id,
        "status": "RUNNING",
        "execution_status": EXECUTION_STATUS,
    }
    # 受理与订阅解耦：长流仍开着，202 已经返回；网关在受理帧后关闭自己的读取流。
    assert runtime_probe.streams_released == [], "断言前提：长流未放行（网关不得等终态）"
    deadline = time.monotonic() + 5.0
    while runtime_probe.streams_closed_before_terminal != [run_id] and time.monotonic() < deadline:
        await asyncio.sleep(0.02)
    assert runtime_probe.streams_closed_before_terminal == [run_id], "网关不得持流到终态"
    assert elapsed < 5.0

    # 完整转 Owner：请求体逐字段等于 API-06 语义（显式会话 + 精确来源 + 幂等身份）。
    create = runtime_probe.calls["/v1/runs"][0]
    assert create.headers["idempotency-key"] == IDEMPOTENCY_KEY
    assert create.headers["x-tenant-id"] == GATEWAY_TENANT
    assert create.body["agent_id"] == str(AGENT_ID)
    assert create.body["platform_user_id"] == str(PLATFORM_USER_ID)
    assert create.body["conversation_id"] == str(CONVERSATION_ID)
    assert create.body["channel"] == {
        "type": "WEBCHAT",
        "bot_id": str(ENTRY_ID),
        "external_user_id": str(PLATFORM_USER_ID),
        "external_conversation_id": str(CONVERSATION_ID),
    }
    assert create.body["message"] == {
        "id": MESSAGE_ID,
        "type": "attachment",
        "text": "帮我总结附件",
        "attachments": [],
        "upload_ids": [str(SUBMISSION_ID)],
    }
    # 受理后的状态/阶段来自受理裁决响应头（TASK-010）：202 之后不再 get_run。
    run_calls = [call for path, calls in runtime_probe.calls.items() for call in calls
                 if path == f"/v1/runs/{run_id}"]
    assert run_calls == [], "受理回执不得再回读 Run（状态/阶段已随受理一次带回）"

    # 重投：同一幂等键原样转发（Owner 据此回放原 Run），响应给回同一身份。
    async with _gateway_client(stub_console, runtime_probe) as client:
        replay = await client.post(
            MESSAGES_URL,
            headers=_chat_headers() | {"Idempotency-Key": IDEMPOTENCY_KEY},
            json={"conversation_id": str(CONVERSATION_ID), "message_id": MESSAGE_ID, "text": "帮我总结附件"},
        )
    assert replay.status_code == 202, replay.text
    assert runtime_probe.calls["/v1/runs"][1].headers["idempotency-key"] == IDEMPOTENCY_KEY
    assert replay.json()["data"]["run_id"] == run_id
    assert SESSION_TOKEN not in response.text
    assert "principal" not in response.text


async def test_b252_message_submit_surfaces_real_busy_and_capacity_statuses(
    stub_console: StubConsole,
    console_probe: _ConsoleProbe,
    runtime_probe: _RuntimeProbe,
) -> None:
    """忙碌（RUN_BUSY 409）与队列/容量满（RUN_CAPACITY_EXCEEDED 429）按 Owner 原码返回。"""
    payload = {
        "conversation_id": str(CONVERSATION_ID),
        "message_id": MESSAGE_ID,
        "text": "帮我总结附件",
    }
    runtime_probe.busy = True
    async with _gateway_client(stub_console, runtime_probe) as client:
        busy = await client.post(
            MESSAGES_URL,
            headers=_chat_headers() | {"Idempotency-Key": IDEMPOTENCY_KEY},
            json=payload,
        )
    assert busy.status_code == 409 and busy.json()["code"] == "RUN_BUSY"
    assert "run_id" not in busy.text, "忙碌不得伪造 run_id"

    runtime_probe.busy = False
    runtime_probe.capacity_full = True
    async with _gateway_client(stub_console, runtime_probe) as client:
        full = await client.post(
            MESSAGES_URL,
            headers=_chat_headers() | {"Idempotency-Key": IDEMPOTENCY_KEY},
            json=payload,
        )
    assert full.status_code == 429 and full.json()["code"] == "RUN_CAPACITY_EXCEEDED"
    assert "run_id" not in full.text

    # 缺少幂等键：入口显式拒绝（API-06 契约要求携带），不触达 Runtime。
    runtime_probe.capacity_full = False
    async with _gateway_client(stub_console, runtime_probe) as client:
        missing = await client.post(MESSAGES_URL, headers=_chat_headers(), json=payload)
    assert missing.status_code == 422
    assert len(runtime_probe.calls["/v1/runs"]) == 2, "缺幂等键的提交不得触达 Owner"


# --- B-252：API-08 会话创建 / 取消 / 恢复；API-14 列表 ---------------------------------------


async def test_b252_conversation_create_and_list_delegate_to_owner(
    stub_console: StubConsole,
    console_probe: _ConsoleProbe,
    runtime_probe: _RuntimeProbe,
) -> None:
    """API-08 新建（201）与 API-14 列表：入口/主体显式，响应是 Owner 的公开投影。"""
    async with _gateway_client(stub_console, runtime_probe) as client:
        created = await client.post(
            CONVERSATIONS_URL,
            headers=_chat_headers() | {"Idempotency-Key": "webchat-b252-conv-1"},
        )
        listed = await client.get(
            CONVERSATIONS_URL,
            headers=_chat_headers(),
            params={"page": 2, "page_size": 10},
        )

    assert created.status_code == 201, created.text
    assert created.json()["data"] == {
        "conversation_id": str(CONVERSATION_ID),
        "entry_id": str(ENTRY_ID),
        "title": "新会话",
        "status": "ACTIVE",
        "last_seq": 0,
        "active_run": None,
    }
    create = runtime_probe.calls["/v1/conversations"][0]
    assert create.headers["idempotency-key"] == "webchat-b252-conv-1"
    assert create.body == {
        "agent_id": str(AGENT_ID),
        "platform_user_id": str(PLATFORM_USER_ID),
        "entry_id": str(ENTRY_ID),
        "external_thread_id": str(PLATFORM_USER_ID),
    }

    assert listed.status_code == 200, listed.text
    assert listed.json()["data"] == {
        "items": [
            {
                "conversation_id": str(CONVERSATION_ID),
                "title": "容量检查",
                "create_time": "2026-10-18T06:00:00Z",
                "last_message_at": "2026-10-18T06:30:00Z",
                "last_run_id": str(RUN_ID),
                "status": "ACTIVE",
                "active_run_status": "RUNNING",
            }
        ],
        "page": 2,
        "page_size": 10,
        "total": 11,
    }
    read = runtime_probe.calls[f"/internal/webchat/entries/{ENTRY_ID}/conversations"][0]
    assert read.headers["x-internal-service"] == INTERNAL_TOKEN
    assert read.headers["x-tenant-id"] == GATEWAY_TENANT
    assert read.query == {
        "user_id": str(PLATFORM_USER_ID),
        "agent_id": str(AGENT_ID),
        "page": "2",
        "page_size": "10",
    }


async def test_b252_conversation_detail_and_history_resolve_scope_then_read_entry_scoped(
    stub_console: StubConsole,
    console_probe: _ConsoleProbe,
    runtime_probe: _RuntimeProbe,
) -> None:
    """API-15/09：公共路径不带入口 ⇒ 先经 Owner 解析归属，再走入口显式读取；投影原样。"""
    async with _gateway_client(stub_console, runtime_probe) as client:
        detail = await client.get(CONVERSATION_URL, headers=_chat_headers())
        history = await client.get(
            HISTORY_URL, headers=_chat_headers(), params={"page": 1, "page_size": 50, "through_seq": 7}
        )

    assert detail.status_code == 200, detail.text
    assert detail.json()["data"] == {
        "conversation_id": str(CONVERSATION_ID),
        "entry_id": str(ENTRY_ID),
        "agent_id": str(AGENT_ID),
        "title": "容量检查",
        "status": "ACTIVE",
        "last_seq": 12,
        "last_run_id": str(RUN_ID),
        "active_run": {
            "id": str(RUN_ID),
            "status": "WAITING_INPUT",
            "execution_status": {
                "stage": "WAITING_INPUT",
                "reason": None,
                "queued_at": None,
                "deadline_at": None,
                "started_at": None,
            },
            "confirmation": None,
        },
    }
    assert history.status_code == 200, history.text
    assert history.json()["data"] == {
        "items": [
            {
                "event_id": str(SOURCE_EVENT_ID),
                "seq": 7,
                "run_id": str(RUN_ID),
                "role": "USER",
                "text": "帮我总结附件",
                "attachments": [],
            }
        ],
        "page": 1,
        "page_size": 50,
        "total": 1,
        "through_seq": 7,
    }

    scope_path = f"/internal/webchat/conversations/{CONVERSATION_ID}/scope"
    assert [call.query for call in runtime_probe.calls[scope_path]] == [
        {"user_id": str(PLATFORM_USER_ID)},
        {"user_id": str(PLATFORM_USER_ID)},
    ], "详情与历史各自解析一次归属（只带受信主体）"
    detail_read = runtime_probe.calls[f"/internal/webchat/conversations/{CONVERSATION_ID}"][0]
    assert detail_read.query == {
        "user_id": str(PLATFORM_USER_ID),
        "agent_id": str(AGENT_ID),
        "entry_id": str(ENTRY_ID),
    }
    history_read = runtime_probe.calls[
        f"/internal/webchat/conversations/{CONVERSATION_ID}/messages"
    ][0]
    assert history_read.query == {
        "user_id": str(PLATFORM_USER_ID),
        "agent_id": str(AGENT_ID),
        "entry_id": str(ENTRY_ID),
        "page": "1",
        "page_size": "50",
        "through_seq": "7",
    }
    assert history_read.headers["x-internal-service"] == INTERNAL_TOKEN

    # 会话的 Agent 不在**当前**授权集合（授权已撤销）：读取被拒，且不触达入口显式读取端点。
    runtime_probe.scope_agent_id = OTHER_AGENT_ID
    async with _gateway_client(stub_console, runtime_probe) as client:
        revoked = await client.get(CONVERSATION_URL, headers=_chat_headers())
    assert revoked.status_code == 403 and revoked.json()["code"] == "FORBIDDEN"
    assert (
        len(runtime_probe.calls[f"/internal/webchat/conversations/{CONVERSATION_ID}"]) == 1
    ), "撤销授权后不得再读入口显式详情"
    assert SESSION_TOKEN not in revoked.text


# --- B-252：API-08 精确取消与恢复 -----------------------------------------------------------


async def test_b252_cancel_and_resume_bind_the_original_run_and_replay(
    stub_console: StubConsole,
    console_probe: _ConsoleProbe,
    runtime_probe: _RuntimeProbe,
) -> None:
    """取消/恢复：先核 Run 归属（他人 Run 不可见），再按原 Run/绑定转 Owner；可重放。"""
    async with _gateway_client(stub_console, runtime_probe) as client:
        cancelled = await client.post(CANCEL_URL, headers=_chat_headers())
        replayed = await client.post(CANCEL_URL, headers=_chat_headers())
        resumed = await client.post(
            RESUME_URL,
            headers=_chat_headers(),
            json={
                "submission_id": str(SUBMISSION_ID),
                "source_event_id": str(SOURCE_EVENT_ID),
                "message_id": RESUME_MESSAGE_ID,
                "selection": f"确认 {SUBMISSION_ID}",
            },
        )

    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["data"] == {
        "run_id": str(RUN_ID),
        "status": "CANCELLING",
        "cancel_requested": True,
    }
    assert replayed.status_code == 200 and replayed.json()["data"] == cancelled.json()["data"]
    assert len(runtime_probe.calls[f"/v1/runs/{RUN_ID}/cancel"]) == 2, "取消可重放"

    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["data"] == {
        "run_id": str(RUN_ID),
        "submission_id": str(SUBMISSION_ID),
        "status": "RUNNING",
        "execution_status": EXECUTION_STATUS,
    }
    resume_call = runtime_probe.calls[f"/v1/runs/{RUN_ID}/resume"][0]
    assert resume_call.body == {
        "platform_user_id": str(PLATFORM_USER_ID),
        "input": {"id": RESUME_MESSAGE_ID, "type": "text", "text": f"确认 {SUBMISSION_ID}"},
        "submission_id": str(SUBMISSION_ID),
        "source_event_id": str(SOURCE_EVENT_ID),
    }
    assert resume_call.headers["idempotency-key"] == RESUME_MESSAGE_ID

    # 他人的 Run：归属核验先拒（404 不泄露存在性），取消/恢复零触达。
    runtime_probe.run_owner = uuid.uuid4()
    async with _gateway_client(stub_console, runtime_probe) as client:
        foreign_cancel = await client.post(CANCEL_URL, headers=_chat_headers())
        foreign_resume = await client.post(
            RESUME_URL,
            headers=_chat_headers(),
            json={
                "submission_id": str(SUBMISSION_ID),
                "source_event_id": str(SOURCE_EVENT_ID),
                "message_id": RESUME_MESSAGE_ID,
                "selection": f"确认 {SUBMISSION_ID}",
            },
        )
    assert foreign_cancel.status_code == 404 and foreign_cancel.json()["code"] == "COMMON_NOT_FOUND"
    assert foreign_resume.status_code == 404 and foreign_resume.json()["code"] == "COMMON_NOT_FOUND"
    assert len(runtime_probe.calls[f"/v1/runs/{RUN_ID}/cancel"]) == 2, "他人 Run 不得被取消"
    assert len(runtime_probe.calls[f"/v1/runs/{RUN_ID}/resume"]) == 1, "他人 Run 不得被恢复"
    assert SESSION_TOKEN not in foreign_cancel.text + foreign_resume.text


# --- B-252：凭据卫生与不持库 ----------------------------------------------------------------


async def test_b252_chat_credentials_never_reach_responses_logs_or_owner_payloads(
    stub_console: StubConsole,
    console_probe: _ConsoleProbe,
    runtime_probe: _RuntimeProbe,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """CHAT 会话原文只进发往 Console 的核验请求体：响应/日志/转发头里都没有它。"""
    caplog.set_level(logging.DEBUG)
    async with _gateway_client(stub_console, runtime_probe) as client:
        ok = await client.get(ENTRIES_URL, headers=_chat_headers())
        failed = await client.get(ENTRIES_URL, headers=_chat_headers(uuid.uuid4()))

    assert ok.status_code == 200 and failed.status_code == 409
    assert SESSION_TOKEN not in ok.text + failed.text
    assert all(SESSION_TOKEN not in record.getMessage() for record in caplog.records)
    principal_call = _principal_calls(console_probe)[0]
    assert principal_call[2] == {"session_token": SESSION_TOKEN}, "原文只进 Console 核验体"
    assert principal_call[1]["x-tenant-id"] == GATEWAY_TENANT
    assert principal_call[1]["x-internal-service"] == INTERNAL_TOKEN
    # 主体不在任何响应里；Console 角色字段也不出现。
    assert "principal" not in ok.text
    assert CONSOLE_ROLE_FIELDS.isdisjoint(set(ok.json()["data"]["items"][0]))
    assert PRINCIPAL_FIELDS.isdisjoint(set(ok.json()["data"]["items"][0]))


def test_b252_resource_api_modules_hold_no_database() -> None:
    """网关不持库：资源接口/服务模块零 sqlalchemy（与架构守卫同一口径）。"""
    root = Path(__file__).resolve().parents[2]
    for relative in (
        "apps/im-gateway/src/muad_im_gateway/api/webchat.py",
        "apps/im-gateway/src/muad_im_gateway/application/webchat_service.py",
    ):
        source = (root / relative).read_text(encoding="utf-8")
        assert "sqlalchemy" not in source, relative
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all("sqlalchemy" not in alias.name for alias in node.names), relative
            elif isinstance(node, ast.ImportFrom):
                assert "sqlalchemy" not in (node.module or ""), relative
