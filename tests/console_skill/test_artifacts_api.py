from httpx import AsyncClient

from console_skill.conftest import SkillContext, import_skill, tenant_headers
from console_skill.packages import demo_package


async def test_add_artifact_flips_current_artifact(
    client: AsyncClient,
    skill_env: SkillContext,
) -> None:
    imported = await import_skill(
        client,
        skill_env,
        demo_package(name="Versioned Skill"),
        version="1.0.0",
    )
    assert imported.status_code == 200
    skill = imported.json()["data"]
    skill_id = skill["id"]
    first_artifact_id = skill["current_artifact_id"]

    second_package = demo_package(
        name="Versioned Skill",
        extra_files={"scripts/run.py": "print('v2')\n"},
    )
    second = await client.post(
        f"/api/v1/skills/{skill_id}/artifacts",
        files={"file": ("skill.zip", second_package, "application/zip")},
        data={"version": "1.1.0"},
        headers=tenant_headers(skill_env),
    )
    assert second.status_code == 200
    second_artifact = second.json()["data"]
    assert second_artifact["id"] != first_artifact_id
    assert second_artifact["version"] == "1.1.0"

    detail = (
        await client.get(f"/api/v1/skills/{skill_id}", headers=tenant_headers(skill_env))
    ).json()["data"]
    assert detail["current_artifact_id"] == second_artifact["id"]
    assert detail["current_version"] == "1.1.0"

    listing = (
        await client.get(
            f"/api/v1/skills/{skill_id}/artifacts",
            headers=tenant_headers(skill_env),
        )
    ).json()["data"]
    assert listing["total"] == 2
    assert listing["items"][0]["artifact_id"] == second_artifact["id"]

    fetched = await client.get(
        f"/api/v1/skills/{skill_id}/artifacts/{second_artifact['id']}",
        headers=tenant_headers(skill_env),
    )
    assert fetched.status_code == 200
    data = fetched.json()["data"]
    assert data["artifact_id"] == second_artifact["id"]
    assert data["skill_id"] == skill_id
    assert data["validation_status"] == "READY"
    assert data["validation_message"] is None
    assert data["instructions"].startswith("# Versioned Skill")
    assert "Follow the instructions." in data["instructions"]
    assert data["instructions"] == detail["current_artifact"]["instructions"]
    assert data["manifest"]["file_count"] == 3

    old = await client.get(
        f"/api/v1/skills/{skill_id}/artifacts/{first_artifact_id}",
        headers=tenant_headers(skill_env),
    )
    assert old.status_code == 200
    assert old.json()["data"]["version"] == "1.0.0"


async def test_add_artifact_rejects_duplicate_version_and_checksum(
    client: AsyncClient,
    skill_env: SkillContext,
) -> None:
    package = demo_package(name="Duplicate Skill")
    imported = await import_skill(client, skill_env, package, version="1.0.0")
    skill_id = imported.json()["data"]["id"]

    duplicate_version = await client.post(
        f"/api/v1/skills/{skill_id}/artifacts",
        files={"file": ("skill.zip", package, "application/zip")},
        data={"version": "1.0.0"},
        headers=tenant_headers(skill_env),
    )
    assert duplicate_version.status_code == 409
    assert duplicate_version.json()["code"] == "SKILL_VERSION_EXISTS"

    duplicate_checksum = await client.post(
        f"/api/v1/skills/{skill_id}/artifacts",
        files={"file": ("skill.zip", package, "application/zip")},
        data={"version": "1.2.0"},
        headers=tenant_headers(skill_env),
    )
    assert duplicate_checksum.status_code == 409
    assert duplicate_checksum.json()["code"] == "SKILL_VERSION_EXISTS"


async def test_artifact_routes_enforce_tenant_scope(
    client: AsyncClient,
    skill_env: SkillContext,
) -> None:
    listing = await client.get(
        f"/api/v1/skills/{skill_env.skill_other_tenant_id}/artifacts",
        headers=tenant_headers(skill_env),
    )
    assert listing.status_code == 404
    assert listing.json()["code"] == "COMMON_NOT_FOUND"

    detail = await client.get(
        f"/api/v1/skills/{skill_env.skill_all_id}/artifacts/{skill_env.artifact_all_id}",
        headers=tenant_headers(skill_env),
    )
    assert detail.status_code == 200

    missing = await client.get(
        f"/api/v1/skills/{skill_env.skill_all_id}/artifacts/{skill_env.skill_unbound_id}",
        headers=tenant_headers(skill_env),
    )
    assert missing.status_code == 404
    assert missing.json()["code"] == "COMMON_NOT_FOUND"
