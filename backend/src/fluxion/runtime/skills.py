from __future__ import annotations

from dataclasses import dataclass

from fluxion.resources import ResourceDefinition, SkillDefinition
from fluxion.runtime.tools import ToolDescriptor


@dataclass(frozen=True, slots=True)
class DeclarativeSkill:
    skill_id: str
    name: str
    description: str
    capability_id: str
    parameters: dict[str, object]

    @property
    def descriptor(self) -> ToolDescriptor:
        return ToolDescriptor(
            tool_id=f"skill.{self.skill_id}",
            capability_id=self.capability_id,
            name=self.name,
            parameters_schema=self.parameters,
            external_dependency=False,
        )


@dataclass(frozen=True, slots=True)
class SkillInvocation:
    skill_id: str
    capability_id: str
    arguments: dict[str, object]


class DeclarativeSkillRuntime:
    def __init__(self) -> None:
        self._skills: dict[str, DeclarativeSkill] = {}

    def register(self, skill: DeclarativeSkill) -> None:
        self._skills[skill.skill_id] = skill

    def register_definition(self, definition: SkillDefinition) -> DeclarativeSkill:
        skill = DeclarativeSkill(
            skill_id=definition.name,
            name=definition.name,
            description=definition.description,
            capability_id=definition.capability_id or f"skill.{definition.name}",
            parameters=definition.parameters,
        )
        self.register(skill)
        return skill

    def register_resource(self, resource: ResourceDefinition) -> DeclarativeSkill:
        definition = SkillDefinition.model_validate(resource.spec_json)
        skill = DeclarativeSkill(
            skill_id=resource.id,
            name=definition.name,
            description=definition.description,
            capability_id=definition.capability_id or f"skill.{resource.id}",
            parameters=definition.parameters,
        )
        self.register(skill)
        return skill

    def descriptor(self, skill_id: str) -> ToolDescriptor:
        return self._skill(skill_id).descriptor

    async def invoke(self, skill_id: str, arguments: dict[str, object]) -> SkillInvocation:
        skill = self._skill(skill_id)
        return SkillInvocation(
            skill_id=skill.skill_id,
            capability_id=skill.capability_id,
            arguments=arguments,
        )

    def _skill(self, skill_id: str) -> DeclarativeSkill:
        skill = self._skills.get(skill_id)
        if skill is None:
            raise LookupError(f"skill {skill_id} not registered")
        return skill
