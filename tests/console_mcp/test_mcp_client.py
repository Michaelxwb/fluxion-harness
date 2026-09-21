"""[B-06] MCP Streamable HTTP 客户端对真实探针 Server 的契约（不 mock HTTP）。"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator

import pytest
import uvicorn


@pytest.fixture(scope="module")
def probe_server() -> Iterator[str]:
    config = uvicorn.Config("tests.e2e.mcp_probe_app:app", host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)

    def run() -> None:
        server.run()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1] if server.servers else 0
    yield f"http://127.0.0.1:{port}/mcp"
    server.should_exit = True
    thread.join(timeout=5)


def test_initialize_and_list_tools_against_real_probe(probe_server: str) -> None:
    """[B-06] initialize 握手 + tools/list normalize（真实 HTTP 探针）。"""
    from muad_console_platform.infrastructure.mcp_client import McpClient, normalize_tools

    client = McpClient(probe_server, timeout_ms=5000)
    server_info = client.initialize()
    assert server_info["name"] == "mcp-probe"
    tools = client.list_tools()
    assert [tool.name for tool in tools] == ["probe_tool_0", "probe_tool_1"]
    normalized = normalize_tools(tools)
    assert normalized[0]["name"] == "probe_tool_0"
    assert normalized[0]["effect"] == "WRITE"
    assert normalized[0]["input_schema"]["required"] == ["query"]


def test_normalize_effect_maps_annotations() -> None:
    from muad_console_platform.infrastructure.mcp_client import normalize_effect

    assert normalize_effect({"annotations": {"readOnlyHint": True}}) == "READ"
    assert normalize_effect({"annotations": {"destructiveHint": True}}) == "DESTRUCTIVE"
    assert normalize_effect({"annotations": {}}) == "WRITE"
    assert normalize_effect({}) == "WRITE"


def test_sse_transport_is_decoded(probe_server: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """[B-06] Streamable HTTP SSE 响应（text/event-stream）可解码。"""
    from muad_console_platform.infrastructure.mcp_client import McpClient

    monkeypatch.setenv("MCP_PROBE_SSE", "1")
    try:
        client = McpClient(probe_server, timeout_ms=5000)
        assert client.initialize()["name"] == "mcp-probe"
        tools = client.list_tools()
        assert [tool.name for tool in tools] == ["probe_tool_0", "probe_tool_1"]
    finally:
        monkeypatch.delenv("MCP_PROBE_SSE")


def test_session_header_is_echoed(probe_server: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """[B-06] initialize 返回 Mcp-Session-Id 后，后续请求必须携带（探针强制校验）。"""
    from muad_console_platform.infrastructure.mcp_client import McpClient, McpClientError

    monkeypatch.setenv("MCP_PROBE_SESSION", "1")
    try:
        client = McpClient(probe_server, timeout_ms=5000)
        assert client.initialize()["name"] == "mcp-probe"
        assert [tool.name for tool in client.list_tools()] == ["probe_tool_0", "probe_tool_1"]
        without_session = McpClient(probe_server, timeout_ms=5000)
        with pytest.raises(McpClientError) as rejected:
            without_session.list_tools()
        assert rejected.value.reason == "protocol"
    finally:
        monkeypatch.delenv("MCP_PROBE_SESSION")


def test_tools_list_follows_cursor_pagination(
    probe_server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """[B-06] tools/list 支持 nextCursor 分页并聚合全部工具。"""
    from muad_console_platform.infrastructure.mcp_client import McpClient

    monkeypatch.setenv("MCP_PROBE_TOOLS", "5")
    monkeypatch.setenv("MCP_PROBE_PAGE_SIZE", "2")
    try:
        client = McpClient(probe_server, timeout_ms=5000)
        client.initialize()
        tools = client.list_tools()
        assert [tool.name for tool in tools] == [f"probe_tool_{index}" for index in range(5)]
    finally:
        monkeypatch.delenv("MCP_PROBE_TOOLS")
        monkeypatch.delenv("MCP_PROBE_PAGE_SIZE")


def test_tools_list_empty_202_is_protocol_error(
    probe_server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """[B-06] tools/list 返回 202 空响应不得被当成"0 个工具"。"""
    from muad_console_platform.infrastructure.mcp_client import McpClient, McpClientError

    monkeypatch.setenv("MCP_PROBE_EMPTY_LIST", "1")
    try:
        client = McpClient(probe_server, timeout_ms=5000)
        client.initialize()
        with pytest.raises(McpClientError) as rejected:
            client.list_tools()
        assert rejected.value.reason == "protocol"
        assert "missing response payload" in str(rejected.value)
    finally:
        monkeypatch.delenv("MCP_PROBE_EMPTY_LIST")


def test_tools_list_over_limit_fails_fast(
    probe_server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from muad_console_platform.infrastructure.mcp_client import McpClient, McpClientError

    monkeypatch.setenv("MCP_PROBE_TOOLS", "201")
    try:
        client = McpClient(probe_server, timeout_ms=5000)
        client.initialize()
        with pytest.raises(McpClientError) as rejected:
            client.list_tools(max_tools=200)
        assert "exceeds limit" in str(rejected.value)
    finally:
        monkeypatch.delenv("MCP_PROBE_TOOLS")


def test_auth_header_passthrough(probe_server: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """[B-06] auth_secret 以 Authorization Bearer 透传；无凭据被探针拒绝。"""
    from muad_console_platform.infrastructure.mcp_client import McpClient, McpClientError

    unauthenticated = McpClient(probe_server, auth_secret=None, timeout_ms=5000)
    unauthenticated.initialize()  # 探针默认不强制鉴权

    monkeypatch.setenv("MCP_PROBE_REQUIRE_AUTH", "probe-secret")
    try:
        with pytest.raises(McpClientError) as rejected:
            McpClient(probe_server, auth_secret=None, timeout_ms=5000).initialize()
        assert rejected.value.reason == "protocol"

        client = McpClient(probe_server, auth_secret="probe-secret", timeout_ms=5000)
        assert client.initialize()["name"] == "mcp-probe"
    finally:
        monkeypatch.delenv("MCP_PROBE_REQUIRE_AUTH")


def test_tools_list_failure_raises_discovery_error(
    probe_server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """[B-06] tools/list 协议失败 → McpClientError(reason=protocol)。"""
    from muad_console_platform.infrastructure.mcp_client import McpClient, McpClientError

    monkeypatch.setenv("MCP_PROBE_FAIL_LIST", "1")
    try:
        client = McpClient(probe_server, timeout_ms=5000)
        client.initialize()
        with pytest.raises(McpClientError) as exc:
            client.list_tools()
        assert exc.value.reason == "protocol"
        assert "tools/list failed" in str(exc.value)
    finally:
        monkeypatch.delenv("MCP_PROBE_FAIL_LIST")


def test_connection_failure_and_timeout(
    probe_server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """[B-06] 连接拒绝 → reason=connect；真实慢响应 → reason=timeout。"""
    from muad_console_platform.infrastructure.mcp_client import McpClient, McpClientError

    # 不可达端点：连接拒绝或挂起超时（本机行为差异），均为传输层失败
    with pytest.raises(McpClientError) as refused:
        McpClient("http://127.0.0.1:9/mcp", timeout_ms=1000).initialize()
    assert refused.value.reason in ("connect", "timeout")

    # 真实探针延迟 1s，客户端 100ms 超时 → 超时路径
    monkeypatch.setenv("MCP_PROBE_DELAY_MS", "1000")
    try:
        slow = McpClient(probe_server, timeout_ms=100)
        start = time.monotonic()
        with pytest.raises(McpClientError) as timed_out:
            slow.initialize()
        assert timed_out.value.reason == "timeout"
        assert time.monotonic() - start < 5
    finally:
        monkeypatch.delenv("MCP_PROBE_DELAY_MS")


def test_auth_secret_never_in_error_messages(probe_server: str) -> None:
    """[RULE-secret-001 遵守] 异常消息不含 auth_secret 明文。"""
    from muad_console_platform.infrastructure.mcp_client import McpClient, McpClientError

    secret = "super-secret-token-value"
    with pytest.raises(McpClientError) as exc:
        McpClient("http://127.0.0.1:9/mcp", auth_secret=secret, timeout_ms=500).initialize()
    assert secret not in str(exc.value)
    assert secret not in repr(exc.value)
