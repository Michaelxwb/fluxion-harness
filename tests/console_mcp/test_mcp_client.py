"""[B-06] Async MCP contracts against a real Streamable HTTP probe."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Iterator

import httpx
import pytest
import uvicorn
from muad_console_platform.infrastructure.mcp_client import (
    McpClient,
    McpClientError,
    normalize_effect,
    normalize_tools,
)


@pytest.fixture(scope="module")
def probe_server() -> Iterator[str]:
    config = uvicorn.Config("tests.e2e.mcp_probe_app:app", host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1] if server.servers else 0
    yield f"http://127.0.0.1:{port}/mcp"
    server.should_exit = True
    thread.join(timeout=5)


async def test_initialize_and_list_tools_against_real_probe(probe_server: str) -> None:
    async with McpClient(probe_server) as client:
        assert (await client.initialize())["name"] == "mcp-probe"
        tools = await client.list_tools()
    assert [tool.name for tool in tools] == ["probe_tool_0", "probe_tool_1"]
    normalized = normalize_tools(tools)
    assert normalized[0]["name"] == "probe_tool_0"
    assert normalized[0]["effect"] == "WRITE"
    assert normalized[0]["input_schema"]["required"] == ["query"]


def test_normalize_effect_maps_annotations() -> None:
    assert normalize_effect({"annotations": {"readOnlyHint": True}}) == "READ"
    assert normalize_effect({"annotations": {"destructiveHint": True}}) == "DESTRUCTIVE"
    assert normalize_effect({"annotations": {}}) == "WRITE"
    assert normalize_effect({}) == "WRITE"


async def test_sse_transport_is_decoded(probe_server: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_PROBE_SSE", "1")
    async with McpClient(probe_server) as client:
        assert (await client.initialize())["name"] == "mcp-probe"
        tools = await client.list_tools()
    assert [tool.name for tool in tools] == ["probe_tool_0", "probe_tool_1"]


async def test_session_header_is_echoed(probe_server: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_PROBE_SESSION", "1")
    async with McpClient(probe_server) as client:
        assert (await client.initialize())["name"] == "mcp-probe"
        assert [tool.name for tool in await client.list_tools()] == ["probe_tool_0", "probe_tool_1"]
    async with McpClient(probe_server) as without_session:
        with pytest.raises(McpClientError) as rejected:
            await without_session.list_tools()
    assert rejected.value.reason == "protocol"


async def test_tools_list_follows_cursor_pagination(
    probe_server: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_PROBE_TOOLS", "5")
    monkeypatch.setenv("MCP_PROBE_PAGE_SIZE", "2")
    async with McpClient(probe_server) as client:
        await client.initialize()
        tools = await client.list_tools()
    assert [tool.name for tool in tools] == [f"probe_tool_{index}" for index in range(5)]


async def test_tools_list_empty_202_is_protocol_error(
    probe_server: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_PROBE_EMPTY_LIST", "1")
    async with McpClient(probe_server) as client:
        await client.initialize()
        with pytest.raises(McpClientError) as rejected:
            await client.list_tools()
    assert rejected.value.reason == "protocol"
    assert "missing response payload" in str(rejected.value)


async def test_tools_list_over_limit_fails_fast(
    probe_server: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_PROBE_TOOLS", "201")
    async with McpClient(probe_server) as client:
        await client.initialize()
        with pytest.raises(McpClientError) as rejected:
            await client.list_tools(max_tools=200)
    assert "exceeds limit" in str(rejected.value)


async def test_auth_header_passthrough(probe_server: str, monkeypatch: pytest.MonkeyPatch) -> None:
    async with McpClient(probe_server) as unauthenticated:
        await unauthenticated.initialize()
    monkeypatch.setenv("MCP_PROBE_REQUIRE_AUTH", "probe-secret")
    async with McpClient(probe_server) as unauthenticated:
        with pytest.raises(McpClientError) as rejected:
            await unauthenticated.initialize()
    assert rejected.value.reason == "protocol"
    async with McpClient(probe_server, auth_secret="probe-secret") as client:
        assert (await client.initialize())["name"] == "mcp-probe"


async def _captured_initialize_headers(client: McpClient) -> httpx.Headers:
    """用真实 httpx 序列化捕获 initialize 请求头（不 mock 头组装）。"""
    captured: list[httpx.Headers] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.headers)
        payload = json.loads(request.content)
        if payload.get("id") is None:
            return httpx.Response(202)
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "serverInfo": {"name": "header-capture", "version": "1.0.0"},
                },
            },
        )

    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with client:
        await client.initialize()
    assert captured
    return captured[0]


async def test_headers_none_mode_sends_no_authorization() -> None:
    headers = await _captured_initialize_headers(McpClient("http://mcp.test/mcp"))
    assert "authorization" not in headers
    assert headers["accept"] == "application/json, text/event-stream"


async def test_headers_bearer_mode_sends_bearer_token() -> None:
    headers = await _captured_initialize_headers(
        McpClient("http://mcp.test/mcp", auth_secret="bearer-secret")
    )
    assert headers["authorization"] == "Bearer bearer-secret"
    assert "x-api-key" not in headers


async def test_headers_bearer_mode_when_secret_header_is_empty() -> None:
    headers = await _captured_initialize_headers(
        McpClient("http://mcp.test/mcp", auth_secret="bearer-secret", secret_header="")
    )
    assert headers["authorization"] == "Bearer bearer-secret"


async def test_headers_custom_mode_sends_static_headers_and_raw_secret() -> None:
    headers = await _captured_initialize_headers(
        McpClient(
            "http://mcp.test/mcp",
            auth_secret="raw-secret",
            headers={"X-App-Id": "app-1", "X-Platform-Uid": "53842"},
            secret_header="X-Api-Key",
        )
    )
    assert headers["x-app-id"] == "app-1"
    assert headers["x-platform-uid"] == "53842"
    assert headers["x-api-key"] == "raw-secret"
    assert "authorization" not in headers


async def test_headers_custom_mode_without_secret_omits_secret_header() -> None:
    headers = await _captured_initialize_headers(
        McpClient(
            "http://mcp.test/mcp",
            headers={"X-App-Id": "app-1"},
            secret_header="X-Api-Key",
        )
    )
    assert headers["x-app-id"] == "app-1"
    assert "x-api-key" not in headers
    assert "authorization" not in headers


async def test_headers_input_mapping_is_not_mutated() -> None:
    static_headers = {"X-App-Id": "app-1"}
    client = McpClient(
        "http://mcp.test/mcp",
        auth_secret="raw-secret",
        headers=static_headers,
        secret_header="X-Api-Key",
    )
    await client._client.aclose()
    assert static_headers == {"X-App-Id": "app-1"}


async def test_tools_list_failure_raises_discovery_error(
    probe_server: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_PROBE_FAIL_LIST", "1")
    async with McpClient(probe_server) as client:
        await client.initialize()
        with pytest.raises(McpClientError) as exc:
            await client.list_tools()
    assert exc.value.reason == "protocol"
    assert "tools/list failed" in str(exc.value)


async def test_connection_failure_and_timeout(probe_server: str, monkeypatch: pytest.MonkeyPatch) -> None:
    async with McpClient("http://127.0.0.1:9/mcp", timeout_ms=1000) as unreachable:
        with pytest.raises(McpClientError) as refused:
            await unreachable.initialize()
    assert refused.value.reason in ("connect", "timeout")
    monkeypatch.setenv("MCP_PROBE_DELAY_MS", "1000")
    async with McpClient(probe_server, timeout_ms=100) as slow:
        start = time.monotonic()
        with pytest.raises(McpClientError) as timed_out:
            await slow.initialize()
    assert timed_out.value.reason == "timeout"
    assert time.monotonic() - start < 5


async def test_auth_secret_never_in_error_messages(probe_server: str) -> None:
    secret = "super-secret-token-value"
    async with McpClient("http://127.0.0.1:9/mcp", auth_secret=secret, timeout_ms=500) as client:
        with pytest.raises(McpClientError) as exc:
            await client.initialize()
    assert secret not in str(exc.value)
    assert secret not in repr(exc.value)


async def test_slow_mcp_yields_and_closes(probe_server: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_PROBE_DELAY_MS", "200")
    async with McpClient(probe_server) as client:
        pending = asyncio.create_task(client.initialize())
        await asyncio.sleep(0.04)
        assert not pending.done(), "slow MCP I/O must yield to other requests"
        assert (await pending)["name"] == "mcp-probe"
    assert client._client.is_closed


async def test_failed_mcp_closes_connections() -> None:
    client = McpClient("http://127.0.0.1:9/mcp", timeout_ms=100)
    with pytest.raises(McpClientError):
        async with client:
            await client.initialize()
    assert client._client.is_closed
