"""Runtime 的冻结 MCP catalog 适配：注册工具并统一走共享 `McpClient`。

Runtime 只负责把 `McpServerDefinition` 映射成共享 client 的配置、适配审计端口；
session、协议初始化、认证头、接收上限、retryable 与媒体转换全部由
`muad_platform_sdk.mcp_client` 提供，不再维护第二套会话实现。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
from muad_agent_core.tools import (
    ArtifactRef,
    ToolCall,
    ToolCapability,
    ToolDefinition,
    ToolEffect,
    ToolHandler,
    ToolRegistry,
    ToolResult,
)
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import ToolRetrySafety
from muad_platform_sdk.mcp_audit import McpAuditEvent, McpAuditPort
from muad_platform_sdk.mcp_client import McpClient, McpError, McpServerConfig
from muad_platform_sdk.mcp_content import McpArtifact, McpArtifactWriter, McpResult
from muad_platform_sdk.mcp_limits import McpLimits
from muad_platform_sdk.platform_transport import McpBudgetScope

from ..infrastructure.audit_writer import RuntimeAuditWriter
from ..metrics import EGRESS_CALLS_METRIC, record_outcome

MCP_CALL_TIMEOUT_SEC = 10.0
# Egress 审计与 `egress_calls_total{platform}` 共用同一取值（非资源 ID，不含 endpoint）。
EGRESS_TARGET_TYPE = "MCP"


class McpToolError(RuntimeError):
    """MCP 传输/协议/工具执行失败；只携带共享 client 的稳定 code。"""

    def __init__(
        self,
        code: str,
        detail: str,
        *,
        status_code: int | None = None,
        retry_after: str | None = None,
        retryable: bool = False,
    ) -> None:
        self.code = code
        self.detail = detail
        self.status_code = status_code
        self.retry_after = retry_after
        self.retryable = retryable
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True, slots=True)
class McpToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    effect: str = "READ"
    #: Owner 冻结的重放声明；effect 只做效果分类，不参与重试许可。
    retry_safety: str = "NON_RETRYABLE"


@dataclass(frozen=True, slots=True)
class McpServerDefinition:
    mcp_server_id: uuid.UUID
    key: str
    endpoint: str
    catalog_revision: int = 0
    catalog_hash: str | None = None
    tools: list[McpToolDefinition] = field(default_factory=list)
    auth_secret: str | None = field(default=None, repr=False)
    #: 非密静态请求头；写入前已由 Console 校验。
    headers: dict[str, str] = field(default_factory=dict)
    #: 承载 `auth_secret` 的请求头名；空表示 `Authorization: Bearer`。
    secret_header: str | None = None


def tool_registry_name(server_key: str, tool_name: str) -> str:
    """docs/07 §4.1：mcp::<server_key>::<tool_name>。"""
    return f"mcp::{server_key}::{tool_name}"


class _RuntimeMcpAudit(McpAuditPort):
    def __init__(self, writer: RuntimeAuditWriter | None) -> None:
        self._writer = writer

    async def record(self, event: McpAuditEvent) -> None:
        target = f"mcp://{event.server_key}/{event.tool_name}"
        record_outcome(EGRESS_CALLS_METRIC, event.result_status, {"platform": EGRESS_TARGET_TYPE})
        if self._writer is None:
            return
        await self._writer.record_egress(
            target_type=EGRESS_TARGET_TYPE,
            target=target,
            policy_decision="ALLOW",
            operation="mcp.tool",
            method="POST",
            status_code=event.http_status,
            result_status=event.result_status,
            latency_ms=event.latency_ms,
            error_code=event.error_code,
            attempt_id=event.attempt_id,
        )


class RuntimeMcpArtifactWriter(McpArtifactWriter):
    """把 MCP 媒体写成 Run Artifact；只接受已冻结的 Run 身份。"""

    def __init__(
        self,
        writer: Any,
        *,
        tenant_id: str,
        conversation_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> None:
        self._writer = writer
        self._tenant_id = tenant_id
        self._conversation_id = conversation_id
        self._run_id = run_id

    async def write(self, artifact: McpArtifact) -> str:
        artifact_id = await self._writer.persist_mcp_artifact(
            tenant_id=self._tenant_id,
            conversation_id=self._conversation_id,
            run_id=self._run_id,
            task_id=None,
            filename=artifact.filename or "mcp-artifact.bin",
            media_type=artifact.media_type,
            content=artifact.data,
        )
        return str(artifact_id)


class McpRuntimeAdapter:
    """Runtime 侧 MCP 消费：注册冻结 catalog 到 ToolRegistry；执行统一走共享 client。"""

    def __init__(
        self,
        *,
        audit_writer: RuntimeAuditWriter | None,
        timeout_sec: float = MCP_CALL_TIMEOUT_SEC,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
        budget: McpBudgetScope | None = None,
        artifact_writer: McpArtifactWriter | None = None,
        limits: McpLimits | None = None,
    ) -> None:
        self._audit_writer = audit_writer
        self._artifact_writer = artifact_writer
        self._owns_client = client is None
        self._http = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_sec), transport=transport
        )
        self._client = McpClient(
            self._http,
            limits=limits,
            audit=_RuntimeMcpAudit(audit_writer),
            artifact_writer=artifact_writer,
            budget=budget,
            timeout_sec=timeout_sec,
            client_name="muad-agent-runtime",
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http.aclose()

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
                        retry_safety=_retry_safety(tool.retry_safety),
                        handler=self._make_handler(server, tool),
                        capabilities=frozenset({ToolCapability.MCP_CALL}),
                    )
                )

    def _make_handler(
        self,
        server: McpServerDefinition,
        tool: McpToolDefinition,
    ) -> ToolHandler:
        async def handler(arguments: Any, *, call: ToolCall) -> ToolResult:
            result = await self.execute_tool_result(
                server=server,
                tool=tool,
                arguments=dict(arguments),
                policy_decision="ALLOW",
                call_id=call.call_id,
            )
            return ToolResult(
                result.text,
                artifacts=tuple(
                    ArtifactRef(
                        artifact_id=str(item.artifact_id),
                        media_type=item.media_type,
                        filename=item.filename,
                        size=len(item.data),
                    )
                    for item in result.artifacts
                    if item.artifact_id
                ),
            )

        return handler

    async def execute_tool_result(
        self,
        *,
        server: McpServerDefinition,
        tool: McpToolDefinition,
        arguments: dict[str, Any],
        policy_decision: str,
        call_id: str = "mcp",
    ) -> McpResult:
        if policy_decision == "DENY":
            target = f"mcp://{server.key}/{tool.name}"
            if self._audit_writer is not None:
                await self._audit_writer.record_egress(
                    target_type=EGRESS_TARGET_TYPE,
                    target=target,
                    policy_decision="DENY",
                    operation="mcp.tool",
                    method="POST",
                    result_status="DENIED",
                )
            raise AppError(ErrorCode.FORBIDDEN)
        config = McpServerConfig(
            server_id=server.mcp_server_id,
            key=server.key,
            endpoint=server.endpoint,
            headers=server.headers,
            secret_header=server.secret_header,
            auth_secret=server.auth_secret,
        )
        try:
            result = await self._client.call(
                config,
                tool.name,
                arguments,
                retry_safety=_retry_safety(tool.retry_safety),
                call_id=call_id,
            )
        except McpError as exc:
            raise McpToolError(
                exc.code,
                exc.message,
                status_code=exc.status_code,
                retry_after=exc.retry_after,
                retryable=exc.retryable,
            ) from exc
        if result.artifacts and self._artifact_writer is None:
            raise McpToolError(
                "MCP_ARTIFACT_UNAVAILABLE",
                "MCP media requires an artifact writer",
                retryable=False,
            )
        return result


def _effect(raw: str) -> ToolEffect:
    try:
        return ToolEffect(raw)
    except ValueError:
        return ToolEffect.WRITE


def _retry_safety(raw: str) -> ToolRetrySafety:
    try:
        return ToolRetrySafety(raw)
    except ValueError:
        return ToolRetrySafety.NON_RETRYABLE
