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


async def test_e09_deleted_skill_no_longer_locks_the_same_draft_revision(
    internal_client: AsyncClient,
    admin_client: AsyncClient,
    approval_env: ApprovalEnv,
    session: AsyncSession,
) -> None:
    """技能被删除后，草稿里那条"已审批回执"不再构成提交锁：同名重新提交会新建 PENDING 技能。

    修复前：`_replay_pending` 只看草稿的上次提交回执（APPROVED）就抛
    `SKILL_APPROVED_LOCKED`，即使技能已被删除也无法重新上传。
    """
    submitted = await submit_user_skill(internal_client, approval_env, key="e09-deleted")
    approved = await approve(admin_client, submitted.skill_id, submitted.artifact_id)
    assert approved.status_code == 200, approved.text

    unbound = await admin_client.delete(
        f"/api/v1/agents/{approval_env.agent_id}/skills/{submitted.skill_id}"
    )
    assert unbound.status_code == 200, unbound.text
    deleted = await admin_client.delete(f"/api/v1/skills/{submitted.skill_id}")
    assert deleted.status_code == 200, deleted.text

    again = await submit_draft(
        internal_client,
        tenant_id=approval_env.tenant,
        owner_user_id=approval_env.owner_id,
        draft_id=submitted.draft_id,
        expected_revision=submitted.revision,
        version="1.0.0",
        submitted_agent_id=approval_env.agent_id,
        submitted_channel="WEBCHAT",
    )
    assert again.status_code == 200, again.text
    data = again.json()["data"]
    assert data["skill_id"] != submitted.skill_id, "删除后用同一草稿重新提交会新建技能"
    assert data["review_status"] == "PENDING", "重新提交进入待审批，而不是被旧审批锁死"

    session.expire_all()
    old = await session.get(Skill, uuid.UUID(submitted.skill_id))
    assert old is not None and old.is_deleted is True
    fresh = await session.get(Skill, uuid.UUID(data["skill_id"]))
    assert fresh is not None and fresh.is_deleted is False
