"""[B-116] 冻结 MCP catalog 运行适配器：ToolRegistry 注册 + 真实 HTTP tools/call + 审计。"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from httpx import ASGITransport
from muad_agent_core.tools import ToolRegistry
from muad_agent_runtime.application.mcp_runtime_adapter import (
    McpRuntimeAdapter,
    McpServerDefinition,
    McpToolDefinition,
    McpToolError,
)
from muad_agent_runtime.infrastructure.audit_writer import RuntimeAuditWriter
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import EgressAudit
from muad_api import AppError

from tests.e2e.mcp_probe_app import app as probe_app

TENANT = f"mcpr-{uuid.uuid4()}"
RUN_ID = uuid.uuid4()
CONV_ID = uuid.uuid4()
USER_ID = uuid.uuid4()


@pytest.fixture
async def adapter():
    instance = McpRuntimeAdapter(
        audit_writer=_audit(), transport=ASGITransport(app=probe_app)
    )
    yield instance
    await instance.aclose()


def _audit() -> RuntimeAuditWriter:
    return RuntimeAuditWriter(
        tenant_id=TENANT,
        run_id=RUN_ID,
        task_id=None,
        conversation_id=CONV_ID,
        user_id=USER_ID,
        session_factory=get_session_factory,
    )


def _server(
    name: str,
    tools: list[McpToolDefinition],
    *,
    endpoint: str = "http://mcp-probe/mcp",
    auth_secret: str | None = None,
) -> McpServerDefinition:
    return McpServerDefinition(
        mcp_server_id=uuid.uuid4(),
        key=f"mcpr-{name}",
        endpoint=endpoint,
        catalog_revision=2,
        catalog_hash="sha256:" + "a" * 64,
        tools=tools,
        auth_secret=auth_secret,
    )


def _tool(name: str = "probe_tool_1") -> McpToolDefinition:
    return McpToolDefinition(
        name=name,
        description="Probe tool",
        input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
        effect="READ",
    )


async def _egress_rows() -> list[EgressAudit]:
    async with get_session_factory()() as session:
        rows = (
            await session.execute(
                sa.select(EgressAudit).where(EgressAudit.tenant_id == TENANT)
            )
        ).scalars().all()
        return list(rows)


async def _cleanup_audits() -> None:
    async with get_session_factory()() as session:
        await session.execute(EgressAudit.__table__.delete().where(EgressAudit.tenant_id == TENANT))
        await session.commit()


async def test_b116_freezes_catalog_into_tool_registry() -> None:
    """[B-116] 冻结 catalog 定义注册进 ToolRegistry，命名 mcp::<server>::<tool>。"""
    server = _server("one", [_tool("search")])
    adapter = McpRuntimeAdapter(audit_writer=None)
    registry = ToolRegistry()
    adapter.register_catalog(registry=registry, tenant_id=TENANT, run_id=RUN_ID, servers=[server])

    tool = registry.get("mcp::mcpr-one::search")
    assert tool.description == "Probe tool"
    assert tool.effect.value == "READ"
    assert tool.handler is not None  # 统一注册，执行经 adapter


async def test_b116_tool_namespacing_isolation() -> None:
    """[RULE-auth-001 引用] 不同 server 同名工具命名空间隔离。"""
    server_a = _server("a", [_tool("t")])
    server_b = _server("b", [_tool("t")])
    registry = ToolRegistry()
    adapter = McpRuntimeAdapter(audit_writer=None)
    adapter.register_catalog(
        registry=registry, tenant_id=TENANT, run_id=RUN_ID, servers=[server_a, server_b]
    )
    assert registry.get("mcp::mcpr-a::t").description == "Probe tool"
    assert registry.get("mcp::mcpr-b::t").description == "Probe tool"
    assert registry.get("mcp::mcpr-a::t") is not registry.get("mcp::mcpr-b::t")


async def test_b116_execute_audits_disabled_server_denied() -> None:
    """[B-116/E-05 同策略] MCP 执行经 Egress 策略；禁用 server 执行被拒且落审计。"""
    await _cleanup_audits()
    server = _server("deny", [_tool("t")])
    adapter = McpRuntimeAdapter(audit_writer=_audit(), transport=ASGITransport(app=probe_app))

    with pytest.raises(AppError) as exc:
        await adapter.execute_tool(
            server=server, tool=server.tools[0], arguments={}, policy_decision="DENY"
        )
    assert exc.value.code == "FORBIDDEN"

    rows = await _egress_rows()
    assert rows, "MCP 拒绝未落 egress 审计"
    assert rows[0].policy_decision == "DENY"
    assert rows[0].target.startswith("mcp://mcpr-deny/")
    assert rows[0].result_status == "DENIED"
    await adapter.aclose()
    await _cleanup_audits()


async def test_b116_real_tools_call_over_http_with_audit() -> None:
    """[B-116] tools/call 真实 HTTP（initialize → tools/call），结果返回且落 OK 审计。"""
    await _cleanup_audits()
    server = _server("live", [_tool("probe_tool_1")])
    adapter = McpRuntimeAdapter(audit_writer=_audit(), transport=ASGITransport(app=probe_app))

    content = await adapter.execute_tool(
        server=server,
        tool=server.tools[0],
        arguments={"query": "hello"},
        policy_decision="ALLOW",
    )
    assert content == "probe-result:probe_tool_1:hello"

    rows = await _egress_rows()
    assert len(rows) == 1
    assert rows[0].policy_decision == "ALLOW"
    assert rows[0].status_code == 200
    assert rows[0].result_status == "OK"
    assert rows[0].target_type == "MCP"
    await adapter.aclose()
    await _cleanup_audits()


async def test_b116_tool_is_error_maps_to_error_audit() -> None:
    await _cleanup_audits()
    server = _server("fail", [_tool("probe_tool_1")])
    adapter = McpRuntimeAdapter(audit_writer=_audit(), transport=ASGITransport(app=probe_app))

    import os

    os.environ["MCP_PROBE_FAIL_CALL"] = "1"
    try:
        with pytest.raises(McpToolError):
            await adapter.execute_tool(
                server=server,
                tool=server.tools[0],
                arguments={"query": "x"},
                policy_decision="ALLOW",
            )
    finally:
        os.environ.pop("MCP_PROBE_FAIL_CALL", None)

    rows = await _egress_rows()
    assert rows and rows[0].result_status == "ERROR"
    await adapter.aclose()
    await _cleanup_audits()
