"""TASK-006：Skill required_capabilities + 发布期 closure 校验。

S-04（integration）：skill 声明的能力已被 Agent 覆盖 → 正常解析，且不隐式扩张
agent 工具权限（skill 不再把 required_capabilities 并入 agent 工具白名单）。
E-02（integration）：skill 声明的能力越出 Agent 能力 → 解析 fail-closed。

真实边界：真实 ContextResolver 十段管线 + Registry + AgentDefinition/Skill 定义。
"""

from __future__ import annotations

import pytest

from fluxion.agents.definitions import (
    AgentCapabilityReference,
    AgentDefinition,
    AgentModelPolicy,
    CapabilityType,
)
from fluxion.registry import SQLiteRegistryStore
from fluxion.resources import ExactResourceVersion, ResourceKind
from fluxion.services.context_resolver import (
    ContextResolutionError,
    ContextResolver,
    ResolverSelector,
)
from tests.runtime_helpers import publish_resource, resource_definition, seed_model_definition


async def _seed_agent_with_skill(
    store: SQLiteRegistryStore,
    *,
    skill_id: str,
    required_capabilities: list[str],
    agent_tools: list[str],
) -> None:
    await publish_resource(
        store,
        tenant_id="tenant-a",
        kind=ResourceKind.RUNTIME_PROFILE,
        resource_id="assistant",
        version="1",
        spec={"request_timeout_ms": 30_000, "max_retries": 1},
    )
    await publish_resource(
        store,
        tenant_id="tenant-a",
        kind=ResourceKind.SKILL,
        resource_id=skill_id,
        version="1",
        spec={"name": skill_id, "required_capabilities": required_capabilities},
    )
    capabilities = [
        AgentCapabilityReference(capability_ref=t, version_pin="1", type=CapabilityType.TOOL)
        for t in agent_tools
    ] + [
        AgentCapabilityReference(capability_ref=skill_id, version_pin="1", type=CapabilityType.SKILL)
    ]
    # ADR-A008：agent.model_policy 指向 ModelDefinition（model.dev.echo）
    await seed_model_definition(store, tenant_id="tenant-a", provider_id="dev.echo")
    await store.put(
        resource_definition(
            tenant_id="tenant-a",
            kind=ResourceKind.AGENT_DEFINITION,
            resource_id="assistant",
            version="1",
            spec=AgentDefinition(
                name="assistant",
                system_prompt="p",
                owner="builder",
                model_policy=AgentModelPolicy(primary_model_ref=ExactResourceVersion(id="model.dev.echo", version="1")),
                capabilities=capabilities,
            ).model_dump(mode="json"),
        )
    )
    await store.publish(ResourceKind.AGENT_DEFINITION, "assistant", tenant_id="tenant-a", version="1")


@pytest.mark.asyncio
async def test_S04_skill_required_capabilities_covered_resolves_without_expansion() -> None:
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    await store.initialize()
    try:
        await _seed_agent_with_skill(
            store, skill_id="math", required_capabilities=["calc"], agent_tools=["calc"]
        )
        snapshot = (
            await ContextResolver(store).resolve(
                ResolverSelector(tenant_id="tenant-a", agent_id="assistant", user_id="user-a"),
                session_id="s",
            )
        ).snapshot

        # closure 通过：skill 只声明 calc（已被 agent 覆盖），agent_tools 仍只有 agent
        # 声明的 calc——skill 不再隐式扩张。
        assert set(snapshot.effective_permissions["agent_tools"]) == {"calc"}
        assert snapshot.skill_required_capabilities == ["calc"]
        # TASK-007：typed EffectiveCapability 图统一表达 skill/tool 依赖与授权。
        assert snapshot.effective_capability.skills == {"math": "1"}
        assert snapshot.effective_capability.tools == ["calc"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_E02_skill_required_capabilities_beyond_agent_fails_closed() -> None:
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    await store.initialize()
    try:
        await _seed_agent_with_skill(
            store, skill_id="math", required_capabilities=["weather"], agent_tools=["calc"]
        )
        resolver = ContextResolver(store)
        with pytest.raises(ContextResolutionError) as exc:
            await resolver.resolve(
                ResolverSelector(tenant_id="tenant-a", agent_id="assistant", user_id="user-a"),
                session_id="s",
            )
        assert exc.value.code == "skill_closure_violation"
        assert "weather" in exc.value.message
    finally:
        await store.close()
