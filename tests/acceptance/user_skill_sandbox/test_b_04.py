"""[B-04] 系统 Skill 在 effective 查询与执行快照中的 source/artifact 字段一致。

真实边界：`control.skill`/`agent_skill_binding` → `SkillRepository` → `ResolvedSkill` →
execution snapshot；系统 Skill 无 binding 的可见性由 TASK-012 扩展，本文件同时验证
list/count/catalog/execution/snapshot 五个入口口径一致且普通 Skill 不被误放大。
"""

from __future__ import annotations

import hashlib
import uuid

import pytest
from muad_console_platform.infrastructure.models.control import (
    AgentAccessGrant,
    AgentDefinition,
    AgentSkillBinding,
    ModelCredential,
    ModelDefinition,
    ModelDeployment,
    PlatformUser,
    Skill,
    SkillArtifact,
    SkillUserGrant,
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
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

TENANT = f"acc-b04-{uuid.uuid4()}"
OTHER_TENANT = f"acc-b04-other-{uuid.uuid4()}"


@pytest.fixture(autouse=True)
async def _cleanup(session: AsyncSession):
    yield
    for tenant in (TENANT, OTHER_TENANT):
        skill_ids = select(Skill.id).where(Skill.tenant_id == tenant)
        agent_ids = select(AgentDefinition.id).where(AgentDefinition.tenant_id == tenant)
        await session.execute(
            delete(SkillUserGrant).where(SkillUserGrant.skill_id.in_(skill_ids))
        )
        await session.execute(
            delete(AgentSkillBinding).where(AgentSkillBinding.skill_id.in_(skill_ids))
        )
        await session.execute(
            delete(SkillArtifact).where(SkillArtifact.skill_id.in_(skill_ids))
        )
        await session.execute(delete(Skill).where(Skill.tenant_id == tenant))
        await session.execute(
            delete(AgentAccessGrant).where(AgentAccessGrant.agent_id.in_(agent_ids))
        )
        await session.execute(delete(AgentDefinition).where(AgentDefinition.tenant_id == tenant))
        await session.execute(
            delete(ModelDeployment).where(ModelDeployment.tenant_id == tenant)
        )
        await session.execute(
            delete(ModelCredential).where(ModelCredential.tenant_id == tenant)
        )
        await session.execute(delete(ModelDefinition).where(ModelDefinition.tenant_id == tenant))
        await session.execute(delete(PlatformUser).where(PlatformUser.tenant_id == tenant))
    await session.commit()


def _artifact(
    skill_id: uuid.UUID,
    *,
    version: str,
    source: str = "SYSTEM",
    review_status: str = "APPROVED",
) -> SkillArtifact:
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
        review_status=review_status,
        source=source,
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

    contract_skill = _resolved_skill(resolved_skill, resolved_artifact)
    contract_agent = ResolvedAgent(
        id=agent.id, key=agent.key, revision=1, instructions="run", runtime_config={}
    )
    contract_model = _resolved_model()
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


class _QueryContext:
    def __init__(self, *, actor_id: uuid.UUID, agent_id: uuid.UUID, second_agent_id: uuid.UUID):
        self.actor_id = actor_id
        self.agent_id = agent_id
        self.second_agent_id = second_agent_id


async def _add_skill(
    session: AsyncSession,
    *,
    key: str,
    source: str,
    user_scope: str,
    enabled: bool = True,
    tenant_id: str = TENANT,
    binding_agent_id: uuid.UUID | None = None,
    grant_user_id: uuid.UUID | None = None,
) -> Skill:
    skill = Skill(
        tenant_id=tenant_id,
        key=key,
        name=f"Skill {key}",
        description="B-04 effective query seed",
        user_scope=user_scope,
        enabled=enabled,
        source=source,
    )
    session.add(skill)
    await session.flush()
    artifact = _artifact(skill.id, version="1.0.0", source=source)
    session.add(artifact)
    await session.flush()
    skill.current_artifact_id = artifact.id
    if binding_agent_id is not None:
        session.add(AgentSkillBinding(agent_id=binding_agent_id, skill_id=skill.id))
    if grant_user_id is not None:
        session.add(
            SkillUserGrant(
                skill_id=skill.id,
                user_id=grant_user_id,
                granted_by=uuid.uuid4(),
            )
        )
    await session.flush()
    return skill


async def _seed_effective_matrix(session: AsyncSession) -> _QueryContext:
    model = ModelDefinition(
        tenant_id=TENANT,
        key=f"model-{uuid.uuid4()}",
        name="Effective Model",
        model_id="gpt-4o-mini",
        provider_key="openai",
    )
    session.add(model)
    await session.flush()
    credential = ModelCredential(
        tenant_id=TENANT,
        key=f"credential-{uuid.uuid4()}",
        provider_key="openai",
        base_url="https://model.example/v1",
        api_key="sk-b04-fixture",
        api_key_digest=hashlib.sha256(b"sk-b04-fixture").hexdigest(),
        max_inflight=8,
    )
    session.add(credential)
    await session.flush()
    session.add(
        ModelDeployment(
            tenant_id=TENANT,
            key=f"member-{uuid.uuid4()}",
            model_id=model.id,
            credential_id=credential.id,
            weight=1,
        )
    )
    agent = AgentDefinition(
        tenant_id=TENANT,
        key=f"agent-{uuid.uuid4()}",
        name="Effective Agent",
        instructions="run",
        model_id=model.id,
    )
    second_agent = AgentDefinition(
        tenant_id=TENANT,
        key=f"agent-{uuid.uuid4()}",
        name="Second Effective Agent",
        instructions="run",
        model_id=model.id,
    )
    session.add_all([agent, second_agent])
    await session.flush()
    actor = PlatformUser(
        tenant_id=TENANT,
        user_code=f"b04-user-{uuid.uuid4()}",
        display_name="B-04 Actor",
    )
    session.add(actor)
    await session.flush()
    actor_id = actor.id
    session.add_all(
        [
            AgentAccessGrant(user_id=actor_id, agent_id=agent.id, granted_by=actor_id),
            AgentAccessGrant(user_id=actor_id, agent_id=second_agent.id, granted_by=actor_id),
        ]
    )
    await _add_skill(
        session, key="b04-system", source="SYSTEM", user_scope="ALL"
    )
    await _add_skill(
        session,
        key="b04-system-disabled",
        source="SYSTEM",
        user_scope="ALL",
        enabled=False,
    )
    await _add_skill(
        session, key="b04-admin-unbound", source="ADMIN", user_scope="ALL"
    )
    await _add_skill(
        session,
        key="b04-selected-no-grant",
        source="ADMIN",
        user_scope="SELECTED",
        binding_agent_id=agent.id,
    )
    await _add_skill(
        session,
        key="b04-selected-granted",
        source="ADMIN",
        user_scope="SELECTED",
        binding_agent_id=agent.id,
        grant_user_id=actor_id,
    )
    await _add_skill(
        session,
        key="b04-bound-second",
        source="ADMIN",
        user_scope="ALL",
        binding_agent_id=second_agent.id,
    )
    await _add_skill(
        session,
        key="b04-other-tenant-system",
        source="SYSTEM",
        user_scope="ALL",
        tenant_id=OTHER_TENANT,
    )
    await session.commit()
    return _QueryContext(
        actor_id=actor_id, agent_id=agent.id, second_agent_id=second_agent.id
    )


async def test_b04_system_skill_visible_without_binding_across_effective_queries(
    session: AsyncSession,
    internal_client,
) -> None:
    """[B-04] list/count/catalog/execution/snapshot 都包含无 binding 的 SYSTEM Skill。"""
    context = await _seed_effective_matrix(session)
    repo = SkillRepository(session)

    rows = await repo.list_effective_for_agent(
        TENANT, context.agent_id, context.actor_id
    )
    listed_keys = {skill.key for skill, _artifact in rows}
    assert listed_keys == {"b04-system", "b04-selected-granted"}
    assert await repo.count_effective_for_agent(
        TENANT, context.agent_id, context.actor_id
    ) == len(rows)
    assert "b04-admin-unbound" not in listed_keys
    assert "b04-selected-no-grant" not in listed_keys
    assert "b04-system-disabled" not in listed_keys
    assert "b04-other-tenant-system" not in listed_keys
    assert "b04-bound-second" not in listed_keys

    second_rows = await repo.list_effective_for_agent(
        TENANT, context.second_agent_id, context.actor_id
    )
    assert {skill.key for skill, _artifact in second_rows} == {
        "b04-system",
        "b04-bound-second",
    }

    catalog = await internal_client.get(
        "/internal/channel/skills",
        params={
            "agent_id": str(context.agent_id),
            "platform_user_id": str(context.actor_id),
        },
        headers={"X-Tenant-Id": TENANT},
    )
    assert catalog.status_code == 200, catalog.text
    catalog_data = catalog.json()["data"]
    assert catalog_data["total"] == 2
    assert {item["key"] for item in catalog_data["items"]} == listed_keys

    resolved = await internal_client.post(
        "/internal/runtime/resolve-definition",
        json={
            "agent_id": str(context.agent_id),
            "actor_user_id": str(context.actor_id),
            "channel": "WECOM",
        },
        headers={"X-Tenant-Id": TENANT},
    )
    assert resolved.status_code == 200, resolved.text
    resolved_data = resolved.json()["data"]
    resolved_skills = {entry["key"]: entry for entry in resolved_data["skills"]}
    assert set(resolved_skills) == listed_keys
    system_entry = resolved_skills["b04-system"]
    assert system_entry["source"] == "SYSTEM"

    contract_skill = ResolvedSkill(**system_entry)
    snapshot = build_task_snapshot(
        agent=ResolvedAgent(**resolved_data["agent"]),
        model=ResolvedModel(**resolved_data["model"]),
        skill=contract_skill,
        workspace=WorkspaceRef(
            tenant_id=TENANT,
            user_id=context.actor_id,
            agent_id=context.agent_id,
        ),
    )
    entry = snapshot["skills"][0]
    assert entry["key"] == "b04-system"
    assert entry["source"] == "SYSTEM"
    assert entry["artifact_id"] == system_entry["artifact_id"]
    assert entry["checksum"] == system_entry["checksum"]


def _resolved_skill(skill: Skill, artifact: SkillArtifact) -> ResolvedSkill:
    return ResolvedSkill(
        skill_id=skill.id,
        artifact_id=artifact.id,
        key=skill.key,
        name=skill.name,
        description=skill.description,
        version=artifact.version,
        checksum=artifact.checksum,
        storage_key=artifact.storage_key,
        execution_mode=SkillExecutionMode.ASYNC,
        frontmatter=artifact.frontmatter_json,
        source=SkillSource(skill.source),
    )


def _resolved_model() -> ResolvedModel:
    return ResolvedModel(
        id=uuid.uuid4(),
        revision=1,
        model_id="gpt-4o-mini",
        base_url="https://model.example/v1",
    )
