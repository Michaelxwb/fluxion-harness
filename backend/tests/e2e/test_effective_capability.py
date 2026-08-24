from __future__ import annotations

import pytest
from tests.runtime_helpers import publish_resource

from fluxion.registry import RegistryStore
from fluxion.resources import ResourceBinding, ResourceKind, SubjectType
from fluxion.runtime.capabilities import EffectiveCapabilityResolver


@pytest.mark.asyncio
async def test_S_R04_effective_capability_intersects_user_binding_policy_and_agent_allowlist(
    sqlite_store: RegistryStore,
) -> None:
    await publish_resource(
        sqlite_store,
        tenant_id="tenant-a",
        kind=ResourceKind.RUNTIME_PROFILE,
        resource_id="assistant",
        version="1",
        spec={
            "prompt": "strict",
            "model_policy": {"provider": "stub"},
            "allowed_mcps": ["weather"],
            "allowed_tools": ["mcp.weather.current", "mcp.weather.delete"],
        },
    )
    await publish_resource(
        sqlite_store,
        tenant_id="tenant-a",
        kind=ResourceKind.MCP,
        resource_id="weather",
        version="1",
        spec={
            "name": "weather",
            "server_uri": "stdio://weather",
            "tools": [
                {
                    "tool_id": "mcp.weather.current",
                    "name": "weather.current",
                    "capability_id": "cap.weather.current",
                },
                {
                    "tool_id": "mcp.weather.delete",
                    "name": "weather.delete",
                    "capability_id": "cap.weather.delete",
                },
                {
                    "tool_id": "mcp.weather.audit",
                    "name": "weather.audit",
                    "capability_id": "cap.weather.audit",
                },
            ],
        },
    )
    await publish_resource(
        sqlite_store,
        tenant_id="tenant-a",
        kind=ResourceKind.POLICY,
        resource_id="tenant-policy",
        version="1",
        spec={"allowed_tools": ["mcp.weather.current", "mcp.weather.audit"]},
    )
    await sqlite_store.put_binding(
        ResourceBinding(
            binding_id="user-weather",
            tenant_id="tenant-a",
            subject_type=SubjectType.USER,
            subject_id="user-a",
            resource_type=ResourceKind.MCP,
            resource_id="weather",
            credential_ref="secret://tenant-a/weather",
        )
    )
    await sqlite_store.put_binding(
        ResourceBinding(
            binding_id="tenant-policy-binding",
            tenant_id="tenant-a",
            subject_type=SubjectType.TENANT,
            subject_id="tenant-a",
            resource_type=ResourceKind.POLICY,
            resource_id="tenant-policy",
        )
    )

    resolver = EffectiveCapabilityResolver(sqlite_store)
    tools = await resolver.visible_tools(
        tenant_id="tenant-a",
        user_id="user-a",
        runtime_profile_id="assistant",
    )

    assert [tool.tool_id for tool in tools] == ["mcp.weather.current"]
    assert tools[0].credential_ref == "secret://tenant-a/weather"
    assert tools[0].capability_id == "cap.weather.current"


