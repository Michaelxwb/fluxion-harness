from __future__ import annotations

import asyncio
import socket
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from typing import Any
from uuid import uuid4

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from muad_api import AppError
from muad_api.context import set_request_context
from muad_contracts import (
    ChannelBindRequest,
    ChannelBindResponse,
    ChannelResolveRequest,
    ChannelResolveResponse,
)
from muad_im_gateway.application.console_client import ConsoleClient

RESOLVE_DATA = {
    "bound": True,
    "agent_id": str(uuid4()),
    "platform_user_id": str(uuid4()),
    "authorized": True,
}


def _resolve_request() -> ChannelResolveRequest:
    return ChannelResolveRequest(channel="WECOM", bot_id="bot-1", external_user_id="ext-1")


def _bind_request() -> ChannelBindRequest:
    return ChannelBindRequest(
        channel="WECOM",
        bot_id="bot-1",
        external_user_id="ext-1",
        bind_code="ABC123",
    )


async def test_resolve_posts_and_parses_response() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"code": "0", "msg": "成功", "data": RESOLVE_DATA})

    client = ConsoleClient("http://console.test", transport=httpx.MockTransport(handler))
    try:
        resolved = await client.resolve(_resolve_request(), "tenant-1")
    finally:
        await client.aclose()

    assert isinstance(resolved, ChannelResolveResponse)
    assert resolved.bound is True
    assert resolved.authorized is True
    assert captured[0].url.path == "/internal/channel/resolve"
    assert captured[0].headers["x-tenant-id"] == "tenant-1"
    assert captured[0].headers["x-caller-service"] == "muad-im-gateway"


async def test_bind_parses_response() -> None:
    platform_user_id = uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "code": "0",
                "msg": "成功",
                "data": {"platform_user_id": str(platform_user_id), "bound": True},
            },
        )

    client = ConsoleClient("http://console.test", transport=httpx.MockTransport(handler))
    try:
        bound = await client.bind(_bind_request(), "tenant-1")
    finally:
        await client.aclose()
    assert isinstance(bound, ChannelBindResponse)
    assert bound.platform_user_id == platform_user_id


