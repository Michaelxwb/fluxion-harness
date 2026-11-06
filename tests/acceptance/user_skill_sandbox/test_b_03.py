"""[B-03] 同一 owner 覆盖自己的 PENDING：旧包 SUPERSEDED，新包成为唯一待审。

真实边界：Console API → 提交/审批状态机 → 真实 PostgreSQL；旧包审批必须被拒绝。
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

pytestmark = pytest.mark.integration


async def test_b03_superseded_pending_cannot_be_approved(
    internal_client: AsyncClient,
    admin_client: AsyncClient,
    approval_env: ApprovalEnv,
    session: AsyncSession,
) -> None:
    first = await submit_user_skill(
        internal_client, approval_env, key="b03-supersede"
    )
    second = await modify_and_submit(
        internal_client, approval_env, first, version="1.0.1"
    )
    assert second.status_code == 200, second.text
    second_artifact_id = second.json()["data"]["artifact_id"]
    assert second_artifact_id != first.artifact_id

    session.expire_all()
    skill = await session.get(Skill, uuid.UUID(first.skill_id))
    assert skill is not None
    rows = (
        await session.scalars(
            sa.select(SkillArtifact).where(
                SkillArtifact.skill_id == skill.id,
                SkillArtifact.is_deleted.is_(False),
            )
        )
    ).all()
    statuses = {str(row.id): row.review_status for row in rows}
    assert statuses[first.artifact_id] == "SUPERSEDED"
    assert statuses[second_artifact_id] == "PENDING"
    pending = [row for row in rows if row.review_status == "PENDING"]
    assert len(pending) == 1
    assert str(pending[0].id) == second_artifact_id

    stale = await approve(admin_client, first.skill_id, first.artifact_id)
    assert stale.status_code == 409
    assert stale.json()["code"] == "SKILL_ARTIFACT_NOT_PENDING"

    session.expire_all()
    skill = await session.get(Skill, uuid.UUID(first.skill_id))
    assert skill is not None
    assert skill.current_artifact_id is None
    unchanged = await session.get(SkillArtifact, uuid.UUID(first.artifact_id))
    assert unchanged is not None
    assert unchanged.review_status == "SUPERSEDED"

    approved = await approve(admin_client, first.skill_id, second_artifact_id)
    assert approved.status_code == 200, approved.text
    session.expire_all()
    skill = await session.get(Skill, uuid.UUID(first.skill_id))
    assert skill is not None
    assert str(skill.current_artifact_id) == second_artifact_id