@pytest.mark.asyncio
async def test_S_R04_deny_only_policy_does_not_blanket_deny(
    sqlite_store: RegistryStore,
) -> None:
    await publish_resource(
        sqlite_store,
        tenant_id="tenant-a",
        kind=ResourceKind.RUNTIME_PROFILE,
        resource_id="assistant",
        version="1",
        spec={
            "prompt": "strict",
            "model_policy": {"provider": "stub"},
            "allowed_mcps": ["weather"],
            "allowed_tools": ["mcp.weather.current", "mcp.weather.delete"],
        },
    )
    await publish_resource(
        sqlite_store,
        tenant_id="tenant-a",
        kind=ResourceKind.MCP,
        resource_id="weather",
        version="1",
        spec={
            "name": "weather",
            "server_uri": "stdio://weather",
            "tools": [
                {
                    "tool_id": "mcp.weather.current",
                    "name": "weather.current",
                    "capability_id": "cap.weather.current",
                },
                {
                    "tool_id": "mcp.weather.delete",
                    "name": "weather.delete",
                    "capability_id": "cap.weather.delete",
                },
            ],
        },
    )
    await publish_resource(
        sqlite_store,
        tenant_id="tenant-a",
        kind=ResourceKind.POLICY,
        resource_id="deny-only",
        version="1",
        spec={"denied_tools": ["mcp.weather.delete"]},
    )
    await sqlite_store.put_binding(
        ResourceBinding(
            binding_id="user-weather",
            tenant_id="tenant-a",
            subject_type=SubjectType.USER,
            subject_id="user-a",
            resource_type=ResourceKind.MCP,
            resource_id="weather",
        )
    )
    await sqlite_store.put_binding(
        ResourceBinding(
            binding_id="tenant-policy-binding",
            tenant_id="tenant-a",
            subject_type=SubjectType.TENANT,
            subject_id="tenant-a",
            resource_type=ResourceKind.POLICY,
            resource_id="deny-only",
        )
    )

    resolver = EffectiveCapabilityResolver(sqlite_store)
    tools = await resolver.visible_tools(
        tenant_id="tenant-a",
        user_id="user-a",
        runtime_profile_id="assistant",
    )

    # deny-only policy 只拒绝 denied，不把其它工具一并拒掉
    assert {tool.tool_id for tool in tools} == {"mcp.weather.current"}


@pytest.mark.asyncio
async def test_S_R04_mcp_allowed_tools_subfilter_and_user_granted_tools(
    sqlite_store: RegistryStore,
) -> None:
    await publish_resource(
        sqlite_store,
        tenant_id="tenant-a",
        kind=ResourceKind.RUNTIME_PROFILE,
        resource_id="assistant",
        version="1",
        spec={
            "prompt": "strict",
            "model_policy": {"provider": "stub"},
            "allowed_mcps": ["weather"],
            "allowed_tools": ["mcp.weather.current"],
        },
    )
    await publish_resource(
        sqlite_store,
        tenant_id="tenant-a",
        kind=ResourceKind.MCP,
        resource_id="weather",
        version="1",
        spec={
            "name": "weather",
            "server_uri": "stdio://weather",
            "allowed_tools": ["mcp.weather.current", "mcp.weather.audit"],
            "tools": [
                {
                    "tool_id": "mcp.weather.current",
                    "name": "weather.current",
                    "capability_id": "cap.weather.current",
                },
                {
                    "tool_id": "mcp.weather.delete",
                    "name": "weather.delete",
                    "capability_id": "cap.weather.delete",
                },
                {
                    "tool_id": "mcp.weather.audit",
                    "name": "weather.audit",
                    "capability_id": "cap.weather.audit",
                },
            ],
        },
    )
    await sqlite_store.put_binding(
        ResourceBinding(
            binding_id="user-weather",
            tenant_id="tenant-a",
            subject_type=SubjectType.USER,
            subject_id="user-a",
            resource_type=ResourceKind.MCP,
            resource_id="weather",
        )
    )

    resolver = EffectiveCapabilityResolver(sqlite_store)
    granted = await resolver.user_granted_tools(
        tenant_id="tenant-a",
        user_id="user-a",
        runtime_profile_id="assistant",
    )
    tools = await resolver.visible_tools(
        tenant_id="tenant-a",
        user_id="user-a",
        runtime_profile_id="assistant",
    )

    # MCP 自身 allowed_tools 子过滤：delete 不在 MCP 暴露范围内
    assert granted == {"mcp.weather.current", "mcp.weather.audit"}
    # visible_tools 还叠加 profile.allowed_tools 这个 agent 层：audit 被 agent 层过滤
    assert [tool.tool_id for tool in tools] == ["mcp.weather.current"]
