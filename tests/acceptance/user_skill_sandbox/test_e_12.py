"""[E-12] 非管理员执行审批/下载/解绑/系统上传被拒绝，且不泄露资源存在性。

真实边界：真实 Console 登录与角色（BUILDER）→ 真实 ASGI 权限依赖 → 真实 PostgreSQL
artifact/grant/binding 行；`AdminAccount`/管理员分支在进入业务查询前拒绝，因此随机
资源 ID 也只能得到 403，不会因 404/403 差异暴露他人资源是否存在。
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from muad_console_platform.infrastructure.models.control import (
    AgentSkillBinding,
    Skill,
    SkillArtifact,
    SkillUserGrant,
)
from sqlalchemy.ext.asyncio import AsyncSession

from tests.console_skill.packages import demo_package

from .approval_support import (
    ApprovalEnv,
    approve,
    authenticated_builder,
    seed_builder_account,
    submit_user_skill,
)

pytestmark = pytest.mark.integration

DOWNLOAD = "/api/v1/skills/{skill_id}/artifacts/{artifact_id}/download"


def _download_url(skill_id: str, artifact_id: str) -> str:
    return DOWNLOAD.format(skill_id=skill_id, artifact_id=artifact_id)


def _assert_forbidden(response, action: str) -> None:
    assert response.status_code == 403, f"{action}: {response.text}"
    assert response.json()["code"] == "FORBIDDEN", action


async def test_e12_builder_is_forbidden_for_admin_skill_operations(
    internal_client: AsyncClient,
    approval_env: ApprovalEnv,
    session: AsyncSession,
) -> None:
    submitted = await submit_user_skill(internal_client, approval_env, key="e12-builder")
    builder_username = await seed_builder_account(session, approval_env)
    random_skill_id = str(uuid.uuid4())
    random_artifact_id = str(uuid.uuid4())

    async with authenticated_builder(approval_env, builder_username) as builder:
        _assert_forbidden(
            await approve(builder, submitted.skill_id, submitted.artifact_id),
            "审批他人待审包",
        )
        _assert_forbidden(
            await builder.get(_download_url(submitted.skill_id, submitted.artifact_id)),
            "下载他人包",
        )
        _assert_forbidden(
            await builder.delete(
                f"/api/v1/skills/{submitted.skill_id}",
                params={"unbind": "true", "confirm": "true"},
            ),
            "解绑并软删",
        )
        _assert_forbidden(
            await builder.post(
                "/api/v1/skills/import",
                files={
                    "file": (
                        "skill.zip",
                        demo_package(name="Builder System Skill"),
                        "application/zip",
                    )
                },
                data={"version": "1.0.0", "key": "e12-builder-system", "source": "SYSTEM"},
            ),
            "系统 Skill 上传",
        )

        # 不泄露存在性：管理员专属路由先校验角色，再查询资源；随机 ID 同样是 403。
        _assert_forbidden(
            await approve(builder, random_skill_id, random_artifact_id),
            "审批不存在的资源",
        )
        _assert_forbidden(
            await builder.get(_download_url(random_skill_id, random_artifact_id)),
            "下载不存在的资源",
        )
        _assert_forbidden(
            await builder.delete(
                f"/api/v1/skills/{random_skill_id}",
                params={"unbind": "true", "confirm": "true"},
            ),
            "解绑不存在的资源",
        )

    session.expire_all()
    skill = await session.get(Skill, uuid.UUID(submitted.skill_id))
    artifact = await session.get(SkillArtifact, uuid.UUID(submitted.artifact_id))
    assert skill is not None and skill.is_deleted is False
    assert artifact is not None and artifact.review_status == "PENDING"
    assert artifact.reviewed_by is None
    assert (
        await session.scalar(
            sa.select(sa.func.count())
            .select_from(AgentSkillBinding)
            .where(AgentSkillBinding.skill_id == skill.id)
        )
        == 0
    )
    assert (
        await session.scalar(
            sa.select(sa.func.count())
            .select_from(SkillUserGrant)
            .where(SkillUserGrant.skill_id == skill.id)
        )
        == 0
    )


async def test_e12_admin_can_complete_review_after_forbidden_attempts(
    internal_client: AsyncClient,
    admin_client: AsyncClient,
    approval_env: ApprovalEnv,
    session: AsyncSession,
) -> None:
    """403 不产生副作用：管理员仍可完成审批，且只绑定提交者与上传 Agent。"""
    submitted = await submit_user_skill(internal_client, approval_env, key="e12-admin")
    builder_username = await seed_builder_account(session, approval_env)

    async with authenticated_builder(approval_env, builder_username) as builder:
        _assert_forbidden(
            await approve(builder, submitted.skill_id, submitted.artifact_id),
            "审批他人待审包",
        )

    approved = await approve(admin_client, submitted.skill_id, submitted.artifact_id)
    assert approved.status_code == 200, approved.text
    assert approved.json()["data"]["review_status"] == "APPROVED"

    session.expire_all()
    artifact = await session.get(SkillArtifact, uuid.UUID(submitted.artifact_id))
    assert artifact is not None and artifact.review_status == "APPROVED"
    assert artifact.reviewed_by == approval_env.admin_id
