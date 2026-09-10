"""TASK-003（S-01 侧）：MCP approved policies 快照冻结 + 单入口约束。

- snapshot.mcp_tool_policies：resolve 期冻结各 bound MCP 的
  {tool_name: schema_hash}（仅 enabled approved），进 canonical digest。
- 单入口：执行期授权只认 ContextResolver 冻结的 effective_permissions；
  空权限快照下任何调用 fail-closed（无第二套授权逻辑）。
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from fluxion.registry import RegistryStore
from fluxion.registry.schema import capability_mcp_tool_policies
from fluxion.resources import ResourceKind
from fluxion.runtime.builtin_tools import BuiltinToolConfig, register_builtin_tools
from fluxion.runtime.context import RequestContext, RuntimeContext
from fluxion.runtime.tools import ToolAuthorizationError, ToolRuntime
from fluxion.services.context_resolver import ContextResolver, ResolverSelector
from tests.runtime_helpers import (
    TEST_POSTGRES_DSN,
    minimal_tool_context,
    publish_resource,
    seed_model_definition,
    seed_tenant_policy,
)


async def _seed_mcp_agent(pg_store: RegistryStore) -> None:
    await publish_resource(
        pg_store,
        tenant_id="tenant-a",
        kind=ResourceKind.RUNTIME_PROFILE,
        resource_id="assistant",
        version="1",
        spec={"max_rounds": 8, "default": True},
    )
    await seed_model_definition(pg_store, tenant_id="tenant-a", provider_id="dev.echo")
    await publish_resource(
        pg_store,
        tenant_id="tenant-a",
        kind=ResourceKind.MCP,
        resource_id="weather",
        version="1",
        spec={
            "name": "weather",
            "transport": "streamable_http",
            "url": "https://mcp.example.com/mcp",
            "connect_timeout_ms": 30000,
            "allowed_tools": ["lookup"],
        },
    )
    await publish_resource(
        pg_store,
        tenant_id="tenant-a",
        kind=ResourceKind.AGENT_DEFINITION,
        resource_id="assistant",
        version="1",
        spec={
            "name": "assistant",
            "system_prompt": "p",
            "owner": "builder",
            "model_policy": {
                "primary_model_ref": {"id": "model.dev.echo", "version": "1"}
            },
            "capabilities": [
                {"capability_ref": "weather", "version_pin": "1", "type": "mcp"}
            ],
        },
    )
    await seed_tenant_policy(pg_store, tenant_id="tenant-a", allowed_tools=[])
    # TASK-005 前无 policy 写 API：直写新表（读路径 TASK-003 先行）。
    engine = create_async_engine(TEST_POSTGRES_DSN)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                capability_mcp_tool_policies.insert().values(
                    tenant_id="tenant-a",
                    mcp_id="weather",
                    mcp_version=1,
                    tool_name="lookup",
                    schema_hash="sh1",
                    operation="read",
                    side_effect="none",
                    risk_level="low",
                    idempotency_json={},
                    approval_policy_json={},
                    enabled=True,
                )
            )
            await conn.execute(
                capability_mcp_tool_policies.insert().values(
                    tenant_id="tenant-a",
                    mcp_id="weather",
                    mcp_version=1,
                    tool_name="disabled_tool",
                    schema_hash="shX",
                    operation="write",
                    side_effect="writes",
                    risk_level="high",
                    idempotency_json={},
                    approval_policy_json={},
                    enabled=False,
                )
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_S01_mcp_approved_policies_frozen_in_snapshot(
    pg_store: RegistryStore,
) -> None:
    """S-01：bound MCP 的 enabled approved policies 冻结进快照 + digest 稳定。"""
    await _seed_mcp_agent(pg_store)
    resolver = ContextResolver(pg_store)
    selector = ResolverSelector(
        tenant_id="tenant-a", agent_id="assistant", user_id="user-a"
    )
    first = await resolver.resolve(
        selector,
        session_id="s-a",
        request_id="req_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        trace_id="trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        execution_id="exec_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )
    assert first.snapshot.mcp_versions == {"weather": "1"}
    assert first.snapshot.mcp_tool_policies == {"weather": {"lookup": "sh1"}}
    second = await resolver.resolve(
        selector,
        session_id="s-a",
        request_id="req_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        trace_id="trace_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        execution_id="exec_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    )
    assert second.snapshot.snapshot_digest == first.snapshot.snapshot_digest


@pytest.mark.asyncio
async def test_S01_single_resolver_entry() -> None:
    """S-01 单入口：空权限快照下任何工具调用 fail-closed（RULE-CAP-05）。"""
    context = minimal_tool_context(
        {"user_tools": [], "agent_tools": [], "tenant_tools": []}
    )
    runtime = ToolRuntime()
    register_builtin_tools(runtime, BuiltinToolConfig())
    with pytest.raises(ToolAuthorizationError):
        await runtime.call(context, "time.now", {})
    assert isinstance(context.snapshot.effective_permissions, dict)


@pytest.mark.asyncio
async def test_S01_empty_permissions_snapshot_shape() -> None:
    """S-01 单入口：RuntimeContext 快照缺权限键时按空集执行（不抛第二套逻辑）。"""
    context = minimal_tool_context({})
    assert RuntimeContext(
        request=RequestContext(
            tenant_id="t", user_id="u", session_id="s",
        ),
        snapshot=context.snapshot,
    ).snapshot.effective_permissions == {}
