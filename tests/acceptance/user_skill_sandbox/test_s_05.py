"""[S-05] 批准 PENDING：artifact/current 切换、提交者授权、上传 Agent 绑定与审计。

真实边界：Console API → 审批事务 → 真实 PostgreSQL/Artifact Store → 审计表；不 mock ORM/HTTP。
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from muad_console_platform.infrastructure.models.control import (
    AgentSkillBinding,
    ConfigAuditLog,
    Skill,
    SkillArtifact,
    SkillUserGrant,
)
from sqlalchemy.ext.asyncio import AsyncSession

from .approval_support import ApprovalEnv, approve, submit_user_skill
from .draft_support import draft_status

pytestmark = pytest.mark.integration


async def test_s05_approve_switches_current_grants_binds_and_audits(
    internal_client: AsyncClient,
    admin_client: AsyncClient,
    approval_env: ApprovalEnv,
    session: AsyncSession,
) -> None:
    submitted = await submit_user_skill(
        internal_client, approval_env, key="s05-approve"
    )
    skill_id = uuid.UUID(submitted.skill_id)
    artifact_id = uuid.UUID(submitted.artifact_id)

    pending_list = await admin_client.get(
        "/api/v1/skills", params={"review_status": "PENDING", "source": "USER"}
    )
    assert pending_list.status_code == 200, pending_list.text
    pending_items = pending_list.json()["data"]["items"]
    assert submitted.skill_id in {item["id"] for item in pending_items}
    pending_item = next(
        item for item in pending_items if item["id"] == submitted.skill_id
    )
    assert pending_item["review_status"] == "PENDING"
    assert pending_item["source"] == "USER"

    response = await approve(
        admin_client, submitted.skill_id, submitted.artifact_id
    )
    assert response.status_code == 200, response.text
    detail = response.json()["data"]
    assert detail["current_artifact_id"] == submitted.artifact_id
    assert detail["current_artifact"]["review_status"] == "APPROVED"
    assert detail["review_status"] == "APPROVED"

    session.expire_all()
    artifact = await session.get(SkillArtifact, artifact_id)
    assert artifact is not None
    assert artifact.review_status == "APPROVED"
    assert artifact.reviewed_by == approval_env.admin_id
    assert artifact.reviewed_at is not None
    skill = await session.get(Skill, skill_id)
    assert skill is not None
    assert skill.current_artifact_id == artifact.id

    grant = await session.scalar(
        sa.select(SkillUserGrant).where(
            SkillUserGrant.skill_id == skill_id,
            SkillUserGrant.user_id == approval_env.owner_id,
            SkillUserGrant.is_deleted.is_(False),
        )
    )
    assert grant is not None
    assert grant.granted_by == approval_env.admin_id
    binding = await session.scalar(
        sa.select(AgentSkillBinding).where(
            AgentSkillBinding.skill_id == skill_id,
            AgentSkillBinding.agent_id == approval_env.agent_id,
            AgentSkillBinding.is_deleted.is_(False),
        )
    )
    assert binding is not None

    audit = await session.scalar(
        sa.select(ConfigAuditLog).where(
            ConfigAuditLog.tenant_id == approval_env.tenant,
            ConfigAuditLog.action == "SKILL_REVIEW",
        )
    )
    assert audit is not None
    assert audit.actor_user_id == approval_env.admin_id
    assert audit.resource_id == skill_id
    assert audit.after_json is not None
    assert audit.after_json["skill_id"] == submitted.skill_id
    assert audit.after_json["artifact_id"] == submitted.artifact_id
    assert audit.after_json["agent_id"] == str(approval_env.agent_id)
    assert audit.after_json["result"] == "APPROVE"
    assert audit.after_json["review_status"] == "APPROVED"

    status = await draft_status(
        internal_client,
        tenant_id=approval_env.tenant,
        owner_user_id=approval_env.owner_id,
        draft_id=submitted.draft_id,
    )
    assert status.status_code == 200, status.text
    assert status.json()["data"]["review_status"] == "APPROVED"
    assert status.json()["data"]["artifact_id"] == submitted.artifact_id

    approved_list = await admin_client.get(
        "/api/v1/skills", params={"review_status": "APPROVED", "source": "USER"}
    )
    assert approved_list.status_code == 200, approved_list.text
    assert submitted.skill_id in {
        item["id"] for item in approved_list.json()["data"]["items"]
    }
    pending_after = await admin_client.get(
        "/api/v1/skills", params={"review_status": "PENDING", "source": "USER"}
    )
    assert pending_after.status_code == 200, pending_after.text
    assert submitted.skill_id not in {
        item["id"] for item in pending_after.json()["data"]["items"]
    }

    # 已审批锁定：再次审批同一 artifact 返回明确冲突，且不会重复授权/绑定。
    again = await approve(admin_client, submitted.skill_id, submitted.artifact_id)
    assert again.status_code == 409
    assert again.json()["code"] == "SKILL_ARTIFACT_NOT_PENDING"
    session.expire_all()
    grants = await session.scalar(
        sa.select(sa.func.count())
        .select_from(SkillUserGrant)
        .where(
            SkillUserGrant.skill_id == skill_id,
            SkillUserGrant.is_deleted.is_(False),
        )
    )
    bindings = await session.scalar(
        sa.select(sa.func.count())
        .select_from(AgentSkillBinding)
        .where(
            AgentSkillBinding.skill_id == skill_id,
            AgentSkillBinding.is_deleted.is_(False),
        )
    )
    assert grants == 1
    assert bindings == 1
