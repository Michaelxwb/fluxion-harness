"""Shared MCP client: strict ids, session rebuild, retry formula, bounded content."""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
from muad_contracts import ToolRetrySafety
from muad_platform_sdk.mcp_audit import McpAuditEvent
from muad_platform_sdk.mcp_client import McpClient, McpError, McpServerConfig
from muad_platform_sdk.mcp_content import convert_content
from muad_platform_sdk.mcp_limits import McpLimits


def _server() -> McpServerConfig:
    return McpServerConfig(
        server_id=uuid.uuid4(),
        key="server",
        endpoint="http://mcp/rpc",
        headers={"X-Test": "1"},
    )


def _client(handler, *, audit=None, limits=None) -> tuple[McpClient, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return McpClient(http, audit=audit, limits=limits), http


def _bearer_server(secret: str = "token-1") -> McpServerConfig:
    return McpServerConfig(
        server_id=uuid.uuid4(),
        key="bearer",
        endpoint="http://mcp/rpc",
        headers={"X-Test": "1"},
        auth_secret=secret,
    )


async def test_bearer_secret_is_sent_as_authorization_header() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        payload = json.loads(request.content)
        if payload["method"] == "notifications/initialized":
            return httpx.Response(202)
        if payload["method"] == "initialize":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": {}})
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": payload["id"], "result": {"content": []}},
        )

    client, http = _client(handler)
    try:
        await client.call(_bearer_server(), "lookup", {}, call_id="c1")
    finally:
        await http.aclose()

    assert seen[0].headers["authorization"] == "Bearer token-1"
    assert seen[1].headers["authorization"] == "Bearer token-1"
    assert seen[0].headers["X-Test"] == "1"


async def test_call_matches_request_id_and_converts_json_array_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload["method"] == "notifications/initialized":
            return httpx.Response(202)
        if payload["method"] == "initialize":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": {}})
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {"content": [{"type": "text", "text": "[1,2,3]"}]},
            },
        )

    client, http = _client(handler)
    try:
        result = await client.call(_server(), "lookup", {}, call_id="c1")
    finally:
        await http.aclose()
    assert result.text == "[1,2,3]"


async def test_http_200_is_error_is_a_logical_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload["method"] == "notifications/initialized":
            return httpx.Response(202)
        if payload["method"] == "initialize":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": {}})
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {"isError": True, "content": [{"type": "text", "text": "bad"}]},
            },
        )

    client, http = _client(handler)
    try:
        with pytest.raises(McpError) as caught:
            await client.call(
                _server(),
                "write",
                {},
                call_id="c1",
                retry_safety=ToolRetrySafety.NON_RETRYABLE,
            )
    finally:
        await http.aclose()
    assert caught.value.code == "MCP_TOOL_FAILED"
    assert caught.value.sent is True and caught.value.outcome_known is True
    assert caught.value.retryable is False

    client, http = _client(handler)
    try:
        with pytest.raises(McpError) as caught:
            await client.call(_server(), "write", {}, call_id="c2", retry_safety=ToolRetrySafety.IDEMPOTENT)
    finally:
        await http.aclose()
    assert caught.value.retryable is True


async def test_404_rebuilds_session_on_the_next_call() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload["method"] == "notifications/initialized":
            return httpx.Response(202)
        if payload["method"] == "initialize":
            calls["n"] += 1
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": {}})
        if calls["n"] == 1:
            return httpx.Response(404)
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": payload["id"], "result": {"content": []}},
        )

    client, http = _client(handler)
    try:
        with pytest.raises(McpError) as first:
            await client.call(_server(), "lookup", {}, call_id="c1")
        assert first.value.code == "MCP_SESSION_EXPIRED"
        assert first.value.sent is False and first.value.retryable is True
        result = await client.call(_server(), "lookup", {}, call_id="c2")
    finally:
        await http.aclose()
    assert result.text == ""
    assert calls["n"] == 2


async def test_transport_timeout_after_send_is_unknown_and_retryability_uses_replay_safety() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload["method"] == "notifications/initialized":
            return httpx.Response(202)
        if payload["method"] == "initialize":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": {}})
        raise httpx.ReadTimeout("slow", request=request)

    client, http = _client(handler)
    try:
        with pytest.raises(McpError) as caught:
            await client.call(_server(), "write", {}, call_id="c1")
    finally:
        await http.aclose()
    assert caught.value.code == "MCP_TRANSPORT_FAILED"
    assert caught.value.outcome_known is False and caught.value.retryable is False


async def test_content_conversion_keeps_images_as_artifacts() -> None:
    result = await convert_content(
        [
            {"type": "text", "text": "hello"},
            {
                "type": "image",
                "mimeType": "image/png",
                "data": "aGVsbG8=",
            },
        ],
        limits=McpLimits(),
    )
    assert "hello" in result.text
    assert result.artifacts[0].media_type == "image/png"
    assert result.artifacts[0].data == b"hello"


