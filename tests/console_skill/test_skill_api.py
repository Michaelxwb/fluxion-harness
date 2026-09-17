import uuid

from httpx import AsyncClient

from console_skill.conftest import SkillContext, import_skill, tenant_headers
from console_skill.packages import demo_package


async def test_list_skills_filters_and_paginates(
    client: AsyncClient,
    skill_env: SkillContext,
) -> None:
    page = (
        await client.get(
            "/api/v1/skills",
            params={"page": 1, "page_size": 2},
            headers=tenant_headers(skill_env),
        )
    ).json()["data"]
    assert page["page"] == 1
    assert page["page_size"] == 2
    assert page["total"] == 7
    assert len(page["items"]) == 2
    first = page["items"][0]
    assert first["current_artifact_id"]
    assert first["current_version"] == "1.0.0"
    assert first["execution_mode"] == "SYNC"
    assert "key" in first and "name" in first and "user_scope" in first

    last_page = (
        await client.get(
            "/api/v1/skills",
            params={"page": 4, "page_size": 2},
            headers=tenant_headers(skill_env),
        )
    ).json()["data"]
    assert len(last_page["items"]) == 1

    selected = (
        await client.get(
            "/api/v1/skills",
            params={"user_scope": "SELECTED"},
            headers=tenant_headers(skill_env),
        )
    ).json()["data"]
    assert selected["total"] == 3
    assert {item["key"] for item in selected["items"]} == {
        "skill-granted",
        "skill-ungranted",
        "skill-revoked",
    }

    all_scope = (
        await client.get(
            "/api/v1/skills",
            params={"user_scope": "ALL"},
            headers=tenant_headers(skill_env),
        )
    ).json()["data"]
    assert all_scope["total"] == 4

    imported = await import_skill(
        client,
        skill_env,
        demo_package(name="Async Filter Skill", execution="async"),
        version="1.0.0",
    )
    assert imported.status_code == 200

    async_only = (
        await client.get(
            "/api/v1/skills",
            params={"execution_mode": "ASYNC"},
            headers=tenant_headers(skill_env),
        )
    ).json()["data"]
    assert async_only["total"] == 1
    assert async_only["items"][0]["key"] == "async-filter-skill"
    assert async_only["items"][0]["execution_mode"] == "ASYNC"


async def test_get_skill_detail_includes_artifact_and_counts(
    client: AsyncClient,
    skill_env: SkillContext,
) -> None:
    response = await client.get(
        f"/api/v1/skills/{skill_env.skill_all_id}",
        headers=tenant_headers(skill_env),
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["key"] == "skill-all"
    assert data["agent_count"] == 1
    assert data["user_count"] == 0
    assert data["current_artifact"]["artifact_id"] == str(skill_env.artifact_all_id)
    assert data["current_artifact"]["version"] == "1.0.0"

    granted = (
        await client.get(
            f"/api/v1/skills/{skill_env.skill_selected_granted_id}",
            headers=tenant_headers(skill_env),
        )
    ).json()["data"]
    assert granted["user_count"] == 1
    assert granted["user_scope"] == "SELECTED"


async def test_update_skill_fields(client: AsyncClient, skill_env: SkillContext) -> None:
    response = await client.put(
        f"/api/v1/skills/{skill_env.skill_all_id}",
        json={
            "name": "Renamed Skill",
            "description": "Updated description.",
            "platform_label": "WECOM",
            "enabled": False,
        },
        headers=tenant_headers(skill_env),
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["name"] == "Renamed Skill"
    assert data["description"] == "Updated description."
    assert data["platform_label"] == "WECOM"
    assert data["enabled"] is False
    assert data["key"] == "skill-all"

    reloaded = (
        await client.get(
            f"/api/v1/skills/{skill_env.skill_all_id}",
            headers=tenant_headers(skill_env),
        )
    ).json()["data"]
    assert reloaded["name"] == "Renamed Skill"
    assert reloaded["enabled"] is False


async def test_unknown_skill_returns_not_found(
    client: AsyncClient,
    skill_env: SkillContext,
) -> None:
    response = await client.get(
        f"/api/v1/skills/{uuid.uuid4()}",
        headers=tenant_headers(skill_env),
    )
    assert response.status_code == 404
    assert response.json()["code"] == "COMMON_NOT_FOUND"


async def test_other_tenant_skill_returns_not_found(
    client: AsyncClient,
    skill_env: SkillContext,
) -> None:
    response = await client.get(
        f"/api/v1/skills/{skill_env.skill_other_tenant_id}",
        headers=tenant_headers(skill_env),
    )
    assert response.status_code == 404
    assert response.json()["code"] == "COMMON_NOT_FOUND"

    update = await client.put(
        f"/api/v1/skills/{skill_env.skill_other_tenant_id}",
        json={"name": "Cross Tenant"},
        headers=tenant_headers(skill_env),
    )
    assert update.status_code == 404
    assert update.json()["code"] == "COMMON_NOT_FOUND"
