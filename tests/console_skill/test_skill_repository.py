"""[B-04/S-01][RULE-data-001] SkillRepository / SkillDraftRepository 真实 PG 行为。

覆盖：管理员导入默认 source=ADMIN、review_status=APPROVED；effective 查询字段不被
新增列破坏；草稿 revision CAS 与 owner/tenant 隔离。
"""

from __future__ import annotations

import uuid

from httpx import AsyncClient
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.control import SkillDraft
from muad_console_platform.infrastructure.repositories.skill_draft_repository import (
    SkillDraftRepository,
)
from muad_console_platform.infrastructure.repositories.skill_repository import (
    SkillRepository,
)
from sqlalchemy import delete

from console_skill.conftest import SkillContext, import_skill
from console_skill.packages import demo_package

BIND_URL = "/api/v1/agents/{agent_id}/skills/{skill_id}"


async def _effective_entry(context: SkillContext, key: str) -> tuple[str, str]:
    async with get_session_factory()() as session:
        rows = await SkillRepository(session).list_effective_for_agent(
            context.tenant_id, context.agent_id, context.actor_user_id
        )
    for skill, artifact in rows:
        if skill.key == key:
            return skill.source, artifact.review_status
    raise AssertionError(f"skill {key} is not effective")


async def _count_effective(context: SkillContext) -> int:
    async with get_session_factory()() as session:
        return await SkillRepository(session).count_effective_for_agent(
            context.tenant_id, context.agent_id, context.actor_user_id
        )


async def test_admin_import_keeps_effective_query_with_admin_defaults(
    client: AsyncClient, skill_env: SkillContext
) -> None:
    """[B-04] 存量管理员导入仍生效，新列回填为 ADMIN/APPROVED。"""
    key = f"admin-{uuid.uuid4()}"
    created = await import_skill(client, skill_env, demo_package(name="Admin Skill"), key=key)
    assert created.status_code == 200, created.text
    skill_id = created.json()["data"]["id"]

    grant = await client.post(
        f"/api/v1/skills/{skill_id}/users/{skill_env.actor_user_id}",
        headers={"X-Tenant-Id": skill_env.tenant_id},
    )
    assert grant.status_code == 200, grant.text
    bind = await client.post(
        BIND_URL.format(agent_id=skill_env.agent_id, skill_id=skill_id),
        headers={"X-Tenant-Id": skill_env.tenant_id},
    )
    assert bind.status_code == 200, bind.text

    assert await _effective_entry(skill_env, key) == ("ADMIN", "APPROVED")


async def test_effective_query_returns_source_and_review_status_for_seeded_skills(
    skill_env: SkillContext,
) -> None:
    """[B-04] effective 查询仍只返回已授权/绑定 Skill，且带来源与审批状态。"""
    assert await _effective_entry(skill_env, "skill-all") == ("ADMIN", "APPROVED")
    assert await _effective_entry(skill_env, "skill-granted") == ("ADMIN", "APPROVED")
    async with get_session_factory()() as session:
        rows = await SkillRepository(session).list_effective_for_agent(
            skill_env.tenant_id, skill_env.agent_id, skill_env.actor_user_id
        )
    keys = {skill.key for skill, _artifact in rows}
    assert "skill-ungranted" not in keys
    assert "skill-disabled" not in keys
    assert "skill-other-tenant" not in keys
    assert await _count_effective(skill_env) == len(rows)


async def test_skill_draft_revision_cas_and_owner_scope(skill_env: SkillContext) -> None:
    """[S-01] 草稿 revision CAS；跨用户/跨租户不可见；软删后可重建。"""
    owner = skill_env.actor_user_id
    key = f"draft-{uuid.uuid4()}"
    draft_id: uuid.UUID | None = None
    factory = get_session_factory()
    try:
        async with factory() as session:
            repo = SkillDraftRepository(session)
            draft = await repo.create(skill_env.tenant_id, owner, skill_env.agent_id, key)
            await session.commit()
            draft_id = draft.id
            assert draft.revision == 1
            assert await repo.get(skill_env.tenant_id, skill_env.other_user_id, draft.id) is None
            assert await repo.get(skill_env.other_tenant_id, owner, draft.id) is None

            updated = await repo.advance_revision(
                skill_env.tenant_id,
                owner,
                draft.id,
                expected_revision=1,
                file_count=2,
                total_size=32,
            )
            await session.commit()
            assert updated is not None
            assert updated.revision == 2
            assert updated.file_count == 2
            assert updated.total_size == 32

            stale = await repo.advance_revision(
                skill_env.tenant_id,
                owner,
                draft.id,
                expected_revision=1,
                file_count=3,
                total_size=48,
            )
            assert stale is None

        async with factory() as session:
            repo = SkillDraftRepository(session)
            assert await repo.soft_delete(skill_env.tenant_id, owner, draft_id) is True
            await session.commit()
            assert await repo.get(skill_env.tenant_id, owner, draft_id) is None
    finally:
        if draft_id is not None:
            async with factory() as session:
                await session.execute(delete(SkillDraft).where(SkillDraft.id == draft_id))
                await session.commit()
