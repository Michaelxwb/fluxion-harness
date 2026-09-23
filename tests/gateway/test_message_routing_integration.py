"""B-109 / E-01: resolve、未绑定与授权分流（真实 Console HTTP → PostgreSQL → Runtime 接收观测）。

不得 Mock 的真实边界：真实 Console 服务进程（真实 socket）+ 真实 PostgreSQL
（bot/identity/AgentAccessGrant）+ 真实 Runtime HTTP 接收端观测是否创建 Run。
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

# tests/ 需在 sys.path 上才能复用 console_channel 的真实 Console 夹具（租户/bot/identity/grant）
_TESTS_ROOT = Path(__file__).resolve().parents[1]
if str(_TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_TESTS_ROOT))

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from fakes import make_envelope
from muad_api.catalog import MessageCatalog
from muad_im_gateway.application.console_client import ConsoleClient
from muad_im_gateway.application.inbound import InboundPipeline
from muad_im_gateway.application.runtime_client import RuntimeClient
from muad_im_gateway.channels.fake import FakeChannelAdapter
from muad_im_gateway.infrastructure.dedupe import NullDedupeStore

from console_channel.conftest import (  # noqa: F401  (fixture reuse)
    ChannelContext,
    channel,
    database_guard,
)

RESOLVE_URL = "/internal/channel/resolve"
RUNS_PATH = "/v1/runs"
READY_TIMEOUT_SEC = 15.0
TENANT_ID = "tenant-routing"


class ConsoleProcess:
    """真实 Console 服务进程（真实 socket + 真实 PG），与验收栈同一启动口径。"""

    def __init__(self) -> None:
        self._process: subprocess.Popen[bytes] | None = None
        self._log = tempfile.NamedTemporaryFile(prefix="b109-console-", suffix=".log", delete=False)
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
        deadline = time.monotonic() + READY_TIMEOUT_SEC
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                raise RuntimeError(
                    f"console exited early: {Path(self._log.name).read_text(errors='replace')[-1200:]}"
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


class RuntimeReceiver:
    """真实 Runtime HTTP 接收端：记录是否创建 Run 与请求体。"""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        app = FastAPI()

        @app.post(RUNS_PATH)
        async def _runs(request: Request) -> StreamingResponse:
            self.requests.append(json.loads((await request.body()).decode("utf-8")))

            async def _stream() -> AsyncIterator[str]:
                yield 'event: run.created\ndata: {"run_id": "run-1", "resumed": false}\n\n'
                yield 'event: run.completed\ndata: {"status": "COMPLETED", "final_text": "ok"}\n\n'

            return StreamingResponse(_stream(), media_type="text/event-stream")

        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self.url = ""
        self._app = app

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


@pytest.fixture()
async def console_server(tmp_path: Path) -> AsyncIterator[ConsoleProcess]:
    server = ConsoleProcess()
    server.start(tmp_path)
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture()
async def runtime_receiver() -> AsyncIterator[RuntimeReceiver]:
    receiver = RuntimeReceiver()
    receiver.start()
    try:
        yield receiver
    finally:
        receiver.stop()


class RecordingConsoleClient(ConsoleClient):
    """只记录出站 resolve 请求（仍走真实 HTTP 到真实 Console）。"""

    def __init__(self, base_url: str) -> None:
        super().__init__(base_url)
        self.resolve_requests: list[Any] = []

    async def resolve(self, request: Any, tenant_id: str) -> Any:
        self.resolve_requests.append(request)
        return await super().resolve(request, tenant_id)


def _sent_texts(adapter: FakeChannelAdapter) -> list[str]:
    return [message.text for _route, message in adapter.sent]


async def _resolve_raw(console_url: str, channel_ctx: ChannelContext, bot_id: str, user: str) -> httpx.Response:
    async with httpx.AsyncClient(base_url=console_url, timeout=5.0) as client:
        return await client.post(
            RESOLVE_URL,
            json={"channel": "WECOM", "bot_id": bot_id, "external_user_id": user},
            headers={"X-Tenant-Id": channel_ctx.tenant_id},
        )


async def test_e01_unknown_disabled_and_deleted_bot_return_bot_not_found(
    console_server: ConsoleProcess, channel: ChannelContext
) -> None:
    from uuid import uuid4

    cases = [
        channel.disabled_bot_id,
        channel.deleted_bot_id,
        f"bot-unknown-{uuid4().hex[:8]}",
    ]
    for bot_id in cases:
        response = await _resolve_raw(
            console_server.url, channel, bot_id, channel.unbound_external_user_id
        )
        assert response.status_code in (403, 404), (bot_id, response.text)
        assert response.json()["code"] == "BOT_NOT_FOUND", bot_id


async def test_b109_unbound_and_unauthorized_are_normal_branches_without_run(
    console_server: ConsoleProcess,
    runtime_receiver: RuntimeReceiver,
    catalog: MessageCatalog,
    channel: ChannelContext,
) -> None:
    adapter = FakeChannelAdapter()
    console = ConsoleClient(console_server.url)
    runtime = RuntimeClient(runtime_receiver.url)
    pipeline = InboundPipeline(
        dedupe=NullDedupeStore(),
        console=console,
        runtime=runtime,
        catalog=catalog,
        tenant_id=channel.tenant_id,
        locale="zh-CN",
    )
    try:
        # 未绑定：bound=false 属正常分支（提示绑定，不创建 Run）
        await pipeline.handle(
            adapter,
            make_envelope(
                text="你好",
                message_id=f"msg-unbound-{time.time_ns()}",
                external_user_id=channel.unbound_external_user_id,
            ).model_copy(update={"bot_id": channel.bot_id}),
        )
        assert runtime_receiver.requests == []
        assert _sent_texts(adapter) != []

        # 已绑定但无 Agent 授权：拒绝访问，不创建 Run
        adapter2 = FakeChannelAdapter()
        await pipeline.handle(
            adapter2,
            make_envelope(
                text="你好",
                message_id=f"msg-ungranted-{time.time_ns()}",
                external_user_id=channel.ungranted_external_user_id,
            ).model_copy(update={"bot_id": channel.bot_id}),
        )
        assert runtime_receiver.requests == []
        assert _sent_texts(adapter2) != []
    finally:
        await console.aclose()
        await runtime.aclose()


async def test_b109_authorized_message_creates_run_with_matching_route(
    console_server: ConsoleProcess,
    runtime_receiver: RuntimeReceiver,
    catalog: MessageCatalog,
    channel: ChannelContext,
) -> None:
    adapter = FakeChannelAdapter()
    console = RecordingConsoleClient(console_server.url)
    runtime = RuntimeClient(runtime_receiver.url)
    pipeline = InboundPipeline(
        dedupe=NullDedupeStore(),
        console=console,
        runtime=runtime,
        catalog=catalog,
        tenant_id=channel.tenant_id,
        locale="zh-CN",
    )
    message_id = f"msg-route-{time.time_ns()}"
    envelope = make_envelope(
        text="普通消息",
        message_id=message_id,
        external_user_id=channel.bound_external_user_id,
    ).model_copy(update={"bot_id": channel.bot_id, "external_conversation_id": "conv-routing"})
    try:
        await pipeline.handle(adapter, envelope)
        assert len(runtime_receiver.requests) == 1
        body = runtime_receiver.requests[0]
        assert body["message"]["id"] == message_id          # message_id 原样
        assert body["channel"]["bot_id"] == channel.bot_id  # route 未串线
        assert body["channel"]["external_conversation_id"] == "conv-routing"  # 会话标识透传
        assert body["agent_id"] == str(channel.agent_id)    # 逻辑 Agent 正确
        assert "pod" not in json.dumps(body).lower()        # 不传 pod
        # resolve 请求透传会话标识（真实 HTTP 到真实 Console，仅记录出站 payload）
        assert console.resolve_requests[0].external_conversation_id == "conv-routing"
        assert console.resolve_requests[0].bot_id == channel.bot_id
    finally:
        await console.aclose()
        await runtime.aclose()
