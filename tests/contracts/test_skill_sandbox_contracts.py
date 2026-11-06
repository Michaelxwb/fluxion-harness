"""[S-10/B-04][RULE-snapshot-001] execution snapshot 冻结 Skill 来源/artifact/checksum/workspace/平台键。

真实契约入口：`muad_contracts.build_task_snapshot` 与 Runtime 的 `build_snapshot`，
不是测试内自建的字典；缺失/非法 source 由 `ResolvedSkill` 校验直接拒绝。
"""

from __future__ import annotations

import uuid

import pytest
from muad_contracts import (
    ResolvedAgent,
    ResolveDefinitionResponse,
    ResolvedModel,
    ResolvedSkill,
    SkillExecutionMode,
    SkillSource,
    WorkspaceRef,
    build_task_snapshot,
    snapshot_hash,
)
from pydantic import ValidationError


def _agent() -> ResolvedAgent:
    return ResolvedAgent(
        id=uuid.uuid4(),
        key="agent-key",
        revision=1,
        instructions="answer",
        runtime_config={},
    )


def _model() -> ResolvedModel:
    return ResolvedModel(
        id=uuid.uuid4(),
        revision=1,
        model_id="gpt-4o-mini",
        base_url="https://model.example/v1",
    )


def _skill(source: SkillSource = SkillSource.USER) -> ResolvedSkill:
    return ResolvedSkill(
        skill_id=uuid.uuid4(),
        artifact_id=uuid.uuid4(),
        key="my-skill",
        name="My Skill",
        description="user skill",
        version="1.0.0",
        checksum="sha256:" + "a" * 64,
        storage_key="skills/my-skill/1.0.0/skill.zip",
        execution_mode=SkillExecutionMode.ASYNC,
        frontmatter={"platforms": ["mssp", "mssw"]},
        source=source,
    )


def _workspace(agent: ResolvedAgent) -> WorkspaceRef:
    return WorkspaceRef(
        tenant_id="tenant-1",
        user_id=uuid.uuid4(),
        agent_id=agent.id,
    )


def test_snapshot_freezes_source_artifact_workspace_and_platform_keys() -> None:
    """[S-10] skills[] 同时冻结来源、artifact、checksum、workspace 与平台键。"""
    agent, model, skill = _agent(), _model(), _skill(SkillSource.USER)
    workspace = _workspace(agent)

    snapshot = build_task_snapshot(
        agent=agent,
        model=model,
        skill=skill,
        workspace=workspace,
    )
    entry = snapshot["skills"][0]

    assert entry["source"] == "USER"
    assert entry["skill_id"] == str(skill.skill_id)
    assert entry["artifact_id"] == str(skill.artifact_id)
    assert entry["checksum"] == skill.checksum
    assert entry["workspace"] == {
        "tenant_id": "tenant-1",
        "user_id": str(workspace.user_id),
        "agent_id": str(agent.id),
    }
    assert entry["platforms"] == ["mssp", "mssw"]
    assert snapshot["schema_version"] >= 2


@pytest.mark.parametrize("source", [SkillSource.SYSTEM, SkillSource.ADMIN, SkillSource.USER])
def test_snapshot_preserves_every_skill_source(source: SkillSource) -> None:
    """[B-04] SYSTEM/ADMIN/USER 三种来源都进入冻结快照且不被归一化。"""
    agent, model, skill = _agent(), _model(), _skill(source)
    snapshot = build_task_snapshot(
        agent=agent,
        model=model,
        skill=skill,
        workspace=_workspace(agent),
    )
    assert snapshot["skills"][0]["source"] == str(source)


def test_snapshot_rejects_missing_or_invalid_source() -> None:
    """[RULE-snapshot-001] 缺失/非法 source 在契约层拒绝，不补默认值。"""
    values = _skill().model_dump()
    values.pop("source")
    with pytest.raises(ValidationError):
        ResolvedSkill.model_validate(values)
    with pytest.raises(ValidationError):
        ResolvedSkill.model_validate({**_skill().model_dump(), "source": "ROOT"})


def test_snapshot_hash_covers_source_and_workspace() -> None:
    """[RULE-snapshot-001] 来源或 workspace 变化必须改变快照指纹。"""
    agent, model = _agent(), _model()
    base = _skill(SkillSource.ADMIN)
    workspace = _workspace(agent)
    first = build_task_snapshot(agent=agent, model=model, skill=base, workspace=workspace)
    same = build_task_snapshot(agent=agent, model=model, skill=base, workspace=workspace)
    other_source = build_task_snapshot(
        agent=agent, model=model, skill=_skill(SkillSource.USER), workspace=workspace
    )
    other_workspace = build_task_snapshot(
        agent=agent,
        model=model,
        skill=base,
        workspace=WorkspaceRef(
            tenant_id="tenant-2", user_id=workspace.user_id, agent_id=agent.id
        ),
    )

    assert snapshot_hash(first) == snapshot_hash(same)
    assert snapshot_hash(first) != snapshot_hash(other_source)
    assert snapshot_hash(first) != snapshot_hash(other_workspace)


def test_runtime_snapshot_freezes_skill_source() -> None:
    """[S-10] RuntimeSnapshot 的 skill_catalog_json 同样冻结 source。"""
    from muad_agent_runtime.application.run_service import build_snapshot

    agent, model, skill = _agent(), _model(), _skill(SkillSource.SYSTEM)
    resolved = ResolveDefinitionResponse(agent=agent, model=model, skills=[skill], mcp_servers=[])

    snapshot = build_snapshot(run_id=uuid.uuid4(), tenant_id="tenant-1", resolved=resolved)

    assert snapshot.skill_catalog_json[0]["source"] == "SYSTEM"
    assert snapshot.skill_catalog_json[0]["artifact_id"] == str(skill.artifact_id)
    assert snapshot.skill_catalog_json[0]["checksum"] == skill.checksum
