"""Capability Contract 单一解析源（TASK-006 / 规则 12）。

Agent 的 AgentCapabilityReference（typed 三元组）与 Workflow Step 的 capability_ref
字符串（`skill|tool|mcp|plugin:<id>@<version>` 契约语法）是同一 Contract 的两种
表达：kind 映射、id/version 拆装只允许经本模块，禁止两端各自重写。

P1C-02 统一（closure TASK-002）：TOOL 归 `ResourceKind.TOOL`；`tool:` 前缀入
契约语法；`plugin:` 仅保留 Provider/Extension 语义，TOOL capability 遇
`plugin:` ref 一律 fail-closed（不静默转换）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from fluxion.agents.definitions import AgentCapabilityReference, CapabilityType
from fluxion.resources import ResourceKind

CAPABILITY_TYPE_KINDS: dict[CapabilityType, ResourceKind] = {
    CapabilityType.SKILL: ResourceKind.SKILL,
    CapabilityType.MCP: ResourceKind.MCP,
    CapabilityType.TOOL: ResourceKind.TOOL,
    CapabilityType.WORKFLOW: ResourceKind.WORKFLOW,
}

_CAPABILITY_REF = re.compile(r"^(skill|tool|mcp|plugin):([^@]+)@([^@]+)$")


@dataclass(frozen=True, slots=True)
class CapabilityRef:
    """Capability 引用的规范形态（Registry kind + id + 精确版本）。"""

    resource_kind: ResourceKind
    resource_id: str
    version: str


def parse_capability_ref(value: str) -> CapabilityRef | None:
    """解析 workflow 语法引用串；无前缀或缺版本返回 None（调用方负责报错）。"""
    match = _CAPABILITY_REF.fullmatch(value)
    if match is None:
        return None
    kind_value, resource_id, version = match.groups()
    return CapabilityRef(ResourceKind(kind_value), resource_id, version)


def resolve_binding_reference(binding: AgentCapabilityReference) -> CapabilityRef:
    """把 Agent 侧 typed binding 归一到同一 CapabilityRef 形态。

    typed binding 的 ``capability_ref`` 必须是裸资源 ID；带前缀语法一律
    fail-closed——尤其 TOOL capability 的 ``plugin:`` 前缀（P1C-02）：存在
    静默改写就会把 Tool 授权架到 PLUGIN 资源上，形成双 Tool 语义。
    """
    ref = binding.capability_ref
    if ":" in ref:
        if ref.startswith("plugin:") and binding.type is CapabilityType.TOOL:
            raise ValueError(
                f"capability_ref {ref!r} 为 plugin: 前缀：TOOL capability 必须引用 "
                "ResourceKind.TOOL 资源（P1C-02 fail-closed，不静默转换 legacy 值）"
            )
        raise ValueError(
            f"capability_ref 必须为裸资源 ID（收到带前缀形态 {ref!r}）；"
            "带前缀引用请走 parse_capability_ref"
        )
    return CapabilityRef(
        resource_kind=CAPABILITY_TYPE_KINDS[binding.type],
        resource_id=ref,
        version=binding.version_pin,
    )
