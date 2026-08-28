"""TASK-002（phase1-closure）Tool ResourceKind 统一验收测试。

S-02（integration，backend-code-quality-performance / RULE-C-02）：
- `tool:<id>@<version>` 引用串解析为 `ResourceKind.TOOL`；
- TOOL 类型 typed binding 归一到 `ResourceKind.TOOL`（不再映射 PLUGIN）；
- TOOL capability 遇 `plugin:` 前缀 ref → fail-closed 明确错误（不静默转换）。

B-01（unit）：坏格式 ref / 未知 type / `plugin:` 当 tool → 全部明确报错。

真实边界：真实 parser（capabilities.py 纯函数）+ 真实 AgentCapabilityReference typed
model；不 mock。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from fluxion.agents.capabilities import (
    parse_capability_ref,
    resolve_binding_reference,
)
from fluxion.agents.definitions import AgentCapabilityReference, CapabilityType
from fluxion.resources import ResourceKind


def _binding(*, ref: str, kind: CapabilityType) -> AgentCapabilityReference:
    return AgentCapabilityReference(capability_ref=ref, version_pin="1.0.0", type=kind)


def test_s02_tool_prefix_parses_to_tool_kind() -> None:
    """`tool:customer-query@1.0.0` → ResourceKind.TOOL（RED：现无 tool: 前缀）。"""
    ref = parse_capability_ref("tool:customer-query@1.0.0")
    assert ref is not None
    assert ref.resource_kind is ResourceKind.TOOL
    assert ref.resource_id == "customer-query"
    assert ref.version == "1.0.0"


def test_s02_binding_tool_resolves_to_tool_kind() -> None:
    """TOOL 类型 binding 归一到 ResourceKind.TOOL（RED：现映射 PLUGIN）。"""
    ref = resolve_binding_reference(_binding(ref="customer-query", kind=CapabilityType.TOOL))
    assert ref.resource_kind is ResourceKind.TOOL


def test_s02_tool_type_with_plugin_ref_rejected_fail_closed() -> None:
    """TOOL capability + `plugin:` 前缀 ref → fail-closed 明确错误（不静默转换）。"""
    with pytest.raises(ValueError, match="plugin:"):
        resolve_binding_reference(_binding(ref="plugin:legacy-tool", kind=CapabilityType.TOOL))


def test_b01_malformatted_typed_ref_rejected() -> None:
    """坏格式 ref：typed binding 的 capability_ref 不允许携带前缀语法。"""
    with pytest.raises(ValueError, match="capability_ref"):
        resolve_binding_reference(_binding(ref="tool:foo@1", kind=CapabilityType.TOOL))
    with pytest.raises(ValueError, match="capability_ref"):
        resolve_binding_reference(_binding(ref="skill:bar@1", kind=CapabilityType.SKILL))


def test_b01_unknown_capability_type_rejected() -> None:
    """未知 type：CapabilityType 构造期即拒绝（typed enum，无静默降级）。"""
    with pytest.raises(ValueError):
        CapabilityType("unknown")


def test_b01_binding_model_rejects_empty_ref() -> None:
    """空 ref 在 typed model 层即被拒（min_length=1）。"""
    with pytest.raises(ValidationError):
        AgentCapabilityReference(capability_ref="", version_pin="1.0.0", type=CapabilityType.TOOL)


def test_b01_plugin_ref_still_valid_for_provider_semantics() -> None:
    """`plugin:` 前缀保留 Provider/Extension 语义：仍可解析为 PLUGIN（非 tool 路径）。"""
    ref = parse_capability_ref("plugin:model-provider@2")
    assert ref is not None
    assert ref.resource_kind is ResourceKind.PLUGIN
