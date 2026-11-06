"""[E-10] 审批时复核提交者 AgentAccessGrant：撤销后拒绝且不自动恢复。

真实边界：Console 审批事务 → 真实 PostgreSQL AgentAccessGrant/artifact/grant/binding。
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from muad_console_platform.infrastructure.models.control import (
    AgentAccessGrant,
    AgentSkillBinding,
    Skill,
    SkillArtifact,
    SkillUserGrant,
)
from sqlalchemy.ext.asyncio import AsyncSession

from .approval_support import ApprovalEnv, approve, reject, submit_user_skill

pytestmark = pytest.mark.integration


async def test_e10_revoked_agent_access_blocks_approval_without_restore(
    internal_client: AsyncClient,
    admin_client: AsyncClient,
    approval_env: ApprovalEnv,
    session: AsyncSession,
) -> None:
    submitted = await submit_user_skill(
        internal_client, approval_env, key="e10-revoked"
    )
    await session.execute(
        sa.update(AgentAccessGrant)
        .where(AgentAccessGrant.id == approval_env.owner_grant_id)
        .values(is_deleted=True)
    )
    await session.commit()

    response = await approve(
        admin_client, submitted.skill_id, submitted.artifact_id
    )
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "AGENT_ACCESS_REVOKED"

    session.expire_all()
    artifact = await session.get(
        SkillArtifact, uuid.UUID(submitted.artifact_id)
    )
    assert artifact is not None
    assert artifact.review_status == "PENDING"
    assert artifact.reviewed_by is None
    skill = await session.get(Skill, uuid.UUID(submitted.skill_id))
    assert skill is not None
    assert skill.current_artifact_id is None
    assert (
        await session.scalar(
            sa.select(SkillUserGrant.id).where(
                SkillUserGrant.skill_id == skill.id,
                SkillUserGrant.user_id == approval_env.owner_id,
            )
        )
        is None
    )
    assert (
        await session.scalar(
            sa.select(AgentSkillBinding.id).where(
                AgentSkillBinding.skill_id == skill.id,
                AgentSkillBinding.agent_id == approval_env.agent_id,
            )
        )
        is None
    )
    grant = await session.get(AgentAccessGrant, approval_env.owner_grant_id)
    assert grant is not None
    assert grant.is_deleted is True, "撤销的 AgentAccessGrant 不能被审批自动恢复"

    # 审批失败不锁死流程：管理员仍可决定驳回，artifact 留在待处理状态。
    decided = await reject(
        admin_client,
        submitted.skill_id,
        submitted.artifact_id,
        review_note="提交者对 Agent 的访问已撤销，请重新申请授权",
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["data"]["review_status"] == "REJECTED"
