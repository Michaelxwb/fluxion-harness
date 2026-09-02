"""TASK-018（remediation §6.4）：CapabilityPlanningService 依赖闭包。

- B-S-06：Agent 引用 skill 声明 required_capabilities，且已声明对应 Tool → 闭包闭合。
- B-E-05：缺 Tool/MCP → 返回可操作缺失清单（配置期拦截，非运行时失败）。
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
from fluxion.resources import ExactResourceVersion, ResourceDefinition, ResourceKind, ResourceStatus
from fluxion.services.capability_planning import CapabilityPlanningService


async def _put_skill(
    store: SQLiteRegistryStore,
    resource_id: str,
    required: list[str],
    version: str = "1",
) -> None:
    await store.put(
        ResourceDefinition(
            kind=ResourceKind.SKILL,
            id=resource_id,
            tenant_id="tenant-a",
            version=version,
            status=ResourceStatus.DRAFT,
            spec_json={"name": resource_id, "instructions": "x", "required_capabilities": required},
        )
    )


def _agent(*capabilities: AgentCapabilityReference) -> AgentDefinition:
    return AgentDefinition(
        name="agent-a",
        system_prompt="hi",
        owner="admin",
        model_policy=AgentModelPolicy(primary_model_ref=ExactResourceVersion(id="model.dev", version="1")),
        capabilities=list(capabilities),
    )


def _cap(ref: str, cap_type: CapabilityType) -> AgentCapabilityReference:
    return AgentCapabilityReference(capability_ref=ref, version_pin="1", type=cap_type)


@pytest.mark.asyncio
async def test_B_S06_skill_closure_covered_plan_valid() -> None:
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    await store.initialize()
    try:
        await _put_skill(store, "refund-skill", ["refund_order"])
        agent = _agent(
            _cap("refund-skill", CapabilityType.SKILL),
            _cap("refund_order", CapabilityType.TOOL),
        )
        plan = await CapabilityPlanningService(store).plan_agent_capabilities(
            tenant_id="tenant-a", agent_spec=agent
        )
        assert plan.valid is True
        assert plan.missing == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_B_E05_skill_closure_missing_tool_returns_actionable() -> None:
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    await store.initialize()
    try:
        await _put_skill(store, "refund-skill", ["refund_order"])
        # Agent 只声明 skill，未声明其 required tool → 闭包缺口
        agent = _agent(_cap("refund-skill", CapabilityType.SKILL))
        plan = await CapabilityPlanningService(store).plan_agent_capabilities(
            tenant_id="tenant-a", agent_spec=agent
        )
        assert plan.valid is False
        assert any("refund-skill" in item and "refund_order" in item for item in plan.missing)
    finally:
        await store.close()
