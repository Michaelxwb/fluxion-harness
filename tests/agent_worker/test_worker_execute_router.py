"""Worker execute(target=skill) routing over frozen snapshot material."""

from __future__ import annotations

import uuid

import pytest
from muad_agent_core.tools import ToolError
from muad_agent_worker.application.agentic.skill_router import SkillExecutionRouter
from muad_contracts import SnapshotSkill, SnapshotTool, ToolRetrySafety


def _skill(*, key: str = "report") -> SnapshotSkill:
    return SnapshotSkill(
        skill_id=uuid.uuid4(),
        key=key,
        artifact_id=uuid.uuid4(),
        version="1.0.0",
        checksum="sha256:" + "a" * 64,
        storage_key="skills/report.zip",
        source="ADMIN",
        execution_mode="SYNC",
        frontmatter={"name": key},
    )


def _tool(skill: SnapshotSkill, name: str = "run", *, mode: str = "SYNC") -> SnapshotTool:
    return SnapshotTool(
        source="SKILL",
        tool_name=name,
        input_schema={
            "type": "object",
            "properties": {"q": {"type": "string"}},
            "required": ["q"],
            "additionalProperties": False,
        },
        retry_safety="IDEMPOTENT",
        skill_id=skill.skill_id,
        artifact_id=skill.artifact_id,
        checksum=skill.checksum,
        execution_mode=mode,
    )


def _router(skill: SnapshotSkill, tools: list[SnapshotTool]) -> SkillExecutionRouter:
    return SkillExecutionRouter(
        skills=(skill,),
        tools=tools,
        allowlist=tuple(f"SKILL:{skill.skill_id}:{tool.tool_name}" for tool in tools),
        skill_grants=(str(skill.skill_id),),
    )


def test_key_comes_from_snapshot_not_frontmatter() -> None:
    skill = _skill(key="report")
    router = _router(skill, [_tool(skill)])

    route = router.resolve("report", None)

    assert route.skill.key == "report"
    assert route.tool.tool_name == "run"
    assert route.retry_safety is ToolRetrySafety.IDEMPOTENT


def test_multiple_tools_require_explicit_tool() -> None:
    skill = _skill()
    router = _router(skill, [_tool(skill, "one"), _tool(skill, "two")])

    with pytest.raises(ToolError) as missing:
        router.resolve("report", None)
    assert missing.value.code == "SKILL_TOOL_REQUIRED"

    route = router.resolve("report", "two")
    assert route.tool.tool_name == "two"


def test_instructions_only_skill_is_not_executable() -> None:
    skill = _skill()
    router = _router(skill, [])

    with pytest.raises(ToolError) as missing:
        router.resolve("report", None)
    assert missing.value.code == "SKILL_NOT_EXECUTABLE"


def test_unknown_or_ungranted_skill_is_not_effective() -> None:
    skill = _skill()
    router = _router(skill, [_tool(skill)])

    with pytest.raises(ToolError) as missing:
        router.resolve("other", None)
    assert missing.value.code == "SKILL_NOT_EFFECTIVE"


def test_input_schema_and_argv_are_enforced() -> None:
    skill = _skill()
    router = _router(skill, [_tool(skill)])
    route = router.resolve("report", None)

    with pytest.raises(ToolError) as invalid:
        router.validate_input(route, {"unexpected": 1})
    assert invalid.value.code == "SKILL_ARGUMENTS_INVALID"

    with pytest.raises(ToolError) as bad_argv:
        router.validate_argv(["ok", 1])
    assert bad_argv.value.code == "SKILL_ARGUMENTS_INVALID"


def test_missing_input_still_validates_the_tool_schema() -> None:
    skill = _skill()
    router = _router(skill, [_tool(skill)])
    route = router.resolve("report", None)

    with pytest.raises(ToolError) as invalid:
        SkillExecutionRouter.validate_input(route, None)

    assert invalid.value.code == "SKILL_ARGUMENTS_INVALID"
