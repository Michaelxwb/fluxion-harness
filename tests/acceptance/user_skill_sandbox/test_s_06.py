"""[S-06] 驳回必须给原因；用户可读回原因；修改后可重新提交新的 PENDING。

真实边界：Console API → 审批状态机 → 真实 PostgreSQL；驳回原因经内部 status API 读回。
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from muad_console_platform.infrastructure.models.control import SkillArtifact
from sqlalchemy.ext.asyncio import AsyncSession

from .approval_support import (
    ApprovalEnv,
    modify_and_submit,
    reject,
    submit_user_skill,
)
from .draft_support import draft_status

pytestmark = pytest.mark.integration

REJECT_NOTE = "请补充平台登录说明与失败重试策略"


async def test_s06_reject_requires_note_then_allows_resubmission(
    internal_client: AsyncClient,
    admin_client: AsyncClient,
    approval_env: ApprovalEnv,
    session: AsyncSession,
) -> None:
    submitted = await submit_user_skill(
        internal_client, approval_env, key="s06-reject"
    )

    missing_note = await reject(
        admin_client, submitted.skill_id, submitted.artifact_id
    )
    assert missing_note.status_code == 422
    assert missing_note.json()["code"] == "COMMON_VALIDATION_ERROR"

    rejected = await reject(
        admin_client,
        submitted.skill_id,
        submitted.artifact_id,
        review_note=REJECT_NOTE,
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["data"]["review_status"] == "REJECTED"

    session.expire_all()
    artifact = await session.get(
        SkillArtifact, uuid.UUID(submitted.artifact_id)
    )
    assert artifact is not None
    assert artifact.review_status == "REJECTED"
    assert artifact.review_note == REJECT_NOTE
    assert artifact.reviewed_by == approval_env.admin_id
    assert artifact.reviewed_at is not None

    visible = await draft_status(
        internal_client,
        tenant_id=approval_env.tenant,
        owner_user_id=approval_env.owner_id,
        draft_id=submitted.draft_id,
    )
    assert visible.status_code == 200, visible.text
    data = visible.json()["data"]
    assert data["review_status"] == "REJECTED"
    assert data["review_note"] == REJECT_NOTE
    assert data["artifact_id"] == submitted.artifact_id

    resubmitted = await modify_and_submit(
        internal_client, approval_env, submitted, version="1.0.1"
    )
    assert resubmitted.status_code == 200, resubmitted.text
    new_artifact_id = resubmitted.json()["data"]["artifact_id"]
    assert new_artifact_id != submitted.artifact_id
    assert resubmitted.json()["data"]["review_status"] == "PENDING"

    session.expire_all()
    rows = (
        await session.scalars(
            sa.select(SkillArtifact)
            .where(
                SkillArtifact.skill_id == uuid.UUID(submitted.skill_id),
                SkillArtifact.is_deleted.is_(False),
            )
            .order_by(SkillArtifact.create_time, SkillArtifact.id)
        )
    ).all()
    assert {row.id: row.review_status for row in rows}[
        uuid.UUID(submitted.artifact_id)
    ] == "REJECTED"
    assert [row.review_status for row in rows].count("PENDING") == 1
