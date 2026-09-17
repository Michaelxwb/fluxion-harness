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
