from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

SKILL_CATALOG_HEADER = "## Available skills"


@dataclass(frozen=True, slots=True)
class PromptSkill:
    key: str
    name: str
    description: str
    platform_label: str | None = None


class PromptBuilder(Protocol):
    def build(self, *, instructions: str, skills: Sequence[PromptSkill]) -> str: ...


@dataclass(frozen=True, slots=True)
class DefaultPromptBuilder:
    prompt_template_version: str | None = None

    def build(self, *, instructions: str, skills: Sequence[PromptSkill]) -> str:
        sections = [instructions.strip()]
        if skills:
            lines = [SKILL_CATALOG_HEADER]
            lines.extend(self._skill_line(skill) for skill in skills)
            sections.append("\n".join(lines))
        if self.prompt_template_version is not None:
            sections.append(f"<!-- prompt_template_version: {self.prompt_template_version} -->")
        return "\n\n".join(sections)

    @staticmethod
    def _skill_line(skill: PromptSkill) -> str:
        label = f" [{skill.platform_label}]" if skill.platform_label else ""
        return f"- {skill.key}: {skill.name}{label} - {skill.description}"
