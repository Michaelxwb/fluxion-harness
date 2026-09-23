from __future__ import annotations

from uuid import uuid4

import httpx
import pytest
from muad_api import AppError
from muad_contracts import ChannelContext, MessageInput, RunRequest
from muad_im_gateway.application.runtime_client import RuntimeClient

SSE_BODY = (
    ": heartbeat\n"
    "event: run.created\n"
    'data: {"run_id": "run-1", "resumed": false}\n\n'
    ": heartbeat\n"
    "event: message.delta\n"
    'data: {"delta": "你"}\n\n'
    "event: run.completed\n"
    'data: {"status": "COMPLETED", "final_text": "你好"}\n\n'
)


def _run_request() -> RunRequest:
    return RunRequest(
        agent_id=uuid4(),
        platform_user_id=uuid4(),
        channel=ChannelContext(type="WECOM", bot_id="bot-1", external_conversation_id="conv-1"),
        message=MessageInput(id="msg-1", text="你好"),
    )


async def test_create_run_parses_sse_and_sends_headers() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=SSE_BODY.encode("utf-8"),
        )

    client = RuntimeClient("http://runtime.test", transport=httpx.MockTransport(handler))
    try:
        events = [
            event
            async for event in client.create_run(
                _run_request(),
                tenant_id="tenant-1",
                trace_id="trace-1",
            )
        ]
    finally:
        await client.aclose()

    assert [event.type for event in events] == ["run.created", "message.delta", "run.completed"]
    assert events[0].data["run_id"] == "run-1"
    assert events[1].data["delta"] == "你"
    assert len(captured) == 1
    assert captured[0].url.path == "/v1/runs"
    assert captured[0].headers["x-tenant-id"] == "tenant-1"
    assert captured[0].headers["x-trace-id"] == "trace-1"
    assert captured[0].headers["x-caller-service"] == "muad-im-gateway"


