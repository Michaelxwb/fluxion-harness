"""Worker-side execute(target=skill) router over frozen SnapshotSkill/SnapshotTool."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import jsonschema
from muad_agent_core.failures import SKILL_NOT_EFFECTIVE, skill_not_effective_message
from muad_agent_core.tools import ToolError
from muad_contracts import SnapshotSkill, SnapshotTool, ToolRetrySafety
from muad_contracts.skill_invocation import validate_skill_argv

SKILL_TOOL_REQUIRED = "SKILL_TOOL_REQUIRED"
SKILL_ARGUMENTS_INVALID = "SKILL_ARGUMENTS_INVALID"
SKILL_NOT_EXECUTABLE = "SKILL_NOT_EXECUTABLE"
TOOL_NOT_ALLOWED = "TOOL_NOT_ALLOWED"


@dataclass(frozen=True, slots=True)
class SkillExecutionRoute:
    skill: SnapshotSkill
    tool: SnapshotTool
    allowlist_key: str
    input_schema: Mapping[str, Any]
    retry_safety: ToolRetrySafety
    execution_mode: str


class SkillExecutionRouter:
    def __init__(
        self,
        *,
        skills: Sequence[SnapshotSkill],
        tools: Sequence[SnapshotTool],
        allowlist: Sequence[str],
        skill_grants: Sequence[Any],
    ) -> None:
        self._skills = {skill.key: skill for skill in skills}
        self._tools = tuple(tools)
        self._allowlist = set(allowlist)
        self._skill_grants = {str(value) for value in skill_grants}

    def resolve(self, skill_key: object, tool_name: object) -> SkillExecutionRoute:
        if not isinstance(skill_key, str) or not skill_key:
            raise ToolError("COMMON_VALIDATION_ERROR", "skill_key must be a non-empty string")
        skill = self._skills.get(skill_key)
        if skill is None or str(skill.skill_id) not in self._skill_grants:
            raise ToolError(SKILL_NOT_EFFECTIVE, skill_not_effective_message(skill_key))
        executable = [
            tool
            for tool in self._tools
            if tool.source == "SKILL"
            and tool.skill_id == skill.skill_id
            and f"SKILL:{skill.skill_id}:{tool.tool_name}" in self._allowlist
        ]
        if not executable:
            raise ToolError(SKILL_NOT_EXECUTABLE, f"skill has no executable tool: {skill_key}")
        selected: SnapshotTool
        if tool_name is None or tool_name == "":
            if len(executable) != 1:
                raise ToolError(
                    SKILL_TOOL_REQUIRED,
                    f"skill {skill_key} exposes multiple tools; pass tool",
                )
            selected = executable[0]
        else:
            if not isinstance(tool_name, str):
                raise ToolError("COMMON_VALIDATION_ERROR", "tool must be a string")
            selected = next((tool for tool in executable if tool.tool_name == tool_name), None)  # type: ignore[assignment]
            if selected is None:
                raise ToolError(TOOL_NOT_ALLOWED, f"tool is not allowed: {tool_name}")
        retry = _retry_safety(selected)
        return SkillExecutionRoute(
            skill=skill,
            tool=selected,
            allowlist_key=f"SKILL:{skill.skill_id}:{selected.tool_name}",
            input_schema=dict(selected.input_schema),
            retry_safety=retry,
            execution_mode=selected.execution_mode or skill.execution_mode,
        )

    @staticmethod
    def validate_input(route: SkillExecutionRoute, value: object) -> dict[str, Any]:
        data = {} if value is None else value
        if not isinstance(data, Mapping):
            raise ToolError(SKILL_ARGUMENTS_INVALID, "input must be an object")
        schema = dict(route.input_schema) or {"type": "object", "additionalProperties": True}
        try:
            jsonschema.validate(instance=dict(data), schema=schema)
        except jsonschema.ValidationError as exc:
            raise ToolError(SKILL_ARGUMENTS_INVALID, exc.message) from exc
        return dict(data)

    @staticmethod
    def validate_argv(value: object) -> tuple[str, ...]:
        try:
            return validate_skill_argv(value or ())
        except ValueError as exc:
            raise ToolError(SKILL_ARGUMENTS_INVALID, str(exc)) from exc


def _retry_safety(tool: SnapshotTool) -> ToolRetrySafety:
    try:
        return ToolRetrySafety(tool.retry_safety or ToolRetrySafety.NON_RETRYABLE)
    except ValueError:
        return ToolRetrySafety.NON_RETRYABLE


__all__ = [
    "SKILL_ARGUMENTS_INVALID",
    "SKILL_NOT_EFFECTIVE",
    "SKILL_NOT_EXECUTABLE",
    "SKILL_TOOL_REQUIRED",
    "TOOL_NOT_ALLOWED",
    "SkillExecutionRoute",
    "SkillExecutionRouter",
]
