"""MCP Runtime 只承载共享 `McpClient` 的稳定错误码（E-01 / S-09）。"""

from __future__ import annotations

from muad_agent_runtime.application.mcp_runtime_adapter import McpToolError


def test_tool_error_uses_the_shared_tool_failure_code() -> None:
    error = McpToolError("MCP_TOOL_FAILED", "platform failure")

    assert error.code == "MCP_TOOL_FAILED"
    assert error.retryable is False


def test_auth_and_session_http_failures_are_classified() -> None:
    assert McpToolError("MCP_AUTH_FAILED", "http 401", status_code=401).code == "MCP_AUTH_FAILED"

    expired = McpToolError("MCP_SESSION_EXPIRED", "http 404", status_code=404, retryable=True)
    assert expired.code == "MCP_SESSION_EXPIRED"
    assert expired.retryable is True

    unavailable = McpToolError("MCP_TRANSPORT_FAILED", "http 500", status_code=500)
    assert unavailable.code == "MCP_TRANSPORT_FAILED"
    assert unavailable.retryable is False


def test_transport_and_protocol_failures_use_shared_codes() -> None:
    assert McpToolError("MCP_TRANSPORT_FAILED", "timed out").code == "MCP_TRANSPORT_FAILED"
    assert McpToolError("MCP_PROTOCOL_FAILED", "bad payload").code == "MCP_PROTOCOL_FAILED"
