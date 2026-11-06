"""[E-08] USER Skill 的 owner 只认服务端 owner_user_id；跨用户同 key 拒绝且互不可见。

真实边界：Console 内部 API/通道 effective 查询 → 真实 PostgreSQL；不 mock owner 判定。
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
    fill_draft,
    submit_user_skill,
)
from .draft_support import draft_metadata, draft_status, submit_draft

pytestmark = pytest.mark.integration


async def test_e08_cross_user_same_key_rejected_without_visibility(
    internal_client: AsyncClient,
    approval_env: ApprovalEnv,
    session: AsyncSession,
) -> None:
    key = "e08-shared-key"
    owner_submission = await submit_user_skill(
        internal_client, approval_env, key=key
    )

    other_draft_id, other_revision = await fill_draft(
        internal_client,
        approval_env,
        key=key,
        owner_id=approval_env.other_user_id,
    )
    other_submit = await submit_draft(
        internal_client,
        tenant_id=approval_env.tenant,
        owner_user_id=approval_env.other_user_id,
        draft_id=other_draft_id,
        expected_revision=other_revision,
        version="1.0.0",
        submitted_agent_id=approval_env.agent_id,
        submitted_channel="WEBCHAT",
    )
    assert other_submit.status_code == 409, other_submit.text
    assert other_submit.json()["code"] == "SKILL_KEY_EXISTS"

    session.expire_all()
    original = await session.get(
        SkillArtifact, uuid.UUID(owner_submission.artifact_id)
    )
    assert original is not None
    assert original.review_status == "PENDING"
    pending = await session.scalar(
        sa.select(sa.func.count())
        .select_from(SkillArtifact)
        .where(
            SkillArtifact.skill_id == uuid.UUID(owner_submission.skill_id),
            SkillArtifact.review_status == "PENDING",
            SkillArtifact.is_deleted.is_(False),
        )
    )
    assert pending == 1

    hidden_metadata = await draft_metadata(
        internal_client,
        tenant_id=approval_env.tenant,
        owner_user_id=approval_env.other_user_id,
        draft_id=owner_submission.draft_id,
    )
    assert hidden_metadata.status_code == 404
    assert hidden_metadata.json()["code"] == "DRAFT_NOT_FOUND"
    hidden_status = await draft_status(
        internal_client,
        tenant_id=approval_env.tenant,
        owner_user_id=approval_env.other_user_id,
        draft_id=owner_submission.draft_id,
    )
    assert hidden_status.status_code == 404
    assert hidden_status.json()["code"] == "DRAFT_NOT_FOUND"

    for platform_user_id in (
        approval_env.owner_id,
        approval_env.other_user_id,
    ):
        effective = await internal_client.get(
            "/internal/channel/skills",
            params={
                "agent_id": str(approval_env.agent_id),
                "platform_user_id": str(platform_user_id),
            },
            headers={"X-Tenant-Id": approval_env.tenant},
        )
        assert effective.status_code == 200, effective.text
        items = effective.json()["data"]["items"]
        assert all(
            item["skill_id"] != owner_submission.skill_id for item in items
        ), "未审批的 PENDING 不能进入任何用户的 effective Skill 列表"
