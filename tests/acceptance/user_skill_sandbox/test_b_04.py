"""[B-04] 系统 Skill 在 effective 查询与执行快照中的 source/artifact 字段一致。

真实边界：`control.skill`/`agent_skill_binding` → `SkillRepository` → `ResolvedSkill` →
execution snapshot；无绑定可见性规则由 TASK-012 扩展，本用例只验证已有绑定下的字段一致性。
"""

from __future__ import annotations

import uuid

import pytest
from muad_console_platform.infrastructure.models.control import (
    AgentDefinition,
    AgentSkillBinding,
    ModelDefinition,
    Skill,
    SkillArtifact,
)
from muad_console_platform.infrastructure.repositories.skill_repository import SkillRepository
from muad_contracts import (
    ResolvedAgent,
    ResolvedModel,
    ResolvedSkill,
    SkillExecutionMode,
    SkillSource,
    WorkspaceRef,
    build_task_snapshot,
)
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

TENANT = f"acc-b04-{uuid.uuid4()}"


@pytest.fixture(autouse=True)
async def _cleanup(session: AsyncSession):
    yield
    await session.execute(
        delete(SkillArtifact).where(
            SkillArtifact.skill_id.in_(select_skill_ids(TENANT))
        )
    )
    await session.execute(
        delete(AgentSkillBinding).where(
            AgentSkillBinding.skill_id.in_(select_skill_ids(TENANT))
        )
    )
    await session.execute(delete(Skill).where(Skill.tenant_id == TENANT))
    await session.execute(delete(AgentDefinition).where(AgentDefinition.tenant_id == TENANT))
    await session.execute(delete(ModelDefinition).where(ModelDefinition.tenant_id == TENANT))
    await session.commit()


def select_skill_ids(tenant: str):
    from sqlalchemy import select

    return select(Skill.id).where(Skill.tenant_id == tenant)


def _artifact(skill_id: uuid.UUID, *, version: str) -> SkillArtifact:
    return SkillArtifact(
        skill_id=skill_id,
        version=version,
        checksum="sha256:" + uuid.uuid4().hex + uuid.uuid4().hex,
        storage_key=f"skills/{skill_id}/{uuid.uuid4()}/skill.zip",
        frontmatter_json={"platforms": ["mssp"]},
        manifest_json={"files": [], "file_count": 0, "total_size": 0},
        execution_mode="ASYNC",
        package_size=1,
        validation_status="READY",
        created_by=uuid.uuid4(),
        review_status="APPROVED",
        source="SYSTEM",
    )


async def _seed(session: AsyncSession) -> tuple[AgentDefinition, Skill, SkillArtifact]:
    model = ModelDefinition(
        tenant_id=TENANT,
        key=f"model-{uuid.uuid4()}",
        name="System Skill Model",
        model_id="gpt-4o-mini",
    )
    session.add(model)
    await session.flush()
    agent = AgentDefinition(
        tenant_id=TENANT,
        key=f"agent-{uuid.uuid4()}",
        name="System Skill Agent",
        instructions="run",
        model_id=model.id,
    )
    session.add(agent)
    await session.flush()
    skill = Skill(
        tenant_id=TENANT,
        key=f"system-{uuid.uuid4()}",
        name="System Skill",
        description="admin uploaded system skill",
        user_scope="ALL",
        enabled=True,
        source="SYSTEM",
    )
    session.add(skill)
    await session.flush()
    artifact = _artifact(skill.id, version="1.0.0")
    session.add(artifact)
    await session.flush()
    skill.current_artifact_id = artifact.id
    session.add(AgentSkillBinding(agent_id=agent.id, skill_id=skill.id))
    await session.commit()
    return agent, skill, artifact


async def test_b04_system_skill_effective_and_snapshot_agree(session: AsyncSession) -> None:
    """[B-04] effective 查询与执行快照都返回 SYSTEM + 同一 artifact/checksum。"""
    agent, skill, artifact = await _seed(session)
    repo = SkillRepository(session)
    rows = await repo.list_effective_for_agent(TENANT, agent.id, uuid.uuid4())
    matched = [(item, current) for item, current in rows if item.id == skill.id]
    assert len(matched) == 1
    resolved_skill, resolved_artifact = matched[0]
    assert resolved_skill.source == "SYSTEM"
    assert resolved_artifact.id == artifact.id
    assert resolved_artifact.review_status == "APPROVED"
    assert await repo.count_effective_for_agent(TENANT, agent.id, uuid.uuid4()) == len(rows)

    contract_skill = ResolvedSkill(
        skill_id=resolved_skill.id,
        artifact_id=resolved_artifact.id,
        key=resolved_skill.key,
        name=resolved_skill.name,
        description=resolved_skill.description,
        version=resolved_artifact.version,
        checksum=resolved_artifact.checksum,
        storage_key=resolved_artifact.storage_key,
        execution_mode=SkillExecutionMode.ASYNC,
        frontmatter=resolved_artifact.frontmatter_json,
        source=SkillSource(resolved_skill.source),
    )
    contract_agent = ResolvedAgent(
        id=agent.id, key=agent.key, revision=1, instructions="run", runtime_config={}
    )
    contract_model = ResolvedModel(
        id=uuid.uuid4(),
        revision=1,
        model_id="gpt-4o-mini",
        base_url="https://model.example/v1",
    )
    snapshot = build_task_snapshot(
        agent=contract_agent,
        model=contract_model,
        skill=contract_skill,
        workspace=WorkspaceRef(tenant_id=TENANT, user_id=uuid.uuid4(), agent_id=agent.id),
    )
    entry = snapshot["skills"][0]
    assert entry["source"] == "SYSTEM"
    assert entry["artifact_id"] == str(artifact.id)
    assert entry["checksum"] == artifact.checksum
