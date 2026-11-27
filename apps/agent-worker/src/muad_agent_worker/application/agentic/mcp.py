"""冻结 MCP catalog 的 Worker 消费：ToolRegistry 注册 + Streamable HTTP `tools/call`。

- 工具定义只来自快照：本模块**不执行** `tools/list`，也不查询当前 catalog；
- 认证值在恢复时按 API-09 解析（快照只有 `secret_header` 与非密 headers）；
- 每次 `tools/call` 都显式带 Accept 与认证头：借用装配根的共享池客户端没有本会话默认头。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx
from muad_contracts import SnapshotMcpServer, SnapshotTool
from muad_logging import get_logger
from muad_platform_sdk.mcp_audit import McpAuditPort
from muad_platform_sdk.mcp_content import McpArtifactWriter, McpResult

logger = get_logger("muad_agent_worker.application.agentic.mcp")

JSON_RPC_VERSION = "2.0"
MCP_PROTOCOL_VERSION = "2025-03-26"
MCP_ACCEPT = "application/json, text/event-stream"
MCP_CLIENT_NAME = "muad-agent-worker"
DEFAULT_TIMEOUT_SEC = 10.0
MCP_TOOL_PREFIX = "mcp"


class McpToolError(RuntimeError):
    """MCP 传输/协议/工具执行失败；只携带共享 client 的稳定 code。"""

    def __init__(
        self,
        code: str,
        detail: str,
        *,
        retryable: bool = False,
        status_code: int | None = None,
        retry_after: str | None = None,
        sent: bool = False,
        outcome_known: bool = True,
    ) -> None:
        self.code, self.detail = code, detail
        self.retryable = retryable
        self.status_code = status_code
        self.retry_after = retry_after
        self.sent = sent
        self.outcome_known = outcome_known
        super().__init__(f"{code}: {detail}")


def mcp_tool_name(server_key: str, tool_name: str) -> str:
    """与 Runtime/目录同一命名：`mcp::<server_key>::<tool_name>`。"""
    return f"{MCP_TOOL_PREFIX}::{server_key}::{tool_name}"


@dataclass(frozen=True, slots=True)
class McpServerRuntime:
    """一个冻结 server 的执行事实：非密 headers + 认证值（只在内存）。"""

    server: SnapshotMcpServer
    auth_secret: str | None


class McpToolBox:
    """Frozen MCP server pool backed by the shared McpClient (singleflight init)."""

    def __init__(
        self,
        servers: Mapping[UUID, McpServerRuntime],
        client: httpx.AsyncClient,
        *,
        timeout_sec: float = DEFAULT_TIMEOUT_SEC,
        audit: McpAuditPort | None = None,
        artifact_writer: McpArtifactWriter | None = None,
    ) -> None:
        from muad_platform_sdk.mcp_client import McpClient

        self._servers = dict(servers)
        self._artifact_writer = artifact_writer
        self._client = McpClient(
            client,
            timeout_sec=timeout_sec,
            audit=audit,
            artifact_writer=artifact_writer,
            client_name=MCP_CLIENT_NAME,
        )

    async def call(
        self,
        server_id: UUID,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        retry_safety: Any = None,
        call_id: str = "mcp",
    ) -> McpResult:
        from muad_contracts import ToolRetrySafety
        from muad_platform_sdk.mcp_client import McpError, McpServerConfig

        runtime = self._servers.get(server_id)
        if runtime is None:
            raise McpToolError(
                "MCP_SERVER_UNAVAILABLE", "frozen MCP server is not configured"
            )
        server = runtime.server
        config = McpServerConfig(
            server_id=server.server_id,
            key=server.key,
            endpoint=server.endpoint,
            headers=dict(server.headers),
            secret_header=server.secret_header,
            auth_secret=runtime.auth_secret,
        )
        try:
            result = await self._client.call(
                config,
                tool_name,
                arguments,
                retry_safety=retry_safety or ToolRetrySafety.NON_RETRYABLE,
                call_id=call_id,
            )
        except McpError as exc:
            raise McpToolError(
                exc.code, exc.message,
                retryable=exc.retryable,
                status_code=exc.status_code,
                retry_after=exc.retry_after,
                sent=exc.sent,
                outcome_known=exc.outcome_known,
            ) from exc
        if result.artifacts and self._artifact_writer is None:
            raise McpToolError(
                "MCP_ARTIFACT_UNAVAILABLE",
                "MCP media requires an artifact writer",
                sent=True,
            )
        return result


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
