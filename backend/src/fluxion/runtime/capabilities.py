from __future__ import annotations

from fluxion.registry import RegistryStore
from fluxion.resources import (
    PolicyDefinition,
    ResourceDefinition,
    ResourceKind,
    ResourceStatus,
)


class EffectiveCapabilityResolver:
    """租户级策略解析（ADR-012 后仅保留 tenant policy 维度）。

    原静态 MCP ``tools`` 读取路径（visible_tools / user_granted_tools /
    _mcp_tool_descriptors）已移除：

    - 该字段不在 ``MCPDefinition`` 中，严格校验根本建不出这种 spec；
    - 其工具 id 格式与真实 MCP 运行时（``mcp__<server>__<tool>``）不匹配，
      从未授权过真实工具；
    - 集合上被三重交集吸收（user = agent ∪ granted，user ∩ agent = agent），
      对有效放行结果无影响。

    用户级 MCP 授权语义保留在挂载层（mcp.py binding 检查）与 Skill 扩展
    （resolver._effective_skill_selectors）；agent/tenant 维度见
    runtime_tool_ops._effective_tool_policy。
    """

    def __init__(self, store: RegistryStore) -> None:
        self._store = store

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
            resource = await self._required_resource(
                ResourceKind.POLICY,
                binding.resource_id,
                tenant_id=tenant_id,
                selector=binding.resource_version_selector,
            )
            # ADR-012：从 PolicyDefinition 实例取字段（单一真相源）。
            definition = PolicyDefinition.model_validate(resource.spec_json)
            allowed.update(definition.allowed_tools)
            denied.update(definition.denied_tools)
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
