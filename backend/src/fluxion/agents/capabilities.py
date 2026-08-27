"""Capability Contract 单一解析源（TASK-006 / 规则 12）。

Agent 的 CapabilityBinding（typed 三元组）与 Workflow Step 的 capability_ref
字符串（`skill|mcp|plugin:<id>@<version>` 历史契约语法）是同一 Contract 的两种
表达：kind 映射、id/version 拆装只允许经本模块，禁止两端各自重写。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from fluxion.agents.definitions import CapabilityBinding, CapabilityType
from fluxion.resources import ResourceKind

# TOOL 能力的 Registry 承载段沿用 workflow 历史 plugin: 命名——Tool Adapter 的
# 可注册实体存 PLUGIN 资源；准入字符串本身不受此约束（executor 侧语义）。
CAPABILITY_TYPE_KINDS: dict[CapabilityType, ResourceKind] = {
    CapabilityType.SKILL: ResourceKind.SKILL,
    CapabilityType.MCP: ResourceKind.MCP,
    CapabilityType.TOOL: ResourceKind.PLUGIN,
}

_CAPABILITY_REF = re.compile(r"^(skill|mcp|plugin):([^@]+)@([^@]+)$")


@dataclass(frozen=True, slots=True)
class CapabilityRef:
    """Capability 引用的规范形态（Registry kind + id + 精确版本）。"""

    resource_kind: ResourceKind
    resource_id: str
    version: str


def parse_capability_ref(value: str) -> CapabilityRef | None:
    """解析 workflow 语法引用串；无前缀或缺版本返回 None。"""
    match = _CAPABILITY_REF.fullmatch(value)
    if match is None:
        return None
    kind_value, resource_id, version = match.groups()
    return CapabilityRef(ResourceKind(kind_value), resource_id, version)


def resolve_binding_reference(binding: CapabilityBinding) -> CapabilityRef:
    """把 Agent 侧 typed binding 归一到同一 CapabilityRef 形态。"""
    return CapabilityRef(
        resource_kind=CAPABILITY_TYPE_KINDS[binding.type],
        resource_id=binding.capability_ref,
        version=binding.version_pin,
    )