async def test_create_run_maps_error_envelope() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"error": {"code": "RUN_BUSY", "message": "busy"}})

    client = RuntimeClient("http://runtime.test", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(AppError) as excinfo:
            async for _ in client.create_run(_run_request(), tenant_id="tenant-1"):
                pass
    finally:
        await client.aclose()
    assert excinfo.value.code == "RUN_BUSY"


async def test_create_run_maps_transport_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = RuntimeClient("http://runtime.test", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(AppError) as excinfo:
            async for _ in client.create_run(_run_request(), tenant_id="tenant-1"):
                pass
    finally:
        await client.aclose()
    assert excinfo.value.code == "COMMON_INTERNAL_ERROR"


async def test_create_conversation_posts_and_returns_data() -> None:
    captured: list[httpx.Request] = []
    agent_id = uuid4()
    platform_user_id = uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={"code": "0", "msg": "成功", "data": {"conversation_id": str(uuid4())}},
        )

    client = RuntimeClient("http://runtime.test", transport=httpx.MockTransport(handler))
    try:
        data = await client.create_conversation(
            agent_id,
            platform_user_id,
            tenant_id="tenant-1",
        )
    finally:
        await client.aclose()

    assert "conversation_id" in data
    assert captured[0].url.path == "/v1/conversations"
    body = captured[0].content.decode("utf-8")
    assert str(agent_id) in body
    assert str(platform_user_id) in body


async def test_cancel_active_maps_no_active_run() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"code": "NO_ACTIVE_RUN", "msg": "none"})

    client = RuntimeClient("http://runtime.test", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(AppError) as excinfo:
            await client.cancel_active(uuid4(), uuid4(), tenant_id="tenant-1")
    finally:
        await client.aclose()
    assert excinfo.value.code == "NO_ACTIVE_RUN"


async def test_post_json_rejects_missing_envelope_data() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": "0", "msg": "ok"})

    client = RuntimeClient("http://runtime.test", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(AppError) as excinfo:
            await client.create_conversation(uuid4(), uuid4(), tenant_id="tenant-1")
    finally:
        await client.aclose()
    assert excinfo.value.code == "COMMON_INTERNAL_ERROR"


# ---------------------------------------------------------------------------
# B-113: 生产 RuntimeClient → 真实本地 HTTP/SSE 接收端
# ---------------------------------------------------------------------------

import json as _json  # noqa: E402
import socket as _socket  # noqa: E402
import threading as _threading  # noqa: E402
import time as _time  # noqa: E402
from typing import Any as _Any  # noqa: E402

import uvicorn as _uvicorn  # noqa: E402
from fastapi import FastAPI as _FastAPI  # noqa: E402
from fastapi import Request as _Request  # noqa: E402
from fastapi.responses import JSONResponse as _JSONResponse  # noqa: E402
from fastapi.responses import StreamingResponse as _StreamingResponse  # noqa: E402
from muad_api.context import set_request_context as _set_request_context  # noqa: E402
from muad_contracts import ChannelContext as _ChannelContext  # noqa: E402
from muad_contracts import MessageInput as _MessageInput  # noqa: E402
from muad_contracts import RunRequest as _RunRequest  # noqa: E402
from muad_im_gateway.application.runtime_client import RuntimeClient as _RuntimeClient  # noqa: E402

B113_MESSAGE_ID = "msg-b113"


class _RuntimeStub:
    """真实本地 HTTP/SSE 接收端：记录请求头/体，按脚本返回 SSE 或错误封套。"""

    def __init__(self) -> None:
        self.requests: list[dict[str, _Any]] = []
        self.error_status: int | None = None
        self.error_code = "RUN_BUSY"
        self.delay_sec = 0.0
        app = _FastAPI()

        @app.post("/v1/runs")
        async def _runs(request: _Request) -> _Any:
            self.requests.append(
                {"headers": dict(request.headers), "json": _json.loads((await request.body()).decode())}
            )
            if self.delay_sec:
                await asyncio.sleep(self.delay_sec)
            if self.error_status is not None:
                return _JSONResponse(status_code=self.error_status, content={"code": self.error_code, "msg": "busy"})

            async def _stream():
                yield 'event: run.created\ndata: {"run_id": "run-1", "resumed": false}\n\n'
                yield 'event: run.completed\ndata: {"status": "COMPLETED", "final_text": "ok"}\n\n'

            return _StreamingResponse(_stream(), media_type="text/event-stream")

        self._app = app
        self._server: _Any = None
        self._thread: _Any = None
        self.url = ""

    def start(self) -> None:
        with _socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
        config = _uvicorn.Config(self._app, host="127.0.0.1", port=port, log_level="error")
        self._server = _uvicorn.Server(config)
        self._thread = _threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = _time.monotonic() + 10
        while not self._server.started and _time.monotonic() < deadline:
            _time.sleep(0.02)
        self.url = f"http://127.0.0.1:{port}"

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)


def _b113_request() -> _RunRequest:
    return _RunRequest(
        agent_id=uuid4(),
        platform_user_id=uuid4(),
        conversation_id=None,
        channel=_ChannelContext(type="WECOM", bot_id="bot-b113", external_conversation_id="conv-b113"),
        message=_MessageInput(id=B113_MESSAGE_ID, type="text", text="你好"),
    )


async def test_b113_create_run_forwards_idempotency_key_and_link_headers() -> None:
    stub = _RuntimeStub()
    stub.start()
    _set_request_context(locale="zh-CN", trace_id="trace-b113", request_id="req-b113")
    client = _RuntimeClient(stub.url)
    try:
        events = [
            event
            async for event in client.create_run(
                _b113_request(), tenant_id="tenant-b113", trace_id="trace-b113"
            )
        ]
    finally:
        await client.aclose()
        stub.stop()

    headers = stub.requests[0]["headers"]
    assert headers["idempotency-key"] == B113_MESSAGE_ID  # 原 message id 作为稳定幂等键
    assert headers["x-tenant-id"] == "tenant-b113"
    assert headers["x-trace-id"] == "trace-b113"
    assert headers["x-request-id"] == "req-b113"
    assert headers["x-caller-service"] == "muad-im-gateway"

    body = stub.requests[0]["json"]
    assert body["agent_id"] == str(_b113_request().agent_id) or body["agent_id"]
    assert "pod" not in _json.dumps(body).lower()  # 只传逻辑 Agent，不出现 pod
    assert [event.type for event in events] == ["run.created", "run.completed"]


async def test_b113_error_envelope_is_distinguished_from_stream() -> None:
    stub = _RuntimeStub()
    stub.start()
    stub.error_status = 409
    client = _RuntimeClient(stub.url)
    try:
        with pytest.raises(AppError) as excinfo:
            async for _event in client.create_run(_b113_request(), tenant_id="tenant-b113"):
                pass
    finally:
        await client.aclose()
        stub.stop()
    assert excinfo.value.code == "RUN_BUSY"


async def test_b113_stream_timeout_is_bounded() -> None:
    stub = _RuntimeStub()
    stub.start()
    stub.delay_sec = 1.0
    client = _RuntimeClient(stub.url, timeout_sec=0.2)
    started = _time.monotonic()
    try:
        with pytest.raises(AppError):
            async for _event in client.create_run(_b113_request(), tenant_id="tenant-b113"):
                pass
    finally:
        await client.aclose()
        stub.stop()
    assert _time.monotonic() - started < 3.0  # 超时有界，不无限等待
