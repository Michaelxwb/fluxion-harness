"""[RULE-auth-001][RULE-rel-001] owner/审批/grant/binding/effective 与单关系维护。

真实边界：Console API → 真实 PostgreSQL 授权关系；effective 查询走内部通道 API。
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from muad_console_platform.infrastructure.models.control import (
    AgentSkillBinding,
    SkillUserGrant,
)
from sqlalchemy.ext.asyncio import AsyncSession

from .approval_support import (
    ApprovalEnv,
    approve,
    fill_draft,
    seed_second_agent,
    submit_user_skill,
)
from .draft_support import submit_draft

pytestmark = pytest.mark.integration


async def _effective_skill_ids(
    client: AsyncClient, env: ApprovalEnv, platform_user_id: uuid.UUID
) -> set[str]:
    response = await client.get(
        "/internal/channel/skills",
        params={
            "agent_id": str(env.agent_id),
            "platform_user_id": str(platform_user_id),
        },
        headers={"X-Tenant-Id": env.tenant},
    )
    assert response.status_code == 200, response.text
    return {item["skill_id"] for item in response.json()["data"]["items"]}


async def test_owner_approval_grant_binding_and_effective_visibility(
    internal_client: AsyncClient,
    admin_client: AsyncClient,
    approval_env: ApprovalEnv,
    session: AsyncSession,
) -> None:
    submitted = await submit_user_skill(
        internal_client, approval_env, key="auth-effective"
    )

    other_draft, other_revision = await fill_draft(
        internal_client,
        approval_env,
        key="auth-effective",
        owner_id=approval_env.other_user_id,
    )
    hijack = await submit_draft(
        internal_client,
        tenant_id=approval_env.tenant,
        owner_user_id=approval_env.other_user_id,
        draft_id=other_draft,
        expected_revision=other_revision,
        version="1.0.0",
        submitted_agent_id=approval_env.agent_id,
    )
    assert hijack.status_code == 409
    assert hijack.json()["code"] == "SKILL_KEY_EXISTS"

    approved = await approve(
        admin_client, submitted.skill_id, submitted.artifact_id
    )
    assert approved.status_code == 200, approved.text

    assert submitted.skill_id in await _effective_skill_ids(
        internal_client, approval_env, approval_env.owner_id
    )
    assert submitted.skill_id not in await _effective_skill_ids(
        internal_client, approval_env, approval_env.other_user_id
    )

    granted = await admin_client.post(
        f"/api/v1/skills/{submitted.skill_id}/users/{approval_env.other_user_id}"
    )
    assert granted.status_code == 200, granted.text
    assert submitted.skill_id in await _effective_skill_ids(
        internal_client, approval_env, approval_env.other_user_id
    )

    session.expire_all()
    binding = await session.scalar(
        sa.select(AgentSkillBinding).where(
            AgentSkillBinding.skill_id == uuid.UUID(submitted.skill_id),
            AgentSkillBinding.agent_id == approval_env.agent_id,
            AgentSkillBinding.is_deleted.is_(False),
        )
    )
    assert binding is not None
    grant = await session.scalar(
        sa.select(SkillUserGrant).where(
            SkillUserGrant.skill_id == uuid.UUID(submitted.skill_id),
            SkillUserGrant.user_id == approval_env.owner_id,
            SkillUserGrant.is_deleted.is_(False),
        )
    )
    assert grant is not None


async def test_relation_user_grant_post_and_delete_are_single_relation(
    internal_client: AsyncClient,
    admin_client: AsyncClient,
    approval_env: ApprovalEnv,
    session: AsyncSession,
) -> None:
    submitted = await submit_user_skill(
        internal_client, approval_env, key="auth-relation-grant"
    )
    approved = await approve(
        admin_client, submitted.skill_id, submitted.artifact_id
    )
    assert approved.status_code == 200, approved.text

    added = await admin_client.post(
        f"/api/v1/skills/{submitted.skill_id}/users/{approval_env.other_user_id}"
    )
    assert added.status_code == 200, added.text
    removed = await admin_client.delete(
        f"/api/v1/skills/{submitted.skill_id}/users/{approval_env.owner_id}"
    )
    assert removed.status_code == 200, removed.text

    session.expire_all()
    rows = {
        row.user_id: row
        for row in (
            await session.scalars(
                sa.select(SkillUserGrant).where(
                    SkillUserGrant.skill_id == uuid.UUID(submitted.skill_id)
                )
            )
        ).all()
    }
    assert rows[approval_env.owner_id].is_deleted is True
    assert rows[approval_env.other_user_id].is_deleted is False

    # 单关系 POST 幂等恢复，不产生第二行。
    restored = await admin_client.post(
        f"/api/v1/skills/{submitted.skill_id}/users/{approval_env.owner_id}"
    )
    assert restored.status_code == 200, restored.text
    session.expire_all()
    active = await session.scalar(
        sa.select(sa.func.count())
        .select_from(SkillUserGrant)
        .where(
            SkillUserGrant.skill_id == uuid.UUID(submitted.skill_id),
            SkillUserGrant.is_deleted.is_(False),
        )
    )
    assert active == 2


async def test_relation_agent_binding_delete_does_not_touch_other_agents(
    internal_client: AsyncClient,
    admin_client: AsyncClient,
    approval_env: ApprovalEnv,
    session: AsyncSession,
) -> None:
    submitted = await submit_user_skill(
        internal_client, approval_env, key="auth-relation-binding"
    )
    approved = await approve(
        admin_client, submitted.skill_id, submitted.artifact_id
    )
    assert approved.status_code == 200, approved.text

    second_agent = await seed_second_agent(session, approval_env)
    bound = await admin_client.post(
        f"/api/v1/agents/{second_agent}/skills/{submitted.skill_id}"
    )
    assert bound.status_code == 200, bound.text
    unbound = await admin_client.delete(
        f"/api/v1/agents/{approval_env.agent_id}/skills/{submitted.skill_id}"
    )
    assert unbound.status_code == 200, unbound.text

    session.expire_all()
    rows = {
        row.agent_id: row
        for row in (
            await session.scalars(
                sa.select(AgentSkillBinding).where(
                    AgentSkillBinding.skill_id == uuid.UUID(submitted.skill_id)
                )
            )
        ).all()
    }
    assert rows[approval_env.agent_id].is_deleted is True
    assert rows[second_agent].is_deleted is False
    active = await session.scalar(
        sa.select(sa.func.count())
        .select_from(AgentSkillBinding)
        .where(
            AgentSkillBinding.skill_id == uuid.UUID(submitted.skill_id),
            AgentSkillBinding.is_deleted.is_(False),
        )
    )
    assert active == 1
