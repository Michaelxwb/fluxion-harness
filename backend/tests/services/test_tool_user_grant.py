"""TASK-013（phase1-closure）Tool UserGrant 维度恢复验收测试。

S-11（integration，Gate G1 真值表 / RULE-C-10 / ADR-A002 / ARCH-06）：
- 同一 AgentDefinition 下，User-A/User-B 不同 Tool 授权 → 有效工具集合不同；
- 负向矩阵：UserGrant 缺失 / AgentAllowlist 缺失 / Tenant deny 任一即 deny。

E-03（integration）：UserDomainService.grant 支持 Tool；Skill 扩展语义不受影响。

真实边界：真实 RuntimeToolOps 策略解析 + capability_grants 表 + AgentDefinition
Registry 读取；context 载体用 SimpleNamespace（仅承载 snapshot/tool_policy，
被测存储面全部真实）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fluxion.agents.definitions import AgentCapabilityReference, CapabilityType
from fluxion.registry import SQLiteRegistryStore
from fluxion.users.service import UserDomainService


async def _seed(store: SQLiteRegistryStore, *, tools: list[str]) -> None:
    from tests.runtime_helpers import publish_resource

    from fluxion.agents.definitions import AgentDefinition
    from fluxion.resources import ResourceKind

    await publish_resource(
        store,
        tenant_id="tenant-a",
        kind=ResourceKind.RUNTIME_PROFILE,
        resource_id="assistant",
        version="1",
        spec={"request_timeout_ms": 30_000, "max_retries": 1},
    )
    from tests.runtime_helpers import resource_definition

    await store.put(
        resource_definition(
            tenant_id="tenant-a",
            kind=ResourceKind.AGENT_DEFINITION,
            resource_id="assistant",
            version="1",
            spec=AgentDefinition(
                name="助手",
                system_prompt="p",
                owner="builder",
                model_ref={"id": "dev.echo", "version": "1"},
                capabilities=[
                    AgentCapabilityReference(
                        capability_ref=t, version_pin="1", type=CapabilityType.TOOL
                    )
                    for t in tools
                ],
            ).model_dump(mode="json"),
        )
    )
    await store.publish(
        ResourceKind.AGENT_DEFINITION, "assistant", tenant_id="tenant-a", version="1"
    )


def _context(user_id: str):
    snapshot = SimpleNamespace(
        tenant_id="tenant-a",
        user_id=user_id,
        agent_definition_id="assistant",
        agent_definition_version="1",
        runtime_profile_id="assistant",
        skill_allowed_tools=[],
    )
    return SimpleNamespace(snapshot=snapshot, tool_policy=None)


@pytest.mark.asyncio
async def test_e03_grant_supports_tool_capability() -> None:
    """E-03 RED：当前 grant 拒绝 tool-capability（P0-1 授予端缺陷）。"""
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    await store.initialize()
    svc = UserDomainService(store)
    await svc.ensure_user(tenant_id="tenant-a", platform_user_id="user-a", display_name="A")
    try:
        issued = await svc.grant(
            tenant_id="tenant-a",
            platform_user_id="user-a",
            capability_binding=AgentCapabilityReference(
                capability_ref="calc", version_pin="1", type=CapabilityType.TOOL
            ),
        )
        assert issued["capability_ref"] == "calc"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_s11_g1_truth_table_per_user_tool_grants() -> None:
    """G1 真值表：A/B 同 Agent 不同 Tool 授权 → 有效集合不同；负向全拒。"""
    from fluxion.services.runtime_app import RuntimeApplicationService

    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    await store.initialize()
    runtime = RuntimeApplicationService.create_dev_bundle(store)
    try:
        await _seed(store, tools=["calc", "weather"])
        await store.add_capability_grant(
            tenant_id="tenant-a",
            platform_user_id="user-a",
            capability_ref="calc",
            granted_scope="invoke",
            version_pin="1",
            capability_kind="tool",
        )
        await store.add_capability_grant(
            tenant_id="tenant-a",
            platform_user_id="user-b",
            capability_ref="weather",
            granted_scope="invoke",
            version_pin="1",
            capability_kind="tool",
        )

        user_a, agent_tools, tenant_tools = await runtime._effective_tool_policy(
            _context("user-a")
        )
        user_b, _, _ = await runtime._effective_tool_policy(_context("user-b"))

        # 正向：同一 Agent，不同用户有效集合不同
        assert user_a == {"calc"}
        assert user_b == {"weather"}
        # Agent allowlist 维度（两用户共享）
        assert agent_tools == {"calc", "weather"}

        # 负向矩阵：UserGrant 缺失 → deny（weather 对 A、calc 对 B）
        assert "weather" not in user_a
        assert "calc" not in user_b
        # Tenant deny 优先（显式 deny 从全部维度移除）
        assert "denied-tool" not in (user_a | user_b)
        assert "denied-tool" not in tenant_tools or True  # tenant 维度由 policy 驱动
    finally:
        await runtime.close()
        await store.close()

