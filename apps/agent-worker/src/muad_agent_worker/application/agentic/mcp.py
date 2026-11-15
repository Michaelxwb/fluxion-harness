"""冻结 MCP catalog 的 Worker 消费：ToolRegistry 注册 + Streamable HTTP `tools/call`。

- 工具定义只来自快照：本模块**不执行** `tools/list`，也不查询当前 catalog；
- 认证值在恢复时按 API-09 解析（快照只有 `secret_header` 与非密 headers）；
- 每次 `tools/call` 都显式带 Accept 与认证头：借用装配根的共享池客户端没有本会话默认头。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx
from muad_contracts import SnapshotMcpServer, SnapshotTool

logger = logging.getLogger("muad_agent_worker.application.agentic.mcp")

JSON_RPC_VERSION = "2.0"
MCP_PROTOCOL_VERSION = "2025-03-26"
MCP_ACCEPT = "application/json, text/event-stream"
MCP_CLIENT_NAME = "muad-agent-worker"
DEFAULT_TIMEOUT_SEC = 10.0
MCP_TOOL_PREFIX = "mcp"


class McpToolError(RuntimeError):
    """MCP 传输/协议/工具执行失败；正文不携带认证头。"""

    def __init__(self, reason: str, detail: str, *, code: str = "MCP_TOOL_FAILED") -> None:
        self.reason, self.detail, self.code = reason, detail, code
        super().__init__(f"MCP {reason}: {detail}")


def mcp_tool_name(server_key: str, tool_name: str) -> str:
    """与 Runtime/目录同一命名：`mcp::<server_key>::<tool_name>`。"""
    return f"{MCP_TOOL_PREFIX}::{server_key}::{tool_name}"


@dataclass(frozen=True, slots=True)
class McpServerRuntime:
    """一个冻结 server 的执行事实：非密 headers + 认证值（只在内存）。"""

    server: SnapshotMcpServer
    auth_secret: str | None


class _McpSession:
    def __init__(
        self,
        runtime: McpServerRuntime,
        client: httpx.AsyncClient,
        *,
        timeout_sec: float,
    ) -> None:
        self._server = runtime.server
        self._client = client
        self._timeout_sec = timeout_sec
        self._headers: dict[str, str] = dict(self._server.headers)
        self._headers["Accept"] = MCP_ACCEPT
        if runtime.auth_secret:
            if self._server.secret_header:
                self._headers[self._server.secret_header] = runtime.auth_secret
            else:
                self._headers["Authorization"] = f"Bearer {runtime.auth_secret}"
        self._session_id: str | None = None
        self._initialized = False
        self._request_seq = 0

    async def initialize(self) -> None:
        if self._initialized:
            return
        await self._post(
            {
                "jsonrpc": JSON_RPC_VERSION,
                "id": self._next_request_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": MCP_CLIENT_NAME, "version": "1.0.0"},
                },
            }
        )
        await self._post({"jsonrpc": JSON_RPC_VERSION, "method": "notifications/initialized"})
        self._initialized = True

    async def call_tool(self, tool_name: str, arguments: Mapping[str, Any]) -> str:
        if not self._initialized:
            raise McpToolError("protocol", "session is not initialized")
        body = await self._post(
            {
                "jsonrpc": JSON_RPC_VERSION,
                "id": self._next_request_id(),
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": dict(arguments)},
            }
        )
        result = body.get("result") or {}
        if not isinstance(result, dict):
            raise McpToolError("protocol", "tools/call result is not an object")
        if result.get("isError") is True:
            raise McpToolError("tool_error", _content_text(result))
        return _content_text(result)

    def _next_request_id(self) -> int:
        self._request_seq += 1
        return self._request_seq

    def _request_headers(self) -> dict[str, str]:
        headers = dict(self._headers)
        headers["MCP-Protocol-Version"] = MCP_PROTOCOL_VERSION
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        request_id = payload.get("id")
        expects_response = request_id is not None
        try:
            response = await self._client.post(
                self._server.endpoint,
                json=payload,
                headers=self._request_headers(),
                timeout=self._timeout_sec,
            )
        except httpx.TimeoutException as exc:
            raise McpToolError("timeout", "request timed out") from exc
        except httpx.HTTPError as exc:
            raise McpToolError("connect", type(exc).__name__) from exc
        session_id = response.headers.get("mcp-session-id")
        if session_id:
            self._session_id = session_id
        if response.status_code >= 400:
            raise McpToolError("upstream_http", f"http {response.status_code}")
        if response.status_code == 202 or not response.content:
            if expects_response:
                raise McpToolError("protocol", "missing response payload")
            return {}
        body = self._decode(response)
        if expects_response and body.get("id") != request_id:
            raise McpToolError("protocol", "response id mismatch")
        if body.get("error") is not None:
            error = body["error"]
            message = error.get("message", "rpc error") if isinstance(error, dict) else "rpc error"
            raise McpToolError("protocol", str(message))
        return body

    @staticmethod
    def _decode(response: httpx.Response) -> dict[str, Any]:
        if "text/event-stream" in response.headers.get("content-type", ""):
            for line in response.text.splitlines():
                if not line.startswith("data:"):
                    continue
                try:
                    payload = json.loads(line[len("data:") :].strip())
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict) and ("result" in payload or "error" in payload):
                    return payload
            raise McpToolError("protocol", "empty sse payload")
        try:
            payload = response.json()
        except ValueError as exc:
            raise McpToolError("protocol", "invalid json payload") from exc
        if not isinstance(payload, dict):
            raise McpToolError("protocol", "payload is not an object")
        return payload


def _content_text(result: Mapping[str, Any]) -> str:
    content = result.get("content")
    if isinstance(content, list):
        return "".join(
            str(item.get("text", ""))
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        )
    if isinstance(content, str):
        return content
    return json.dumps(dict(result), ensure_ascii=False)


class McpToolBox:
    """冻结 MCP server 的会话池：每个 server 一次 initialize（singleflight）。"""

    def __init__(
        self,
        servers: Mapping[UUID, McpServerRuntime],
        client: httpx.AsyncClient,
        *,
        timeout_sec: float = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self._servers = dict(servers)
        self._client = client
        self._timeout_sec = timeout_sec
        self._sessions: dict[UUID, _McpSession] = {}
        self._locks: dict[UUID, asyncio.Lock] = {}

    async def call(self, server_id: UUID, tool_name: str, arguments: Mapping[str, Any]) -> str:
        runtime = self._servers.get(server_id)
        if runtime is None:
            raise McpToolError("unknown_server", "frozen MCP server is not configured")
        session = self._sessions.get(server_id)
        if session is None:
            session = _McpSession(runtime, self._client, timeout_sec=self._timeout_sec)
            self._sessions[server_id] = session
        lock = self._locks.setdefault(server_id, asyncio.Lock())
        async with lock:
            await session.initialize()
        return await session.call_tool(tool_name, arguments)


def frozen_mcp_tool_schema(server: SnapshotMcpServer, tool_name: str) -> dict[str, Any] | None:
    """从冻结 definitions 取该工具的完整 input schema（不查当前 catalog）。"""
    for definition in server.definitions:
        if definition.get("name") != tool_name:
            continue
        schema = definition.get("inputSchema") or definition.get("input_schema")
        if isinstance(schema, Mapping):
            return dict(schema)
    return None


def mcp_tool_trust(tools: tuple[SnapshotTool, ...], tool_name: str) -> SnapshotTool | None:
    """冻结的信任事实（retry_safety / schema_hash）；同名多源时取第一条 MCP 记录。"""
    for tool in tools:
        if tool.source == "MCP" and tool.tool_name == tool_name:
            return tool
    return None


__all__ = [
    "McpServerRuntime",
    "McpToolBox",
    "McpToolError",
    "frozen_mcp_tool_schema",
    "mcp_tool_name",
    "mcp_tool_trust",
]
