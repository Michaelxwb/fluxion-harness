from __future__ import annotations

from typing import cast

from fluxion.registry import RegistryStore
from fluxion.resources import (
    ResourceBinding,
    ResourceDefinition,
    ResourceKind,
    ResourceStatus,
)
from fluxion.runtime.tools import ToolDescriptor


class EffectiveCapabilityResolver:
    def __init__(self, store: RegistryStore) -> None:
        self._store = store

    async def visible_tools(
        self,
        *,
        tenant_id: str,
        user_id: str,
        runtime_profile_id: str,
    ) -> list[ToolDescriptor]:
        profile = await self._required_resource(
            ResourceKind.RUNTIME_PROFILE,
            runtime_profile_id,
            tenant_id=tenant_id,
        )
        agent_tools = _string_set(profile.spec_json.get("allowed_tools"))
        allowed_mcps = _string_set(profile.spec_json.get("allowed_mcps"))
        policy = await self._tenant_policy_tools(tenant_id)
        descriptors: list[ToolDescriptor] = []
        for binding in await self._mcp_bindings(tenant_id, user_id):
            if allowed_mcps and binding.resource_id not in allowed_mcps:
                continue
            mcp = await self._required_resource(
                ResourceKind.MCP,
                binding.resource_id,
                tenant_id=tenant_id,
                selector=binding.resource_version_selector,
            )
            descriptors.extend(_mcp_tool_descriptors(mcp, binding.credential_ref))
        return [
            descriptor
            for descriptor in descriptors
            if _allowed(descriptor.tool_id, agent_tools, policy)
        ]

    async def user_granted_tools(
        self,
        *,
        tenant_id: str,
        user_id: str,
        runtime_profile_id: str,
    ) -> set[str]:
        """用户 Binding 授予、且 profile allowed_mcps 允许的 MCP 工具 id 集合。"""
        profile = await self._required_resource(
            ResourceKind.RUNTIME_PROFILE,
            runtime_profile_id,
            tenant_id=tenant_id,
        )
        allowed_mcps = _string_set(profile.spec_json.get("allowed_mcps"))
        granted: set[str] = set()
        for binding in await self._mcp_bindings(tenant_id, user_id):
            if allowed_mcps and binding.resource_id not in allowed_mcps:
                continue
            mcp = await self._required_resource(
                ResourceKind.MCP,
                binding.resource_id,
                tenant_id=tenant_id,
                selector=binding.resource_version_selector,
            )
            granted.update(
                tool.tool_id for tool in _mcp_tool_descriptors(mcp, binding.credential_ref)
            )
        return granted

    async def tenant_policy_tools(
        self, *, tenant_id: str
    ) -> tuple[set[str], set[str], bool]:
        """返回 (allowed, denied, configured)。

        - configured=False：未配置 tenant policy，不施加约束。
        - configured=True 且 allowed 非空：allow-list 模式，仅放行 allowed。
        - configured=True 且 allowed 为空：deny-only 模式，放行除 denied 外的全部。
        denied 始终优先，调用方必须从所有维度移除。
        """
        policy = await self._tenant_policy_tools(tenant_id)
        return policy.allowed, policy.denied, policy.configured

    async def _mcp_bindings(self, tenant_id: str, user_id: str) -> list[ResourceBinding]:
        return await self._store.list_bindings(
            tenant_id=tenant_id,
            subject_type="user",
            subject_id=user_id,
            resource_type=ResourceKind.MCP,
        )

    async def _tenant_policy_tools(self, tenant_id: str) -> _PolicyTools:
        bindings = await self._store.list_bindings(
            tenant_id=tenant_id,
            subject_type="tenant",
            subject_id=tenant_id,
            resource_type=ResourceKind.POLICY,
        )
        allowed: set[str] = set()
        denied: set[str] = set()
        for binding in bindings:
            policy = await self._required_resource(
                ResourceKind.POLICY,
                binding.resource_id,
                tenant_id=tenant_id,
                selector=binding.resource_version_selector,
            )
            allowed.update(_string_set(policy.spec_json.get("allowed_tools")))
            denied.update(_string_set(policy.spec_json.get("denied_tools")))
        return _PolicyTools(allowed=allowed, denied=denied, configured=bool(bindings))

    async def _required_resource(
        self,
        kind: ResourceKind,
        resource_id: str,
        *,
        tenant_id: str,
        selector: str = "latest-published",
    ) -> ResourceDefinition:
        version = None if selector == "latest-published" else selector
        resource = await self._store.get(kind, resource_id, tenant_id=tenant_id, version=version)
        if resource is None:
            raise LookupError(f"{tenant_id}/{kind.value}/{resource_id}@{selector} not found")
        # binding pin 显式版本时必须校验已发布：否则 DRAFT 状态的
        # policy/MCP 定义可参与生产授权计算（与 ResourceResolver 的
        # PUBLISHED 校验对齐）。
        if resource.status is not ResourceStatus.PUBLISHED:
            raise LookupError(
                f"{tenant_id}/{kind.value}/{resource_id}@{selector} is not published"
            )
        return resource


class _PolicyTools:
    def __init__(self, *, allowed: set[str], denied: set[str], configured: bool) -> None:
        self.allowed = allowed
        self.denied = denied
        self.configured = configured


def _allowed(tool_id: str, agent_tools: set[str], policy: _PolicyTools) -> bool:
    if tool_id in policy.denied:
        return False
    # ADR-003 failure mode：交集为空→静默无能力。空 allowlist = 全拒（fail-closed），
    # 与 Runtime tools.py enforcement 对齐——此前展示侧用 `agent_tools and ...` 守卫，
    # 空 allowlist 时跳过成员校验（fail-open），导致 Console visible_tools 与 Runtime
    # 实际放行不一致。Skill 维度的扩展（binding 授予 profile 外的 skill）由 resolver
    # 的 _effective_skill_selectors 独立处理，不经过本函数，故此处收敛不影响 S_R18。
    if tool_id not in agent_tools:
        return False
    if not policy.configured:
        return True
    # allow-list 策略：allowed 非空则只放行 allowed；deny-only 策略（allowed 空）仅拒绝 denied。
    if policy.allowed:
        return tool_id in policy.allowed
    return True


def _mcp_tool_descriptors(
    resource: ResourceDefinition,
    credential_ref: str | None,
) -> list[ToolDescriptor]:
    raw_tools = resource.spec_json.get("tools", [])
    if not isinstance(raw_tools, list):
        return []
    mcp_allowed = _string_set(resource.spec_json.get("allowed_tools"))
    descriptors: list[ToolDescriptor] = []
    for raw_tool in raw_tools:
        if not isinstance(raw_tool, dict):
            continue
        tool = cast(dict[str, object], raw_tool)
        tool_id = tool.get("tool_id")
        name = tool.get("name", tool_id)
        capability_id = tool.get("capability_id", tool_id)
        if not isinstance(tool_id, str) or not isinstance(name, str):
            continue
        if mcp_allowed and tool_id not in mcp_allowed:
            continue
        if not isinstance(capability_id, str):
            capability_id = tool_id
        descriptors.append(
            ToolDescriptor(
                tool_id=tool_id,
                capability_id=capability_id,
                name=name,
                credential_ref=credential_ref,
            )
        )
    return descriptors


def _string_set(value: object) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {item for item in value if isinstance(item, str) and item.strip()}