async def test_bind_maps_error_envelope() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"code": "BIND_CODE_INVALID", "msg": "invalid"})

    client = ConsoleClient("http://console.test", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(AppError) as excinfo:
            await client.bind(_bind_request(), "tenant-1")
    finally:
        await client.aclose()
    assert excinfo.value.code == "BIND_CODE_INVALID"


async def test_bots_parses_snapshot() -> None:
    payload = {
        "code": "0",
        "msg": "成功",
        "data": {
            "revision": "sha256:abc",
            "items": [
                {
                    "bot_account_id": str(uuid4()),
                    "bot_id": "bot-1",
                    "secret": "wecom-bot-1",
                    "agent_id": str(uuid4()),
                    "enabled": True,
                }
            ],
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client = ConsoleClient("http://console.test", transport=httpx.MockTransport(handler))
    try:
        snapshot = await client.bots("tenant-1")
    finally:
        await client.aclose()
    assert snapshot.revision == "sha256:abc"
    assert snapshot.items[0].bot_id == "bot-1"


def _skill_item_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "skill_id": str(uuid4()),
        "key": "policy-check",
        "name": "policy-check",
        "platform_label": "策略检查",
        "description": "检查客户设备策略",
    }
    payload.update(overrides)
    return payload


async def test_channel_skills_returns_typed_paged_response() -> None:
    captured: list[httpx.Request] = []
    agent_id = uuid4()
    platform_user_id = uuid4()
    item = _skill_item_payload()

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={
                "code": "0",
                "msg": "成功",
                "data": {"items": [item], "page": 1, "page_size": 20, "total": 1},
            },
        )

    client = ConsoleClient("http://console.test", transport=httpx.MockTransport(handler))
    try:
        skills = await client.channel_skills(agent_id, platform_user_id, "tenant-1")
    finally:
        await client.aclose()

    assert [skill.key for skill in skills.items] == ["policy-check"]
    assert skills.total == 1
    assert captured[0].url.path == "/internal/channel/skills"
    assert captured[0].url.params["agent_id"] == str(agent_id)
    assert captured[0].url.params["platform_user_id"] == str(platform_user_id)


async def test_channel_skills_rejects_bare_list_envelope() -> None:
    """旧未分页形态（裸数组 data）不再是合法契约。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"code": "0", "msg": "成功", "data": [_skill_item_payload()]},
        )

    client = ConsoleClient("http://console.test", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(AppError) as excinfo:
            await client.channel_skills(uuid4(), uuid4(), "tenant-1")
    finally:
        await client.aclose()
    assert excinfo.value.code == "COMMON_INTERNAL_ERROR"


async def test_channel_skills_404_is_an_error() -> None:
    """404 不得再伪装成空目录（依赖失败显式映射）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"code": "COMMON_NOT_FOUND", "msg": "nope"})

    client = ConsoleClient("http://console.test", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(AppError) as excinfo:
            await client.channel_skills(uuid4(), uuid4(), "tenant-1")
    finally:
        await client.aclose()
    assert excinfo.value.code == "COMMON_NOT_FOUND"


async def test_resolve_maps_transport_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = ConsoleClient("http://console.test", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(AppError) as excinfo:
            await client.resolve(_resolve_request(), "tenant-1")
    finally:
        await client.aclose()
    assert excinfo.value.code == "COMMON_INTERNAL_ERROR"

# ---------------------------------------------------------------------------
# B-102: 生产 ConsoleClient → 真实本地 HTTP 服务（uvicorn + 真实 socket）→ Envelope 解码
# ---------------------------------------------------------------------------

READY_TIMEOUT_SEC = 10.0
INTERNAL_BODY_CANARY = "internal-envelope-body-canary"


class StubConsole:
    """真实本地 HTTP 服务：uvicorn 线程 + 真实 socket，按用例脚本返回封套。"""

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

    def json_response(
        self, path: str, payload: Any, status_code: int = 200
    ) -> None:
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
        deadline = time.monotonic() + READY_TIMEOUT_SEC
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


@pytest.fixture()
def stub_console() -> Iterator[StubConsole]:
    stub = StubConsole()
    stub.start()
    try:
        yield stub
    finally:
        stub.stop()


def _skills_envelope(items: list[dict[str, Any]], *, total: int | None = None) -> dict[str, Any]:
    return {
        "code": "0",
        "msg": "成功",
        "data": {
            "items": items,
            "page": 1,
            "page_size": 20,
            "total": len(items) if total is None else total,
        },
    }


async def test_b102_propagates_tenant_trace_and_request_headers(
    stub_console: StubConsole,
) -> None:
    set_request_context(
        locale="zh-CN", trace_id="trace-b102", request_id="req-b102", tenant_id="tenant-1"
    )
    stub_console.json_response("/internal/channel/skills", _skills_envelope([]))
    client = ConsoleClient(stub_console.url)
    try:
        await client.channel_skills(uuid4(), uuid4(), "tenant-1")
    finally:
        await client.aclose()

    headers = stub_console.captured_headers["/internal/channel/skills"]
    assert headers["x-tenant-id"] == "tenant-1"
    assert headers["x-trace-id"] == "trace-b102"
    assert headers["x-request-id"] == "req-b102"
    assert headers["x-caller-service"] == "muad-im-gateway"


async def test_b102_effective_empty_catalog_is_a_valid_response(stub_console: StubConsole) -> None:
    stub_console.json_response("/internal/channel/skills", _skills_envelope([]))
    client = ConsoleClient(stub_console.url)
    try:
        response = await client.channel_skills(uuid4(), uuid4(), "tenant-1")
    finally:
        await client.aclose()
    assert response.items == []
    assert response.total == 0
    assert response.page == 1


async def test_b102_not_found_is_an_error_not_an_empty_catalog(stub_console: StubConsole) -> None:
    stub_console.json_response(
        "/internal/channel/skills",
        {"code": "AGENT_ACCESS_DENIED", "msg": INTERNAL_BODY_CANARY},
        status_code=404,
    )
    client = ConsoleClient(stub_console.url)
    try:
        with pytest.raises(AppError) as excinfo:
            await client.channel_skills(uuid4(), uuid4(), "tenant-1")
    finally:
        await client.aclose()
    assert excinfo.value.code == "AGENT_ACCESS_DENIED"
    assert INTERNAL_BODY_CANARY not in str(excinfo.value)
    assert stub_console.url not in str(excinfo.value)


async def test_b102_bad_envelope_is_an_error_not_an_empty_catalog(stub_console: StubConsole) -> None:
    stub_console.json_response("/internal/channel/skills", [{"name": INTERNAL_BODY_CANARY}])
    client = ConsoleClient(stub_console.url)
    try:
        with pytest.raises(AppError) as excinfo:
            await client.channel_skills(uuid4(), uuid4(), "tenant-1")
    finally:
        await client.aclose()
    assert excinfo.value.code == "COMMON_INTERNAL_ERROR"
    assert INTERNAL_BODY_CANARY not in str(excinfo.value)


async def test_b102_timeout_is_an_error_not_an_empty_catalog(stub_console: StubConsole) -> None:
    async def slow(_: Request) -> Response:
        await asyncio.sleep(0.5)
        return JSONResponse(status_code=200, content=_skills_envelope([]))

    stub_console.handlers["/internal/channel/skills"] = slow
    client = ConsoleClient(stub_console.url, timeout_sec=0.15)
    try:
        with pytest.raises(AppError) as excinfo:
            await client.channel_skills(uuid4(), uuid4(), "tenant-1")
    finally:
        await client.aclose()
    assert excinfo.value.code == "COMMON_INTERNAL_ERROR"


async def test_b102_error_code_is_preserved_and_internal_details_are_not_echoed(
    stub_console: StubConsole,
) -> None:
    stub_console.json_response(
        "/internal/channel/resolve",
        {"code": "BOT_NOT_FOUND", "msg": INTERNAL_BODY_CANARY},
        status_code=404,
    )
    client = ConsoleClient(stub_console.url)
    try:
        with pytest.raises(AppError) as excinfo:
            await client.resolve(_resolve_request(), "tenant-1")
    finally:
        await client.aclose()
    assert excinfo.value.code == "BOT_NOT_FOUND"
    assert excinfo.value.data is None
    assert INTERNAL_BODY_CANARY not in str(excinfo.value)
    assert stub_console.url not in str(excinfo.value)


async def test_b102_skills_response_is_typed_for_contract_errors(stub_console: StubConsole) -> None:
    """封套 data 缺字段（如 skill_id/key）必须显式失败，不用宽泛 Any 掩盖。"""
    stub_console.json_response(
        "/internal/channel/skills",
        _skills_envelope([{"name": "policy-check", "platform_label": "策略"}]),
    )
    client = ConsoleClient(stub_console.url)
    try:
        with pytest.raises(AppError) as excinfo:
            await client.channel_skills(uuid4(), uuid4(), "tenant-1")
    finally:
        await client.aclose()
    assert excinfo.value.code == "COMMON_INTERNAL_ERROR"
