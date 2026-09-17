from typing import Any, cast

from httpx import AsyncClient

from console_skill.conftest import ARTIFACT_CHECKSUM, SkillContext, tenant_headers

RESOLVE_URL = "/internal/runtime/resolve-definition"


def _payload(
    context: SkillContext,
    *,
    actor_user_id: str | None = None,
) -> dict[str, str]:
    return {
        "agent_id": str(context.agent_id),
        "actor_user_id": actor_user_id or str(context.actor_user_id),
        "channel": "WECOM",
    }


async def _resolve(client: AsyncClient, context: SkillContext, **kwargs: Any) -> dict[str, Any]:
    response = await client.post(
        RESOLVE_URL,
        json=_payload(context, **kwargs),
        headers=tenant_headers(context),
    )
    assert response.status_code == 200
    return cast(dict[str, Any], response.json()["data"])


async def test_resolve_returns_effective_skills(
    client: AsyncClient,
    skill_env: SkillContext,
) -> None:
    data = await _resolve(client, skill_env)
    skills = data["skills"]
    assert {skill["key"] for skill in skills} == {"skill-all", "skill-granted"}

    entry = next(skill for skill in skills if skill["key"] == "skill-all")
    assert entry == {
        "skill_id": str(skill_env.skill_all_id),
        "artifact_id": str(skill_env.artifact_all_id),
        "key": "skill-all",
        "name": "Skill skill-all",
        "description": "Seeded skill.",
        "version": "1.0.0",
        "checksum": ARTIFACT_CHECKSUM,
        "storage_key": skill_env.artifact_all_storage_key,
        "execution_mode": "SYNC",
        "frontmatter": {"name": "Skill skill-all", "description": "Seeded skill."},
    }
    assert data["mcp_servers"] == []


async def test_resolve_excludes_selected_without_grant(
    client: AsyncClient,
    skill_env: SkillContext,
) -> None:
    data = await _resolve(client, skill_env)
    keys = {skill["key"] for skill in data["skills"]}
    assert "skill-ungranted" not in keys


async def test_resolve_excludes_selected_for_ungranted_user(
    client: AsyncClient,
    skill_env: SkillContext,
) -> None:
    data = await _resolve(client, skill_env, actor_user_id=str(skill_env.other_user_id))
    assert {skill["key"] for skill in data["skills"]} == {"skill-all"}


async def test_resolve_excludes_disabled_skill(
    client: AsyncClient,
    skill_env: SkillContext,
) -> None:
    keys = {skill["key"] for skill in (await _resolve(client, skill_env))["skills"]}
    assert "skill-disabled" not in keys


async def test_resolve_excludes_unbound_skill(
    client: AsyncClient,
    skill_env: SkillContext,
) -> None:
    keys = {skill["key"] for skill in (await _resolve(client, skill_env))["skills"]}
    assert "skill-unbound" not in keys


async def test_resolve_excludes_soft_deleted_grant(
    client: AsyncClient,
    skill_env: SkillContext,
) -> None:
    keys = {skill["key"] for skill in (await _resolve(client, skill_env))["skills"]}
    assert "skill-revoked" not in keys


async def test_resolve_excludes_soft_deleted_binding(
    client: AsyncClient,
    skill_env: SkillContext,
) -> None:
    keys = {skill["key"] for skill in (await _resolve(client, skill_env))["skills"]}
    assert "skill-binding-removed" not in keys


async def test_resolve_excludes_other_tenant_skill(
    client: AsyncClient,
    skill_env: SkillContext,
) -> None:
    keys = {skill["key"] for skill in (await _resolve(client, skill_env))["skills"]}
    assert "skill-other-tenant" not in keys