async def test_limits_reject_oversized_response_before_content_conversion() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload["method"] == "notifications/initialized":
            return httpx.Response(202)
        if payload["method"] == "initialize":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": {}})
        return httpx.Response(200, content=b"x" * 64)

    client, http = _client(handler, limits=McpLimits(max_response_bytes=8))
    try:
        with pytest.raises(McpError) as caught:
            await client.call(_server(), "lookup", {}, call_id="c1")
    finally:
        await http.aclose()
    assert caught.value.code == "MCP_CONTENT_TOO_LARGE"


class _Audit:
    def __init__(self) -> None:
        self.events: list[McpAuditEvent] = []

    async def record(self, event: McpAuditEvent) -> None:
        self.events.append(event)


async def test_logical_audit_records_ok_and_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload["method"] == "notifications/initialized":
            return httpx.Response(202)
        if payload["method"] == "initialize":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": {}})
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": payload["id"], "result": {"content": []}},
        )

    audit = _Audit()
    client, http = _client(handler, audit=audit)
    try:
        await client.call(_server(), "lookup", {}, call_id="c1")
    finally:
        await http.aclose()
    assert audit.events[-1].result_status == "OK"
    assert audit.events[-1].call_id == "c1"


async def test_sse_decode_uses_last_matching_event() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload["method"] == "notifications/initialized":
            return httpx.Response(202)
        if payload["method"] == "initialize":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": {}})
        event = {
            "jsonrpc": "2.0",
            "id": payload["id"],
            "result": {"content": [{"type": "text", "text": "sse"}]},
        }
        body = f"event: message\ndata: {json.dumps(event)}\n\n"
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    client, http = _client(handler)
    try:
        result = await client.call(_server(), "lookup", {}, call_id="c1")
    finally:
        await http.aclose()
    assert result.text == "sse"


async def test_rpc_error_and_id_mismatch_are_protocol_failures() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload["method"] == "notifications/initialized":
            return httpx.Response(202)
        if payload["method"] == "initialize":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": {}})
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": str(payload["id"]) + "-wrong", "result": {}},
        )

    client, http = _client(handler)
    try:
        with pytest.raises(McpError) as caught:
            await client.call(_server(), "lookup", {}, call_id="c1")
    finally:
        await http.aclose()
    assert caught.value.code == "MCP_PROTOCOL_FAILED"
    assert caught.value.retryable is False


async def test_protocol_headers_and_initialized_notification_are_sent() -> None:
    seen: list[tuple[str, httpx.Headers]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        method = str(payload["method"])
        seen.append((method, request.headers))
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "initialize":
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": payload["id"], "result": {}},
                headers={"Mcp-Session-Id": "s1"},
            )
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": payload["id"], "result": {"content": []}}
        )

    client, http = _client(handler)
    try:
        await client.call(_server(), "lookup", {}, call_id="c1")
    finally:
        await http.aclose()

    assert [method for method, _ in seen] == [
        "initialize",
        "notifications/initialized",
        "tools/call",
    ]
    for _, headers in seen:
        assert headers["accept"] == "application/json, text/event-stream"
        assert headers["mcp-protocol-version"] == "2025-03-26"


async def test_read_only_timeout_is_replay_safe_and_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload["method"] == "initialize":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": {}})
        raise httpx.ReadTimeout("slow", request=request)

    client, http = _client(handler)
    try:
        with pytest.raises(McpError) as caught:
            await client.call(
                _server(),
                "lookup",
                {},
                call_id="c1",
                retry_safety=ToolRetrySafety.READ_ONLY,
            )
    finally:
        await http.aclose()
    assert caught.value.code == "MCP_TRANSPORT_FAILED"
    assert caught.value.retryable is True


async def test_streaming_response_stops_at_the_byte_limit() -> None:
    read = {"bytes": 0}

    class _Chunks(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(64):
                chunk = b"x" * 1024
                read["bytes"] += len(chunk)
                yield chunk

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload["method"] == "initialize":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": {}})
        return httpx.Response(200, stream=_Chunks())

    client, http = _client(handler, limits=McpLimits(max_response_bytes=4096))
    try:
        with pytest.raises(McpError) as caught:
            await client.call(_server(), "lookup", {}, call_id="c1")
    finally:
        await http.aclose()
    assert caught.value.code == "MCP_CONTENT_TOO_LARGE"
    assert read["bytes"] <= 4096 + 1024


async def test_image_content_gets_an_artifact_id_from_the_writer() -> None:
    class _Writer:
        async def write(self, artifact) -> str:
            assert artifact.media_type == "image/png"
            return "artifact-1"

    result = await convert_content(
        [{"type": "image", "mimeType": "image/png", "data": "aGVsbG8="}],
        limits=McpLimits(),
        writer=_Writer(),
    )

    assert result.artifacts[0].artifact_id == "artifact-1"
    assert "[artifact: artifact-1]" in result.text
