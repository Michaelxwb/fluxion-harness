from httpx import ASGITransport, AsyncClient
from muad_console_platform.main import app

from console_skill.conftest import (
    ADMIN_PASSWORD,
    SkillContext,
    import_skill,
    tenant_headers,
)
from console_skill.packages import demo_package


async def test_builder_can_import_update_artifact_and_bind(
    builder_client: AsyncClient,
    skill_env: SkillContext,
) -> None:
    imported = await import_skill(
        builder_client,
        skill_env,
        demo_package(name="Builder Skill"),
        version="1.0.0",
    )
    assert imported.status_code == 200
    skill_id = imported.json()["data"]["id"]

    updated = await builder_client.put(
        f"/api/v1/skills/{skill_id}",
        json={"description": "Builder updated."},
        headers=tenant_headers(skill_env),
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["description"] == "Builder updated."

    artifact = await builder_client.post(
        f"/api/v1/skills/{skill_id}/artifacts",
        files={
            "file": (
                "skill.zip",
                demo_package(
                    name="Builder Skill",
                    extra_files={"scripts/run.py": "print('builder')\n"},
                ),
                "application/zip",
            )
        },
        data={"version": "1.1.0"},
        headers=tenant_headers(skill_env),
    )
    assert artifact.status_code == 200

    binding = await builder_client.post(
        f"/api/v1/agents/{skill_env.agent_id}/skills/{skill_id}",
        headers=tenant_headers(skill_env),
    )
    assert binding.status_code == 200

    unbound = await builder_client.delete(
        f"/api/v1/agents/{skill_env.agent_id}/skills/{skill_id}",
        headers=tenant_headers(skill_env),
    )
    assert unbound.status_code == 200


async def test_builder_cannot_manage_user_scope_or_grants(
    builder_client: AsyncClient,
    skill_env: SkillContext,
) -> None:
    forbidden_scope = await builder_client.put(
        f"/api/v1/skills/{skill_env.skill_all_id}/user-scope",
        json={"user_scope": "ALL"},
        headers=tenant_headers(skill_env),
    )
    assert forbidden_scope.status_code == 403
    assert forbidden_scope.json()["code"] == "FORBIDDEN"

    forbidden_grant = await builder_client.post(
        f"/api/v1/skills/{skill_env.skill_all_id}/users/{skill_env.actor_user_id}",
        headers=tenant_headers(skill_env),
    )
    assert forbidden_grant.status_code == 403
    assert forbidden_grant.json()["code"] == "FORBIDDEN"

    forbidden_revoke = await builder_client.delete(
        f"/api/v1/skills/{skill_env.skill_selected_granted_id}/users/{skill_env.actor_user_id}",
        headers=tenant_headers(skill_env),
    )
    assert forbidden_revoke.status_code == 403
    assert forbidden_revoke.json()["code"] == "FORBIDDEN"


async def test_admin_can_manage_user_scope_and_grants(
    client: AsyncClient,
    skill_env: SkillContext,
) -> None:
    scope = await client.put(
        f"/api/v1/skills/{skill_env.skill_all_id}/user-scope",
        json={"user_scope": "ALL"},
        headers=tenant_headers(skill_env),
    )
    assert scope.status_code == 200

    grant = await client.post(
        f"/api/v1/skills/{skill_env.skill_all_id}/users/{skill_env.actor_user_id}",
        headers=tenant_headers(skill_env),
    )
    assert grant.status_code == 200


async def test_skill_mutations_require_csrf_and_session(skill_env: SkillContext) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as anon:
        no_session = await anon.put(
            f"/api/v1/skills/{skill_env.skill_all_id}/user-scope",
            json={"user_scope": "ALL"},
            headers=tenant_headers(skill_env),
        )
    assert no_session.status_code == 401
    assert no_session.json()["code"] == "UNAUTHORIZED"

    async with AsyncClient(transport=transport, base_url="http://test") as login_client:
        login = await login_client.post(
            "/api/v1/auth/login",
            json={"username": skill_env.admin_username, "password": ADMIN_PASSWORD},
            headers=tenant_headers(skill_env),
        )
        assert login.status_code == 200
        missing_csrf = await login_client.put(
            f"/api/v1/skills/{skill_env.skill_all_id}/user-scope",
            json={"user_scope": "ALL"},
            headers=tenant_headers(skill_env),
        )
        assert missing_csrf.status_code == 403
        assert missing_csrf.json()["code"] == "FORBIDDEN"
