import uuid

from httpx import AsyncClient

from console_skill.conftest import SkillContext, tenant_headers


async def test_user_scope_and_grants(client: AsyncClient, skill_env: SkillContext) -> None:
    scope = await client.put(
        f"/api/v1/skills/{skill_env.skill_all_id}/user-scope",
        json={"user_scope": "ALL"},
        headers=tenant_headers(skill_env),
    )
    assert scope.status_code == 200
    assert scope.json()["data"]["user_scope"] == "ALL"

    selected = await client.put(
        f"/api/v1/skills/{skill_env.skill_all_id}/user-scope",
        json={"user_scope": "SELECTED"},
        headers=tenant_headers(skill_env),
    )
    assert selected.status_code == 200
    assert selected.json()["data"]["user_scope"] == "SELECTED"

    empty = await client.get(
        f"/api/v1/skills/{skill_env.skill_all_id}/users",
        headers=tenant_headers(skill_env),
    )
    assert empty.status_code == 200
    assert empty.json()["data"]["items"] == []

    from muad_console_platform.infrastructure.db import get_session_factory
    from muad_console_platform.infrastructure.models.control import (
        AgentAccessGrant,
        AgentSkillBinding,
    )
    from muad_console_platform.infrastructure.models.control import (
        SkillUserGrant as SkillUserGrantModel,
    )
    from sqlalchemy import func, select

    async with get_session_factory()() as session:
        access_before = (
            await session.execute(
                select(func.count()).select_from(AgentAccessGrant).where(
                    AgentAccessGrant.user_id == skill_env.actor_user_id
                )
            )
        ).scalar_one()
        bindings_before = (
            await session.execute(
                select(func.count()).select_from(AgentSkillBinding).where(
                    AgentSkillBinding.skill_id == skill_env.skill_all_id
                )
            )
        ).scalar_one()

    granted = await client.post(
        f"/api/v1/skills/{skill_env.skill_all_id}/users/{skill_env.actor_user_id}",
        headers=tenant_headers(skill_env),
    )
    assert granted.status_code == 200
    grant = granted.json()["data"]
    assert grant["user_id"] == str(skill_env.actor_user_id)
    assert grant["granted_by"] == str(skill_env.admin_id)
    assert grant["user_code"]
    assert grant["display_name"] == "Actor"

    # [S-02] 只创建 SkillUserGrant，不产生新的 AgentAccessGrant/AgentSkillBinding
    async with get_session_factory()() as session:
        user_grants = (
            await session.execute(
                select(SkillUserGrantModel).where(
                    SkillUserGrantModel.skill_id == skill_env.skill_all_id,
                    SkillUserGrantModel.user_id == skill_env.actor_user_id,
                )
            )
        ).scalars().all()
        assert len(user_grants) == 1
        assert not hasattr(user_grants[0], "expires_at")
        access_after = (
            await session.execute(
                select(func.count()).select_from(AgentAccessGrant).where(
                    AgentAccessGrant.user_id == skill_env.actor_user_id
                )
            )
        ).scalar_one()
        bindings_after = (
            await session.execute(
                select(func.count()).select_from(AgentSkillBinding).where(
                    AgentSkillBinding.skill_id == skill_env.skill_all_id
                )
            )
        ).scalar_one()
    assert access_after == access_before
    assert bindings_after == bindings_before

    # 重复授权 → 幂等：返回既有记录、不新增行、不重复写审计（与用户↔Agent 授权口径一致）
    from sqlalchemy import text

    async def _grant_audits() -> int:
        async with get_session_factory()() as session:
            return int(
                await session.scalar(
                    text(
                        "SELECT count(*) FROM control.config_audit_log "
                        "WHERE resource_type = 'SKILL_USER_GRANT' AND tenant_id = :tid"
                    ),
                    {"tid": skill_env.tenant_id},
                )
                or 0
            )

    audits_before = await _grant_audits()
    repeated = await client.post(
        f"/api/v1/skills/{skill_env.skill_all_id}/users/{skill_env.actor_user_id}",
        headers=tenant_headers(skill_env),
    )
    assert repeated.status_code == 200
    assert str(repeated.json()["data"]["user_id"]) == str(skill_env.actor_user_id)
    assert await _grant_audits() == audits_before, "幂等授权不得重复写审计"

    listed = (
        await client.get(
            f"/api/v1/skills/{skill_env.skill_all_id}/users",
            headers=tenant_headers(skill_env),
        )
    ).json()["data"]
    assert listed["total"] == 1
    assert [item["user_id"] for item in listed["items"]] == [str(skill_env.actor_user_id)]

    removed = await client.delete(
        f"/api/v1/skills/{skill_env.skill_all_id}/users/{skill_env.actor_user_id}",
        headers=tenant_headers(skill_env),
    )
    assert removed.status_code == 200
    assert removed.json()["data"] == {"deleted": True}

    after_remove = (
        await client.get(
            f"/api/v1/skills/{skill_env.skill_all_id}/users",
            headers=tenant_headers(skill_env),
        )
    ).json()["data"]
    assert after_remove["items"] == []

    again = await client.delete(
        f"/api/v1/skills/{skill_env.skill_all_id}/users/{skill_env.actor_user_id}",
        headers=tenant_headers(skill_env),
    )
    assert again.status_code == 404
    assert again.json()["code"] == "COMMON_NOT_FOUND"

    regranted = await client.post(
        f"/api/v1/skills/{skill_env.skill_all_id}/users/{skill_env.actor_user_id}",
        headers=tenant_headers(skill_env),
    )
    assert regranted.status_code == 200
    assert regranted.json()["data"]["user_id"] == str(skill_env.actor_user_id)


async def test_grant_unknown_and_cross_tenant_user_returns_not_found(
    client: AsyncClient,
    skill_env: SkillContext,
) -> None:
    unknown = await client.post(
        f"/api/v1/skills/{skill_env.skill_all_id}/users/{uuid.uuid4()}",
        headers=tenant_headers(skill_env),
    )
    assert unknown.status_code == 404
    assert unknown.json()["code"] == "COMMON_NOT_FOUND"

    cross_tenant = await client.post(
        f"/api/v1/skills/{skill_env.skill_all_id}/users/{skill_env.other_tenant_user_id}",
        headers=tenant_headers(skill_env),
    )
    assert cross_tenant.status_code == 404
    assert cross_tenant.json()["code"] == "COMMON_NOT_FOUND"

    other_skill = await client.post(
        f"/api/v1/skills/{skill_env.skill_other_tenant_id}/users/{skill_env.actor_user_id}",
        headers=tenant_headers(skill_env),
    )
    assert other_skill.status_code == 404
    assert other_skill.json()["code"] == "COMMON_NOT_FOUND"

    invalid_scope = await client.put(
        f"/api/v1/skills/{skill_env.skill_all_id}/user-scope",
        json={"user_scope": "EVERYONE"},
        headers=tenant_headers(skill_env),
    )
    assert invalid_scope.status_code == 422
    assert invalid_scope.json()["code"] == "COMMON_VALIDATION_ERROR"
