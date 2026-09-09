"""CapabilityQueryService：只读有效 Skill 查询（设计 §13）。

只展示"这个用户现在真的可以使用"的 Skill：

```text
Effective Skills =
  Agent declared skills
  ∩ User authorized skills（binding/visibility）
  ∩ Published resources
  ∩ Tenant policy allowed（required 闭包在策略下成立）
```

实现复用 `ContextResolutionSupport._resolve_capability_versions`（与主解析
管线同一 Skill 版本解析算法，不复制），再按 skill 逐个做 required 闭包
判定（判定所用的 tool 三维语义与 `ContextResolver` 第 9 段一致）。
轻查询：不触发 memory recall、credential 解析与 Snapshot 构建（D4）。
"""

from __future__ import annotations

from dataclasses import dataclass

from fluxion.agents.definitions import AgentDefinition
from fluxion.registry import ChannelRegistryStore
from fluxion.registry.store import ScopedRegistryReader
from fluxion.resources import ResourceKind
from fluxion.runtime.capabilities import EffectiveCapabilityResolver
from fluxion.services.context_resolution_support import (
    ContextResolutionSupport,
    _SkillSpecView,
)


@dataclass(frozen=True, slots=True)
class EffectiveSkillSummary:
    skill_id: str
    version: str
    name: str
    description: str


class CapabilityQueryService(ContextResolutionSupport):
    """有效能力只读查询（无状态，实例可跨请求复用）。"""

    def __init__(self, store: ChannelRegistryStore) -> None:
        self._store = store

    async def list_effective_skills(
        self,
        tenant_id: str,
        platform_user_id: str,
        agent_id: str,
    ) -> list[EffectiveSkillSummary]:
        """列出该用户当前真正可用的 Skill（按 skill_id 排序）。"""
        async with self._store.begin_scoped_read(tenant_id=tenant_id) as scope:
            agent = await scope.get(
                ResourceKind.AGENT_DEFINITION, agent_id, tenant_id=tenant_id
            )
            if agent is None:
                return []
            agent_spec = AgentDefinition.model_validate(agent.spec_json)
            # §28.4：Draft/缺失的 skill 直接排除（不展示，不爆炸）。
            # store.get 无版本只解析 published；精确 pin 缺失同样返回 None。
            live_capabilities = []
            for cap in agent_spec.capabilities:
                if cap.type.value != "skill":
                    live_capabilities.append(cap)
                    continue
                row = await scope.get(
                    ResourceKind.SKILL,
                    cap.capability_ref,
                    tenant_id=tenant_id,
                    version=None if cap.version_pin == "latest-published" else cap.version_pin,
                )
                if row is not None:
                    live_capabilities.append(cap)
            skill_versions, _, _, _ = await self._resolve_capability_versions(
                scope, tenant_id, live_capabilities, platform_user_id
            )
            if not skill_versions:
                return []
            # §13.2 交集语义：仅 Agent 声明（且上一步存活）的 skill 可列出。注意
            # 主解析管线会 merge 用户 binding 扩展（既有行为，保持不动）；listing 从严取交集。
            agent_skill_pins = {
                cap.capability_ref for cap in live_capabilities if cap.type.value == "skill"
            }
            allowed_tools = await self._effective_tool_closure(
                scope, tenant_id, agent_spec, platform_user_id
            )
            summaries: list[EffectiveSkillSummary] = []
            for ref in sorted(skill_versions):
                if ref not in agent_skill_pins:
                    continue
                row = await scope.get(ResourceKind.SKILL, ref, tenant_id=tenant_id)
                if row is None:
                    continue
                parsed = _SkillSpecView.model_validate(row.spec_json)
                if not self._required_closure_satisfied(
                    parsed.required_capabilities, allowed_tools
                ):
                    continue
                summaries.append(
                    EffectiveSkillSummary(
                        skill_id=ref,
                        version=skill_versions[ref],
                        name=parsed.name,
                        description=_short_description(parsed.instructions),
                    )
                )
            return summaries

    async def _effective_tool_closure(
        self,
        scope: ScopedRegistryReader,
        tenant_id: str,
        agent_spec: AgentDefinition,
        platform_user_id: str,
    ) -> set[str]:
        """有效工具闭包（与 ContextResolver 第 9 段同语义）。

        required ⊆ 返回集合才算闭合：
        - 未配置 tenant policy → 空集（fail-closed，仅零 required 可用）；
        - allow-list → 三维交集 ∩ allowed；
        - deny-only → 三维交集（denied 已移除）。
        """
        agent_tools = {
            ref.capability_ref for ref in agent_spec.capabilities if ref.type.value == "tool"
        }
        grants = await scope.list_capability_grants(
            tenant_id=tenant_id,
            platform_user_id=platform_user_id,
        )
        user_tools = {g.capability_ref for g in grants if g.capability_kind == "tool"}
        policy_allowed, policy_denied, policy_configured = (
            await EffectiveCapabilityResolver(scope).tenant_policy_tools(
                tenant_id=tenant_id
            )
        )
        base = (agent_tools & user_tools) - policy_denied
        if not policy_configured:
            return set()
        if policy_allowed:
            return base & set(policy_allowed)
        return base

    @staticmethod
    def _required_closure_satisfied(required: list[str], allowed_tools: set[str]) -> bool:
        needed = {item for item in required if item.strip()}
        return needed <= allowed_tools


def _short_description(instructions: str, limit: int = 120) -> str:
    """展示用简介：instructions 首个非空行（SkillDefinition 无独立 description 字段）。"""
    for line in instructions.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:limit]
    return ""
