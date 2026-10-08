"""冻结 MCP catalog 的运行适配器：ToolRegistry 注册 + 执行经 Egress 策略/审计。

Run 内不执行 tools/list：工具定义来自 Snapshot 冻结的 catalog；执行时对
server.endpoint 发起 Streamable HTTP JSON-RPC `tools/call`（initialize 每 server 一次）。
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx
from muad_agent_core.tools import ToolDefinition, ToolEffect, ToolHandler, ToolRegistry
from muad_api import AppError
from muad_api.error_codes import ErrorCode

from ..infrastructure.audit_writer import RuntimeAuditWriter
from ..metrics import EGRESS_CALLS_METRIC, record_outcome

JSON_RPC_VERSION = "2.0"
MCP_PROTOCOL_VERSION = "2025-03-26"
MCP_ACCEPT = "application/json, text/event-stream"
MCP_CLIENT_NAME = "muad-agent-runtime"
MCP_CALL_TIMEOUT_SEC = 10.0
# Egress 审计与 `egress_calls_total{platform}` 共用同一取值（非资源 ID，不含 endpoint）。
EGRESS_TARGET_TYPE = "MCP"


class McpToolError(RuntimeError):
    """MCP 传输/协议/工具执行失败；详情不携带凭据。"""

    def __init__(self, reason: str, detail: str) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"MCP {reason}: {detail}")


@dataclass(frozen=True, slots=True)
class McpToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    effect: str = "READ"


@dataclass(frozen=True, slots=True)
class McpServerDefinition:
    mcp_server_id: uuid.UUID
    key: str
    endpoint: str
    catalog_revision: int = 0
    catalog_hash: str | None = None
    tools: list[McpToolDefinition] = field(default_factory=list)
    auth_secret: str | None = field(default=None, repr=False)


def tool_registry_name(server_key: str, tool_name: str) -> str:
    """docs/07 §4.1：mcp::<server_key>::<tool_name>。"""
    return f"mcp::{server_key}::{tool_name}"


class _McpSession:
    """单个冻结 server 的 Streamable HTTP 会话。

    请求 ID 由会话内计数器提供（initialize / tools/call 每次递增、不复用）；响应 ID 必须与
    请求 ID 严格相等，缺失/错配按协议错误处理。初始化由 `McpRuntimeAdapter` 的 singleflight
    锁保护：成功才置 `_initialized`，失败关闭本会话并允许后续调用重建重试。
    """

    def __init__(
        self,
        server: McpServerDefinition,
        timeout_sec: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        headers = {"Accept": MCP_ACCEPT}
        if server.auth_secret:
            headers["Authorization"] = f"Bearer {server.auth_secret}"
        self._client = httpx.AsyncClient(
            headers=headers, timeout=httpx.Timeout(timeout_sec), transport=transport
        )
        self._endpoint = server.endpoint
        self._session_id: str | None = None
        self._initialized = False
        self._request_seq = 0

    @property
    def initialized(self) -> bool:
        return self._initialized

    async def aclose(self) -> None:
        await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        headers = {"MCP-Protocol-Version": MCP_PROTOCOL_VERSION}
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _next_request_id(self) -> int:
        self._request_seq += 1
        return self._request_seq

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        request_id = payload.get("id")
        expects_response = request_id is not None
        try:
            response = await self._client.post(
                self._endpoint, json=payload, headers=self._headers()
            )
        except httpx.TimeoutException as exc:
            raise McpToolError("timeout", "request timed out") from exc
        except httpx.HTTPError as exc:
            # 不携带请求头（含 auth）内容，避免 Secret 泄漏
            raise McpToolError("connect", type(exc).__name__) from exc
        session_id = response.headers.get("mcp-session-id")
        if session_id:
            self._session_id = session_id
        if response.status_code >= 400:
            raise McpToolError("protocol", f"http {response.status_code}")
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
        content_type = response.headers.get("content-type", "")
        if "text/event-stream" in content_type:
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

    async def initialize(self) -> None:
        """initialize + initialized 通知；由调用方的 singleflight 锁串行化，成功才置位。"""
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


def _content_text(result: Mapping[str, Any]) -> str:
    content = result.get("content")
    if isinstance(content, list):
        parts = [
            str(item.get("text", ""))
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        return "".join(parts)
    if isinstance(content, str):
        return content
    return json.dumps(dict(result), ensure_ascii=False)


class McpRuntimeAdapter:
    """Runtime 侧 MCP 消费：注册冻结 catalog 到 ToolRegistry；执行统一走 Egress 审计。"""

    def __init__(
        self,
        *,
        audit_writer: RuntimeAuditWriter | None,
        timeout_sec: float = MCP_CALL_TIMEOUT_SEC,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._audit_writer = audit_writer
        self._timeout_sec = timeout_sec
        self._transport = transport
        self._sessions: dict[str, _McpSession] = {}
        # 每 server 一把初始化 singleflight 锁：并发调用只初始化一次；失败释放锁并允许重试。
        self._session_locks: dict[str, asyncio.Lock] = {}

    async def aclose(self) -> None:
        for session in self._sessions.values():
            await session.aclose()
        self._sessions.clear()
        self._session_locks.clear()

    def register_catalog(
        self,
        *,
        registry: ToolRegistry,
        tenant_id: str,
        run_id: uuid.UUID,
        servers: list[McpServerDefinition],
    ) -> None:
        """将冻结 catalog 注册为 ToolRegistry 工具（handler 统一经 adapter 执行）。"""
        for server in servers:
            for tool in server.tools:
                registry.register(
                    ToolDefinition(
                        name=tool_registry_name(server.key, tool.name),
                        description=tool.description,
                        input_schema=tool.input_schema,
                        effect=_effect(tool.effect),
                        handler=self._make_handler(server, tool),
                    )
                )

    def _make_handler(
        self,
        server: McpServerDefinition,
        tool: McpToolDefinition,
    ) -> ToolHandler:
        async def handler(arguments: Mapping[str, Any], *, call_id: str) -> str:
            return await self.execute_tool(
                server=server,
                tool=tool,
                arguments=dict(arguments),
                policy_decision="ALLOW",
            )

        return handler

    async def execute_tool(
        self,
        *,
        server: McpServerDefinition,
        tool: McpToolDefinition,
        arguments: dict[str, Any],
        policy_decision: str,
    ) -> str:
        """MCP 工具执行统一入口：策略拒绝先落 DENY 审计再抛 FORBIDDEN。

        任何失败（协议错误/超时/isError/取消）都明确向上抛出并**丢弃该 server 会话、关闭其
        HTTP 连接**：坏会话不得被后续调用复用，重试必须重新 initialize（E-14）。
        """
        target = f"mcp://{server.key}/{tool.name}"
        started = time.monotonic()
        if policy_decision == "DENY":
            await self._audit(target, "DENY", None, "DENIED", latency_ms=0)
            raise AppError(ErrorCode.FORBIDDEN)
        try:
            session = await self._ready_session(server)
            content = await session.call_tool(tool.name, arguments)
        except McpToolError as exc:
            await self._discard_session(server.key)
            await self._audit(
                target,
                policy_decision,
                None,
                "ERROR",
                latency_ms=int((time.monotonic() - started) * 1000),
                error_code=exc.reason,
            )
            raise
        except asyncio.CancelledError:
            await self._discard_session(server.key)
            raise
        await self._audit(
            target,
            policy_decision,
            200,
            "OK",
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        return content

    async def _ready_session(self, server: McpServerDefinition) -> _McpSession:
        """singleflight：并发调用只 initialize 一次；失败关闭会话并释放锁，允许重试。"""
        lock = self._session_locks.setdefault(server.key, asyncio.Lock())
        async with lock:
            session = self._sessions.get(server.key)
            if session is not None:
                return session
            session = _McpSession(server, self._timeout_sec, self._transport)
            try:
                await session.initialize()
            except BaseException:
                await session.aclose()
                raise
            self._sessions[server.key] = session
            return session

    async def _discard_session(self, server_key: str) -> None:
        session = self._sessions.pop(server_key, None)
        if session is not None:
            await session.aclose()

    async def _audit(
        self,
        target: str,
        decision: str,
        status_code: int | None,
        result_status: str,
        *,
        latency_ms: int | None = None,
        error_code: str | None = None,
    ) -> None:
        record_outcome(EGRESS_CALLS_METRIC, result_status, {"platform": EGRESS_TARGET_TYPE})
        if self._audit_writer is None:
            return
        await self._audit_writer.record_egress(
            target_type=EGRESS_TARGET_TYPE,
            target=target,
            policy_decision=decision,
            operation="mcp.tool",
            method="POST",
            status_code=status_code,
            result_status=result_status,
            latency_ms=latency_ms,
            error_code=error_code,
        )


def _effect(raw: str) -> ToolEffect:
    try:
        return ToolEffect(raw)
    except ValueError:
        return ToolEffect.WRITE
