"""[B-116] 冻结 MCP catalog 运行适配器：ToolRegistry 注册 + 审计（真实 PostgreSQL）。"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from muad_agent_core.tools import ToolRegistry
from muad_api import AppError
from muad_agent_runtime.application.mcp_runtime_adapter import (
    McpRuntimeAdapter,
    McpServerDefinition,
    McpToolDefinition,
)
from muad_agent_runtime.infrastructure.audit_writer import RuntimeAuditWriter
from muad_agent_runtime.infrastructure.db import get_session_factory

TENANT = f"mcpr-{uuid.uuid4()}"
RUN_ID = uuid.uuid4()
CONV_ID = uuid.uuid4()


def _server(name: str, tools: list[McpToolDefinition]) -> McpServerDefinition:
    return McpServerDefinition(
        mcp_server_id=uuid.uuid4(),
        key=f"mcpr-{name}",
        endpoint="http://127.0.0.1:9/mcp",
        catalog_revision=2,
        catalog_hash="sha256:" + "a" * 64,
        tools=tools,
    )


async def test_b116_freezes_catalog_into_tool_registry() -> None:
    """[B-116] 冻结 catalog 定义注册进 ToolRegistry，命名 mcp::<server>::<tool>。"""
    server = _server(
        "one",
        tools=[
            McpToolDefinition(
                name="search", description="Search things", input_schema={"type": "object"},
                effect="READ",
            )
        ],
    )
    adapter = McpRuntimeAdapter(audit_writer=None)
    registry = ToolRegistry()
    adapter.register_catalog(registry=registry, tenant_id=TENANT, run_id=RUN_ID, servers=[server])

    tool = registry.get("mcp::mcpr-one::search")
    assert tool.description == "Search things"
    assert tool.effect.value == "READ"
    assert tool.handler is not None  # 统一注册，执行经 adapter


async def test_b116_tool_namespacing_isolation() -> None:
    """[RULE-auth-001 引用] 不同 server 同名工具命名空间隔离。"""
    server_a = _server("a", tools=[McpToolDefinition(name="t", description="A.t", input_schema={"type": "object"}, effect="READ")])
    server_b = _server("b", tools=[McpToolDefinition(name="t", description="B.t", input_schema={"type": "object"}, effect="READ")])
    registry = ToolRegistry()
    adapter = McpRuntimeAdapter(audit_writer=None)
    adapter.register_catalog(registry=registry, tenant_id=TENANT, run_id=RUN_ID, servers=[server_a, server_b])
    assert registry.get("mcp::mcpr-a::t").description == "A.t"
    assert registry.get("mcp::mcpr-b::t").description == "B.t"


async def test_b116_execute_audits_disabled_server_denied() -> None:
    """[B-116/E-05 同策略] MCP 执行经 Egress 策略；禁用 server 执行被拒且落审计。"""
    async with get_session_factory()() as session:
        pass  # 审计表写入不需种子

    audit = RuntimeAuditWriter(
        tenant_id=TENANT,
        run_id=RUN_ID,
        task_id=None,
        conversation_id=CONV_ID,
        user_id=uuid.uuid4(),
        session_factory=get_session_factory,
    )
    server = _server(
        "deny",
        tools=[McpToolDefinition(name="t", description="d", input_schema={"type": "object"}, effect="READ")],
    )
    adapter = McpRuntimeAdapter(audit_writer=audit)

    with pytest.raises(AppError) as exc:
        await adapter.execute_tool(
            tenant_id=TENANT,
            server=server,
            tool=server.tools[0],
            arguments={},
            policy_decision="DENY",
        )
    assert exc.value.code == "FORBIDDEN"

    async with get_session_factory()() as session:
        from muad_agent_runtime.infrastructure.models.runtime import EgressAudit

        rows = (
            await session.execute(
                sa.select(EgressAudit).where(EgressAudit.tenant_id == TENANT)
            )
        ).scalars().all()
        assert rows, "MCP 拒绝未落 egress 审计"
        assert rows[0].policy_decision == "DENY"
        assert rows[0].target.startswith("mcp://mcpr-deny/")
