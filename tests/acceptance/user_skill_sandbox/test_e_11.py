"""[E-11] 并发审批/提交只有一方成功；current_artifact_id 不指向未批准版本。

真实边界：两个独立 HTTP 请求 → 两个真实数据库事务 → artifact 行锁/状态复核。
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from muad_console_platform.infrastructure.models.control import Skill, SkillArtifact
from sqlalchemy.ext.asyncio import AsyncSession

from .approval_support import (
    ApprovalEnv,
    approve,
    authenticated_admin,
    fill_draft,
    submit_user_skill,
)
from .draft_support import submit_draft

pytestmark = pytest.mark.integration

CONFLICT_CODES = {"SKILL_ARTIFACT_NOT_PENDING", "SKILL_REVIEW_CONFLICT"}


async def test_e11_concurrent_approval_has_one_winner(
    internal_client: AsyncClient,
    approval_env: ApprovalEnv,
    session: AsyncSession,
) -> None:
    submitted = await submit_user_skill(
        internal_client, approval_env, key="e11-approve-race"
    )
    async with authenticated_admin(approval_env) as first, authenticated_admin(
        approval_env
    ) as second:
        responses = await asyncio.gather(
            approve(first, submitted.skill_id, submitted.artifact_id),
            approve(second, submitted.skill_id, submitted.artifact_id),
        )
    statuses = sorted(response.status_code for response in responses)
    assert statuses == [200, 409]
    loser = next(response for response in responses if response.status_code == 409)
    assert loser.json()["code"] in CONFLICT_CODES

    session.expire_all()
    skill = await session.get(Skill, uuid.UUID(submitted.skill_id))
    assert skill is not None
    assert skill.current_artifact_id is not None
    current = await session.get(SkillArtifact, skill.current_artifact_id)
    assert current is not None
    assert current.review_status == "APPROVED"
    approved = await session.scalar(
        sa.select(sa.func.count())
        .select_from(SkillArtifact)
        .where(
            SkillArtifact.skill_id == skill.id,
            SkillArtifact.review_status == "APPROVED",
            SkillArtifact.is_deleted.is_(False),
        )
    )
    assert approved == 1


async def test_e11_concurrent_submission_creates_single_pending(
    internal_client: AsyncClient,
    approval_env: ApprovalEnv,
    session: AsyncSession,
) -> None:
    draft_id, revision = await fill_draft(
        internal_client, approval_env, key="e11-submit-race"
    )
    responses = await asyncio.gather(
        submit_draft(
            internal_client,
            tenant_id=approval_env.tenant,
            owner_user_id=approval_env.owner_id,
            draft_id=draft_id,
            expected_revision=revision,
            version="1.0.0",
            submitted_agent_id=approval_env.agent_id,
            submitted_channel="WEBCHAT",
        ),
        submit_draft(
            internal_client,
            tenant_id=approval_env.tenant,
            owner_user_id=approval_env.owner_id,
            draft_id=draft_id,
            expected_revision=revision,
            version="1.0.1",
            submitted_agent_id=approval_env.agent_id,
            submitted_channel="WEBCHAT",
        ),
    )
    statuses = sorted(response.status_code for response in responses)
    assert statuses == [200, 409]
    loser = next(response for response in responses if response.status_code == 409)
    assert loser.json()["code"] in {"SKILL_PENDING_EXISTS", "DRAFT_REVISION_CONFLICT"}

    session.expire_all()
    pending_rows = (
        await session.scalars(
            sa.select(SkillArtifact).where(
                SkillArtifact.skill_id.in_(
                    sa.select(Skill.id).where(
                        Skill.tenant_id == approval_env.tenant,
                        Skill.key == "e11-submit-race",
                    )
                ),
                SkillArtifact.review_status == "PENDING",
                SkillArtifact.is_deleted.is_(False),
            )
        )
    ).all()
    assert len(pending_rows) == 1
    skill = await session.scalar(
        sa.select(Skill).where(
            Skill.tenant_id == approval_env.tenant,
            Skill.key == "e11-submit-race",
            Skill.is_deleted.is_(False),
        )
    )
    assert skill is not None
    assert skill.current_artifact_id is None
