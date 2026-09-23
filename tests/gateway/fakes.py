from __future__ import annotations

import httpx
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uvicorn
from pathlib import Path

from collections.abc import AsyncIterator, Awaitable, Callable
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from typing import Any
from uuid import UUID, uuid4

from muad_api import AppError
from muad_contracts import (
    DEFAULT_PAGE_SIZE,
    BotSnapshotResponse,
    ChannelSkillItem,
    ChannelSkillsResponse,
    ChannelBindRequest,
    ChannelBindResponse,
    ChannelEnvelope,
    ChannelResolveRequest,
    ChannelResolveResponse,
    RunRequest,
)
from muad_im_gateway.application.runtime_client import SseEvent
from muad_im_gateway.channels.wecom.sdk_port import WeComInboundEvent, WeComInboundMessage


class FakeConsoleClient:
    def __init__(self) -> None:
        self.resolve_response = ChannelResolveResponse(bound=False)
        self.resolve_error: AppError | None = None
        self.bind_response = ChannelBindResponse(platform_user_id=uuid4())
        self.bind_error: AppError | None = None
        self.skills: list[Any] = []
        self.bind_keys: list[str | None] = []
        self.skills_error: AppError | None = None
        self.bot_snapshot = BotSnapshotResponse(revision="rev-1")
        self.bots_error: AppError | None = None
        self.resolve_calls: list[ChannelResolveRequest] = []
        self.bind_calls: list[ChannelBindRequest] = []
        self.bots_calls = 0

    async def resolve(
        self,
        request: ChannelResolveRequest,
        tenant_id: str,
    ) -> ChannelResolveResponse:
        self.resolve_calls.append(request)
        if self.resolve_error is not None:
            raise self.resolve_error
        return self.resolve_response

    async def bind(
        self,
        request: ChannelBindRequest,
        tenant_id: str,
        *,
        idempotency_key: str | None = None,
    ) -> ChannelBindResponse:
        self.bind_calls.append(request)
        self.bind_keys.append(idempotency_key)
        if self.bind_error is not None:
            raise self.bind_error
        return self.bind_response

    async def bots(
        self,
        tenant_id: str,
        *,
        page: int = 1,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> BotSnapshotResponse:
        self.bots_calls += 1
        if self.bots_error is not None:
            raise self.bots_error
        return self.bot_snapshot

    async def channel_skills(
        self,
        agent_id: UUID,
        platform_user_id: UUID,
        tenant_id: str,
        *,
        page: int = 1,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> ChannelSkillsResponse:
        if self.skills_error is not None:
            raise self.skills_error
        items = [ChannelSkillItem.model_validate(skill) for skill in self.skills]
        return ChannelSkillsResponse(items=items, page=page, page_size=page_size, total=len(items))


class FakeRuntimeClient:
    def __init__(self, events: list[SseEvent] | None = None) -> None:
        self.events = list(events or [])
        self.run_error: AppError | None = None
        self.conversation_error: AppError | None = None
        self.cancel_error: AppError | None = None
        self.run_requests: list[RunRequest] = []
        self.conversations: list[tuple[UUID, UUID]] = []
        self.cancel_calls: list[tuple[UUID, UUID]] = []

    async def create_run(
        self,
        request: RunRequest,
        *,
        tenant_id: str,
        trace_id: str = "",
    ) -> AsyncIterator[SseEvent]:
        self.run_requests.append(request)
        if self.run_error is not None:
            raise self.run_error
        for event in self.events:
            yield event

    async def create_conversation(
        self,
        agent_id: UUID,
        platform_user_id: UUID,
        *,
        tenant_id: str = "",
        trace_id: str = "",
    ) -> dict[str, Any]:
        if self.conversation_error is not None:
            raise self.conversation_error
        self.conversations.append((agent_id, platform_user_id))
        return {"conversation_id": str(uuid4())}

    async def cancel_active(
        self,
        agent_id: UUID,
        platform_user_id: UUID,
        *,
        tenant_id: str = "",
        trace_id: str = "",
    ) -> dict[str, Any]:
        self.cancel_calls.append((agent_id, platform_user_id))
        if self.cancel_error is not None:
            raise self.cancel_error
        return {"run_id": str(uuid4()), "status": "CANCELLING"}


class FakeWeComSdkPort:
    def __init__(self, bot_id: str) -> None:
        self.bot_id = bot_id
        self.connected = False
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.fail_connect_times = 0
        self.auth_on_connect = True
        self.sent_texts: list[tuple[str, str]] = []
        self.stream_updates: list[tuple[str, str, str, bool]] = []
        self._on_message: Callable[[WeComInboundMessage], None] | None = None
        self._on_event: Callable[[WeComInboundEvent], None] | None = None
        self._on_authenticated: Callable[[], None] | None = None
        self._on_disconnected: Callable[[str], None] | None = None
        self._on_error: Callable[[Exception], None] | None = None

    @property
    def is_connected(self) -> bool:
        return self.connected

    def on_message(self, callback: Callable[[WeComInboundMessage], None]) -> None:
        self._on_message = callback

    def on_event(self, callback: Callable[[WeComInboundEvent], None]) -> None:
        self._on_event = callback

    def on_authenticated(self, callback: Callable[[], None]) -> None:
        self._on_authenticated = callback

    def on_disconnected(self, callback: Callable[[str], None]) -> None:
        self._on_disconnected = callback

    def on_error(self, callback: Callable[[Exception], None]) -> None:
        self._on_error = callback

    async def connect(self) -> None:
        self.connect_calls += 1
        if self.connect_calls <= self.fail_connect_times:
            raise ConnectionError("fake connect failure")
        self.connected = True
        if self.auth_on_connect and self._on_authenticated is not None:
            self._on_authenticated()

    def disconnect(self) -> None:
        self.disconnect_calls += 1
        self.connected = False

    async def send_text(self, chat_id: str, text: str) -> None:
        if not self.connected:
            raise RuntimeError("fake client not connected")
        self.sent_texts.append((chat_id, text))

    async def send_stream(self, reply_id: str, stream_id: str, content: str, *, finish: bool) -> None:
        if not self.connected:
            raise RuntimeError("fake client not connected")
        self.stream_updates.append((reply_id, stream_id, content, finish))

    def push_message(self, message: WeComInboundMessage) -> None:
        if self._on_message is None:
            raise AssertionError("message callback not registered")
        self._on_message(message)

    def push_event(self, event: WeComInboundEvent) -> None:
        if self._on_event is None:
            raise AssertionError("event callback not registered")
        self._on_event(event)

    def emit_disconnected(self, reason: str = "code: 1006") -> None:
        self.connected = False
        if self._on_disconnected is not None:
            self._on_disconnected(reason)

    def emit_error(self, error: Exception) -> None:
        if self._on_error is not None:
            self._on_error(error)


class FakeWeComSdkFactory:
    def __init__(self, *, fail_connect_times: int = 0) -> None:
        self.fail_connect_times = fail_connect_times
        self.clients: list[FakeWeComSdkPort] = []
        self.secrets: list[tuple[str, str]] = []

    def __call__(self, bot_id: str, secret: str) -> FakeWeComSdkPort:
        client = FakeWeComSdkPort(bot_id)
        client.fail_connect_times = self.fail_connect_times
        self.clients.append(client)
        self.secrets.append((bot_id, secret))
        return client

    def latest(self) -> FakeWeComSdkPort:
        if not self.clients:
            raise AssertionError("no sdk client built yet")
        return self.clients[-1]


def make_envelope(
    text: str = "帮我检查设备",
    *,
    message_id: str = "msg-1",
    external_user_id: str = "ext-1",
) -> ChannelEnvelope:
    return ChannelEnvelope(
        channel="WECOM",
        bot_id="bot-1",
        external_user_id=external_user_id,
        external_conversation_id="conv-1",
        message_id=message_id,
        text=text,
    )


def resolved_response(*, bound: bool = True, authorized: bool = True) -> ChannelResolveResponse:
    return ChannelResolveResponse(
        bound=bound,
        agent_id=uuid4(),
        platform_user_id=uuid4() if bound else None,
        authorized=authorized,
    )


class ConsoleProcess:
    """真实 Console 服务进程（真实 socket + 真实 PG），与验收栈同一启动口径。

    供需要"真实 Console HTTP"的用例复用；调用方负责 start/stop。
    """

    READY_TIMEOUT_SEC = 15.0

    def __init__(self) -> None:
        self._process: subprocess.Popen[bytes] | None = None
        self._log = tempfile.NamedTemporaryFile(
            prefix="console-test-", suffix=".log", delete=False
        )
        self.url = ""

    def start(self, tmp_root: Path) -> None:
        artifacts = tmp_root / "artifacts"
        cache = tmp_root / "skill-cache"
        artifacts.mkdir(parents=True, exist_ok=True)
        cache.mkdir(parents=True, exist_ok=True)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
        self._process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "muad_console_platform.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "error",
            ],
            env={
                **os.environ,
                "ARTIFACT_ROOT": str(artifacts),
                "SKILL_CACHE_ROOT": str(cache),
            },
            stdout=self._log,
            stderr=subprocess.STDOUT,
        )
        self.url = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + self.READY_TIMEOUT_SEC
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                raise RuntimeError(
                    "console exited early: "
                    f"{Path(self._log.name).read_text(errors='replace')[-1200:]}"
                )
            try:
                with httpx.Client(timeout=1.0) as client:
                    if client.get(f"{self.url}/healthz").status_code == 200:
                        return
            except httpx.HTTPError:
                time.sleep(0.1)
        raise RuntimeError("console did not become ready")

    def stop(self) -> None:
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._process.kill()
        self._log.close()


