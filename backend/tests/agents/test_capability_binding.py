"""TASK-006 Capability Contract 复用验收测试（BE-S-05 / RULE-fluxion-workflow-001）。

断言 Agent（CapabilityBinding）与 Workflow Step（capability_ref 字符串）两端走
**同一个解析实现**，映射到同一 Registry kind，且收缩后无独立工具字段回潮。
"""

from __future__ import annotations

import pytest

from fluxion.agents.capabilities import (
    CAPABILITY_TYPE_KINDS,
    CapabilityRef,
    parse_capability_ref,
    resolve_binding_reference,
)
from fluxion.agents.definitions import CapabilityBinding, CapabilityType
from fluxion.registry import SQLiteRegistryStore
from fluxion.resources import (
    ResourceDefinition,
    ResourceKind,
    ResourceStatus,
    RuntimeProfile,
)


def test_parse_capability_ref_supports_workflow_prefix_syntax() -> None:
    assert parse_capability_ref("skill:search@3") == CapabilityRef(
        ResourceKind.SKILL, "search", "3"
    )
    assert parse_capability_ref("mcp:weather@1").resource_kind is ResourceKind.MCP
    # workflow 的 plugin: 前缀即 TOOL/Adapter 引用段（历史契约命名保留）。
    parsed = parse_capability_ref("plugin:calc@2")
    assert parsed.resource_id == "calc" and parsed.version == "2"


def test_parse_bare_ref_without_prefix_is_ambiguous_only_for_workflow() -> None:
    # workflow 语法里无前缀 = 非法；Agent 侧由 binding.type 显式补充语义。
    assert parse_capability_ref("search@1") is None


def test_binding_and_workflow_ref_resolve_to_same_contract_target() -> None:
    binding = CapabilityBinding(
        capability_ref="search", version_pin="4", type=CapabilityType.SKILL
    )
    resolved = resolve_binding_reference(binding)
    assert resolved == CapabilityRef(ResourceKind.SKILL, "search", "4")
    # 与 workflow 字符串互推等价：同一 target 两种表达。
    wf_form = f"{resolved.resource_kind.value}:{resolved.resource_id}@{resolved.version}"
    assert parse_capability_ref(wf_form) == resolved


def test_capability_type_to_registry_kind_mapping() -> None:
    assert CAPABILITY_TYPE_KINDS[CapabilityType.SKILL] is ResourceKind.SKILL
    assert CAPABILITY_TYPE_KINDS[CapabilityType.MCP] is ResourceKind.MCP
    # TOOL 能力经 plugin: 段承载 Adapter 注册物（executor 侧准入字符串不强制存在）。
    assert CAPABILITY_TYPE_KINDS[CapabilityType.TOOL] is ResourceKind.PLUGIN


def test_no_standalone_tools_field_regression() -> None:
    from pydantic import ValidationError

    profile_fields = set(RuntimeProfile.model_fields)
    assert profile_fields.isdisjoint({"allowed_tools", "allowed_skills", "prompt"})
    with pytest.raises(ValidationError):
        RuntimeProfile(allowed_tools=["x"], request_timeout_ms=30_000, max_retries=1)


async def test_be_s_05_agent_and_workflow_step_share_the_same_store_target() -> None:
    """BE-S-05：绑定与 Step 各自表达，最终落回同一 Registry 版本对象。"""
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    await store.initialize()
    try:
        resource = ResourceDefinition(
            tenant_id="tenant-a",
            kind=ResourceKind.PLUGIN,
            id="calc",
            version="2",
            status=ResourceStatus.DRAFT,
            spec_json={
                "plugin_type": "model_provider",
                "protocol": "openai_compatible",
                "base_url": "https://x.com/v1",
                "model": "m",
            },
        )
        await store.put(resource)
        await store.publish(ResourceKind.PLUGIN, "calc", tenant_id="tenant-a", version="2")

        binding_target = resolve_binding_reference(
            CapabilityBinding(
                capability_ref="calc", version_pin="2", type=CapabilityType.TOOL
            )
        )
        step_target = parse_capability_ref("plugin:calc@2")
        assert step_target == binding_target

        fetched = await store.get(
            step_target.resource_kind,
            step_target.resource_id,
            tenant_id="tenant-a",
            version=step_target.version,
        )
        assert fetched is not None and fetched.status is ResourceStatus.PUBLISHED
    finally:
        await store.close()


