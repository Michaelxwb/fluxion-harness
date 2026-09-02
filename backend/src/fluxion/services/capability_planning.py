"""CapabilityPlanningService（remediation §6.4 / TASK-018 / TASK-018 返工）。

配置期计算 Agent 能力依赖闭包：每个 Skill 的 `required_capabilities` 必须被 Agent
已声明的 Tool 覆盖，缺失项在 UI 配置期明确提示，而不是运行时才失败。

接入发布链（S-04/E-05）：`console_resources.validate_publish` 与 publish 管道
调用本服务，缺失依赖 fail-closed。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fluxion.agents.definitions import AgentDefinition, CapabilityType
from fluxion.registry import RegistryStore
from fluxion.resources import ResourceKind, SkillDefinition

LATEST_PUBLISHED = "latest-published"


@dataclass(frozen=True, slots=True)
class CapabilityPlan:
    """依赖闭包规划结果：missing 为可操作缺失清单。"""

    missing: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.missing


class CapabilityPlanningService:
    """计算 Agent 能力依赖闭包（Skill required_capabilities → Tool 覆盖）。"""

    def __init__(self, store: RegistryStore) -> None:
        self._store = store

    async def plan_agent_capabilities(
        self,
        *,
        tenant_id: str,
        agent_spec: AgentDefinition,
    ) -> CapabilityPlan:
        # 覆盖集只含 tool 类型声明（与 runtime closure 校验同语义）：
        # skill/mcp 声明不满足 required capabilities——同名 Skill 不可顶替
        # required Tool（TASK-018 返工修复）。
        declared = {
            cap.capability_ref
            for cap in agent_spec.capabilities
            if cap.type is CapabilityType.TOOL
        }
        missing: list[str] = []
        for cap in agent_spec.capabilities:
            if cap.type is not CapabilityType.SKILL:
                continue
            skill = await self._store.get(
                ResourceKind.SKILL,
                cap.capability_ref,
                tenant_id=tenant_id,
                version=None
                if cap.version_pin == LATEST_PUBLISHED
                else cap.version_pin,
            )
            if skill is None:
                missing.append(f"Skill {cap.capability_ref} 不存在（@{cap.version_pin}）")
                continue
            skill_spec = SkillDefinition.model_validate(skill.spec_json)
            for required in skill_spec.required_capabilities:
                if required not in declared:
                    missing.append(f"{cap.capability_ref} 需要能力 {required}，但 Agent 未声明")
        return CapabilityPlan(missing=missing)
