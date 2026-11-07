"""[E-09] 已审批 Skill 锁定：用户提交返回 SKILL_APPROVED_LOCKED，现有 artifact 不变。

真实边界：Console 草稿提交 API → 真实 PostgreSQL；锁定判断来自服务端审批状态。
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from muad_console_platform.infrastructure.models.control import Skill, SkillArtifact
from sqlalchemy.ext.asyncio import AsyncSession

from .approval_support import (
    ApprovalEnv,
    approve,
    modify_and_submit,
    submit_user_skill,
)
from .draft_support import submit_draft

pytestmark = pytest.mark.integration


async def test_e09_approved_skill_is_locked_against_new_submissions(
    internal_client: AsyncClient,
    admin_client: AsyncClient,
    approval_env: ApprovalEnv,
    session: AsyncSession,
) -> None:
    submitted = await submit_user_skill(
        internal_client, approval_env, key="e09-locked"
    )
    approved = await approve(
        admin_client, submitted.skill_id, submitted.artifact_id
    )
    assert approved.status_code == 200, approved.text

    same_revision = await submit_draft(
        internal_client,
        tenant_id=approval_env.tenant,
        owner_user_id=approval_env.owner_id,
        draft_id=submitted.draft_id,
        expected_revision=submitted.revision,
        version="1.0.0",
        submitted_agent_id=approval_env.agent_id,
        submitted_channel="WEBCHAT",
    )
    assert same_revision.status_code == 409
    assert same_revision.json()["code"] == "SKILL_APPROVED_LOCKED"

    changed = await modify_and_submit(
        internal_client, approval_env, submitted, version="1.0.1"
    )
    assert changed.status_code == 409
    assert changed.json()["code"] == "SKILL_APPROVED_LOCKED"

    session.expire_all()
    skill = await session.get(Skill, uuid.UUID(submitted.skill_id))
    assert skill is not None
    assert str(skill.current_artifact_id) == submitted.artifact_id
    artifacts = (
        await session.scalars(
            sa.select(SkillArtifact).where(
                SkillArtifact.skill_id == skill.id,
                SkillArtifact.is_deleted.is_(False),
            )
        )
    ).all()
    assert len(artifacts) == 1
    assert artifacts[0].review_status == "APPROVED"
