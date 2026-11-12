"""TASK-004: 真实子进程验证假 MCP 的 Streamable HTTP 子集与三种认证。

边界：真实 HTTP 子进程（无 mock），Console 侧只使用现有 ``McpClient``。
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from muad_console_platform.infrastructure.mcp_client import (
    McpClient,
    McpClientError,
    normalize_effect,
)

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "tools/fake-business-platform/server.py"
BEARER_TOKEN = "test-mcp-token"
STATIC_HEADERS = {"X-App-Id": "sdspmcp", "X-Platform-Uid": "53842"}
SECRET_HEADERS = {"X-Api-Key": "test-key"}
TOOL_NAMES = [
    "platform.ping",
    "platform.targets.list",
    "platform.echo",
    "platform.targets.delete",
    "platform.fail",
]
EXPECTED_EFFECTS = {
    "platform.ping": "READ",
    "platform.targets.list": "READ",
    "platform.echo": "WRITE",
    "platform.targets.delete": "DESTRUCTIVE",
    "platform.fail": "WRITE",
}


@dataclass
class FakePlatform:
    ready: dict[str, Any]
    process: subprocess.Popen[str]
    logs: str = field(default="")

    @property
    def mcp_url(self) -> str:
        return str(self.ready["mcpUrl"])

    @property
    def base_url(self) -> str:
        return str(self.ready["baseUrl"])


@contextmanager
def running_platform(*extra_args: str) -> Iterator[FakePlatform]:
    process = subprocess.Popen(
        [sys.executable, str(SERVER), "--host", "127.0.0.1", "--port", "0", *extra_args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    line = process.stdout.readline()
    if not line:
        stderr = process.stderr.read() if process.stderr is not None else ""
        process.kill()
        process.wait(timeout=5)
        raise AssertionError(f"fake platform did not become ready: {stderr}")
    platform = FakePlatform(ready=json.loads(line), process=process)
    try:
        yield platform
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        platform.logs = process.stderr.read() if process.stderr is not None else ""


def initialize_payload(request_id: int = 1) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1.0.0"},
        },
    }


def tools_list_payload(request_id: int = 2) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": "tools/list"}


def tools_call_payload(
    name: str, arguments: dict[str, Any] | None = None, request_id: int = 3,
) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments or {}},
    }


def initialize_session(
    client: httpx.Client, url: str, headers: dict[str, str] | None = None,
) -> str:
    response = client.post(url, json=initialize_payload(), headers=headers or {})
    assert response.status_code == 200, response.text
    session_id = response.headers.get("mcp-session-id")
    assert session_id, "initialize must issue Mcp-Session-Id"
    return session_id


def call_tool(
    client: httpx.Client,
    url: str,
    session_id: str,
    name: str,
    arguments: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    merged = dict(headers or {})
    merged["Mcp-Session-Id"] = session_id
    response = client.post(url, json=tools_call_payload(name, arguments), headers=merged)
    assert response.status_code == 200, response.text
    body = response.json()
    assert "error" not in body, body
    result = body["result"]
    assert isinstance(result, dict)
    return result


def test_streamable_http_contract() -> None:
    with running_platform() as platform:
        with httpx.Client(timeout=5) as client:
            init = client.post(platform.mcp_url, json=initialize_payload())
            assert init.status_code == 200
            body = init.json()
            assert body["jsonrpc"] == "2.0"
            assert body["id"] == 1
            result = body["result"]
            assert result["protocolVersion"] == "2025-03-26"
            assert "tools" in result["capabilities"]
            assert result["serverInfo"]["name"] == "fake-business-platform"
            session_id = initialize_session(client, platform.mcp_url)

            notify = client.post(
                platform.mcp_url,
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                headers={"Mcp-Session-Id": session_id},
            )
            assert notify.status_code == 202
            assert notify.content == b""

            listing = client.post(
                platform.mcp_url, json=tools_list_payload(), headers={"Mcp-Session-Id": session_id}
            )
            assert listing.status_code == 200
            listed = listing.json()["result"]
            assert "nextCursor" not in listed
            tools = listed["tools"]
            assert [tool["name"] for tool in tools] == TOOL_NAMES
            by_name = {tool["name"]: tool for tool in tools}
            assert by_name["platform.ping"]["annotations"] == {"readOnlyHint": True}
            assert by_name["platform.targets.list"]["annotations"] == {"readOnlyHint": True}
            assert by_name["platform.targets.delete"]["annotations"] == {"destructiveHint": True}
            assert "annotations" not in by_name["platform.echo"]
            assert {
                name: normalize_effect(tool) for name, tool in by_name.items()
            } == EXPECTED_EFFECTS


def test_tools_call_returns_text_and_is_error() -> None:
    with running_platform() as platform:
        with httpx.Client(timeout=5) as client:
            session_id = initialize_session(client, platform.mcp_url)
            assert call_tool(client, platform.mcp_url, session_id, "platform.ping") == {
                "content": [{"type": "text", "text": "pong"}]
            }
            targets = json.loads(
                call_tool(client, platform.mcp_url, session_id, "platform.targets.list")
                ["content"][0]["text"]
            )
            assert len(targets["targets"]) == 3
            echo = call_tool(
                client, platform.mcp_url, session_id, "platform.echo", {"message": "hello"}
            )
            assert echo["content"][0]["text"] == "hello"
            deleted = json.loads(
                call_tool(
                    client, platform.mcp_url, session_id, "platform.targets.delete", {"id": "target-2"}
                )["content"][0]["text"]
            )
            assert deleted == {"deleted": "target-2"}
            failure = call_tool(client, platform.mcp_url, session_id, "platform.fail")
            assert failure["isError"] is True
            assert failure["content"][0]["text"]


def test_unknown_tool_is_jsonrpc_error() -> None:
    with running_platform() as platform:
        with httpx.Client(timeout=5) as client:
            session_id = initialize_session(client, platform.mcp_url)
            response = client.post(
                platform.mcp_url,
                json=tools_call_payload("platform.missing"),
                headers={"Mcp-Session-Id": session_id},
            )
            assert response.status_code == 200
            error = response.json()["error"]
            assert error["code"] == -32602
            assert "platform.missing" in error["message"]


def test_missing_or_unknown_session_is_401() -> None:
    with running_platform() as platform:
        with httpx.Client(timeout=5) as client:
            assert client.post(platform.mcp_url, json=initialize_payload()).status_code == 200
            for headers in ({}, {"Mcp-Session-Id": "not-a-session"}):
                response = client.post(platform.mcp_url, json=tools_list_payload(), headers=headers)
                assert response.status_code == 401, headers


def test_get_mcp_is_405_with_allow_post() -> None:
    with running_platform() as platform:
        response = httpx.get(platform.mcp_url, timeout=5)
        assert response.status_code == 405
        assert response.headers["allow"] == "POST"


async def test_bearer_mode_is_discoverable_by_mcp_client() -> None:
    with running_platform("--mcp-auth", "bearer", "--mcp-token", BEARER_TOKEN) as platform:
        assert platform.ready["mcpAuth"] == "bearer"
        assert platform.ready["mcpTools"] == TOOL_NAMES
        assert BEARER_TOKEN not in json.dumps(platform.ready)
        async with McpClient(platform.mcp_url, auth_secret=BEARER_TOKEN) as client:
            info = await client.initialize()
            tools = await client.list_tools()
        assert info["name"] == "fake-business-platform"
        assert {tool.name: tool.effect for tool in tools} == EXPECTED_EFFECTS


async def test_wrong_bearer_token_is_rejected() -> None:
    with running_platform("--mcp-auth", "bearer", "--mcp-token", BEARER_TOKEN) as platform:
        response = httpx.post(
            platform.mcp_url,
            json=initialize_payload(),
            headers={"Authorization": "Bearer wrong-token"},
            timeout=5,
        )
        assert response.status_code == 401
        async with McpClient(platform.mcp_url, auth_secret="wrong-token") as client:
            with pytest.raises(McpClientError) as rejected:
                await client.initialize()
        assert rejected.value.reason == "protocol"


def test_headers_mode_requires_every_configured_header() -> None:
    # McpClient 的自定义头支持由 TASK-001 提供；这里用原始 httpx 固定服务端契约：
    # 三个头缺一不可，且带齐后 tools/call 正常。
    with running_platform(
        "--mcp-auth", "headers",
        "--mcp-header", "X-App-Id=sdspmcp",
        "--mcp-header", "X-Platform-Uid=53842",
        "--mcp-header", "X-Api-Key=test-key",
    ) as platform:
        configured = {**STATIC_HEADERS, **SECRET_HEADERS}
        with httpx.Client(timeout=5) as client:
            session_id = initialize_session(client, platform.mcp_url, configured)
            for name in configured:
                partial = {key: value for key, value in configured.items() if key != name}
                partial["Mcp-Session-Id"] = session_id
                response = client.post(platform.mcp_url, json=tools_list_payload(), headers=partial)
                assert response.status_code == 401, f"missing {name} must be rejected"
            echo = call_tool(
                client,
                platform.mcp_url,
                session_id,
                "platform.echo",
                {"message": "headers-ok"},
                configured,
            )
            assert echo["content"][0]["text"] == "headers-ok"


def test_custom_mcp_path_moves_the_endpoint() -> None:
    with running_platform("--mcp-path", "/custom/mcp") as platform:
        assert platform.mcp_url == f"{platform.base_url}/custom/mcp"
        assert httpx.post(
            f"{platform.base_url}/mcp", json=initialize_payload(), timeout=5
        ).status_code == 404
        assert httpx.post(platform.mcp_url, json=initialize_payload(), timeout=5).status_code == 200


@pytest.mark.parametrize(
    ("extra_args", "message"),
    [
        (("--mcp-auth", "bearer"), "--mcp-token"),
        (("--mcp-auth", "headers"), "--mcp-header"),
    ],
)
def test_incomplete_auth_config_fails_at_startup(
    extra_args: tuple[str, ...], message: str,
) -> None:
    completed = subprocess.run(
        [sys.executable, str(SERVER), "--port", "0", *extra_args],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode != 0
    assert message in completed.stderr
    assert completed.stdout == ""


def test_logs_redact_authorization_and_cookie() -> None:
    with running_platform("--mcp-auth", "bearer", "--mcp-token", BEARER_TOKEN) as platform:
        httpx.post(
            platform.mcp_url,
            json=initialize_payload(),
            headers={
                "Authorization": f"Bearer {BEARER_TOKEN}",
                "Cookie": "fake_session=cookie-secret",
            },
            timeout=5,
        )
    lowered = platform.logs.lower()
    assert BEARER_TOKEN not in platform.logs
    assert "cookie-secret" not in platform.logs
    assert "authorization: ***" in lowered
    assert "cookie: ***" in lowered


def test_logs_redact_configured_headers() -> None:
    with running_platform(
        "--mcp-auth", "headers",
        "--mcp-header", "X-App-Id=sdspmcp",
        "--mcp-header", "X-Api-Key=test-key",
    ) as platform:
        httpx.post(
            platform.mcp_url,
            json=initialize_payload(),
            headers={"X-App-Id": "sdspmcp", "X-Api-Key": "test-key"},
            timeout=5,
        )
    lowered = platform.logs.lower()
    assert "sdspmcp" not in platform.logs
    assert "test-key" not in platform.logs
    assert "x-app-id: ***" in lowered
    assert "x-api-key: ***" in lowered
