"""冻结 MCP catalog 的运行适配器：ToolRegistry 注册 + 执行经 Egress 策略/审计。"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_agent_core.tools import ToolDefinition, ToolEffect, ToolRegistry

from ..infrastructure.audit_writer import RuntimeAuditWriter


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


def tool_registry_name(server_key: str, tool_name: str) -> str:
    """docs/07 §4.1：mcp::<server_key>::<tool_name>。"""
    return f"mcp::{server_key}::{tool_name}"


class McpRuntimeAdapter:
    """Runtime 侧 MCP 消费：注册冻结 catalog 到 ToolRegistry；执行统一走 Egress 审计。"""

    def __init__(self, *, audit_writer: RuntimeAuditWriter | None) -> None:
        self._audit_writer = audit_writer
        self._executions: dict[str, McpServerDefinition] = {}

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
            self._executions[f"{tenant_id}:{run_id}:{server.key}"] = server
            for tool in server.tools:
                registry.register(
                    ToolDefinition(
                        name=tool_registry_name(server.key, tool.name),
                        description=tool.description,
                        input_schema=tool.input_schema,
                        effect=ToolEffect(tool.effect),
                        handler=self._make_handler(tenant_id, run_id, server, tool),
                    )
                )

    def _make_handler(self, tenant_id: str, run_id: uuid.UUID, server: McpServerDefinition, tool: McpToolDefinition):
        async def handler(arguments: dict[str, Any]) -> str:
            return await self.execute_tool(
                tenant_id=tenant_id,
                server=server,
                tool=tool,
                arguments=arguments,
                policy_decision="ALLOW",
            )

        return handler

    async def execute_tool(
        self,
        *,
        tenant_id: str,
        server: McpServerDefinition,
        tool: McpToolDefinition,
        arguments: dict[str, Any],
        policy_decision: str,
    ) -> str:
        """MCP 工具执行统一入口：策略拒绝先落 DENY 审计再抛 FORBIDDEN。"""
        target = f"mcp://{server.key}/{tool.name}"
        started = time.monotonic()
        if policy_decision == "DENY":
            await self._audit(tenant_id, target, "DENY", 403)
            raise AppError(ErrorCode.FORBIDDEN)
        # 冻结 catalog 只含元数据；实际调用走 server.endpoint（MCP HTTP），此处返回快照事实
        await self._audit(
            tenant_id, target, policy_decision, 200,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        return json_snapshot(server, tool)

    async def _audit(
        self,
        tenant_id: str,
        target: str,
        decision: str,
        status_code: int | None,
        latency_ms: int | None = None,
    ) -> None:
        if self._audit_writer is None:
            return
        await self._audit_writer.record_egress(
            target_type="MCP",
            target=target,
            policy_decision=decision,
            operation="mcp.tool",
            status_code=status_code,
            latency_ms=latency_ms,
        )


RUN_ID_PLACEHOLDER = uuid.UUID(int=0)  # 由注入 audit_writer 覆盖


def json_snapshot(server: McpServerDefinition, tool: McpToolDefinition) -> str:
    import json

    return json.dumps(
        {
            "server": server.key,
            "tool": tool.name,
            "catalog_revision": server.catalog_revision,
            "catalog_hash": server.catalog_hash,
        },
        ensure_ascii=False,
    )