class StubConsole:
    """真实本地 HTTP 服务：uvicorn 线程 + 真实 socket，按用例脚本返回封套。"""

    READY_TIMEOUT_SEC = 10.0

    def __init__(self) -> None:
        self.handlers: dict[str, Callable[[Request], Awaitable[Response]]] = {}
        self.captured_headers: dict[str, dict[str, str]] = {}
        app = FastAPI()

        @app.api_route("/{path:path}", methods=["GET", "POST"])
        async def _dispatch(request: Request, path: str) -> Response:
            key = f"/{path}"
            self.captured_headers[key] = dict(request.headers)
            handler = self.handlers.get(key)
            if handler is None:
                return JSONResponse(
                    status_code=404, content={"code": "COMMON_NOT_FOUND", "msg": "no stub handler"}
                )
            return await handler(request)

        self._app = app
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self.url = ""

    def json_response(self, path: str, payload: Any, status_code: int = 200) -> None:
        async def handler(_: Request) -> Response:
            return JSONResponse(status_code=status_code, content=payload)

        self.handlers[path] = handler

    def start(self) -> None:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
        config = uvicorn.Config(self._app, host="127.0.0.1", port=port, log_level="error")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + self.READY_TIMEOUT_SEC
        while not self._server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        if not self._server.started:
            raise RuntimeError("stub console did not start")
        self.url = f"http://127.0.0.1:{port}"

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)
