"""Soft-delete semantics for Skills: guard bound Agents, hide from list, keep audit."""

import uuid

import sqlalchemy as sa
from httpx import AsyncClient
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.control import (
    ConfigAuditLog,
    Skill,
    SkillArtifact,
)

from console_skill.conftest import SkillContext, tenant_headers


async def _skill(skill_id: uuid.UUID) -> Skill | None:
    async with get_session_factory()() as session:
        return await session.get(Skill, skill_id)


async def _audit_rows(skill_id: uuid.UUID) -> list[ConfigAuditLog]:
    async with get_session_factory()() as session:
        result = await session.scalars(
            sa.select(ConfigAuditLog)
            .where(
                ConfigAuditLog.resource_type == "SKILL",
                ConfigAuditLog.resource_id == skill_id,
            )
            .order_by(ConfigAuditLog.create_time)
        )
        return list(result.all())


async def test_delete_unbound_skill_soft_deletes_and_hides_it(
    client: AsyncClient,
    skill_env: SkillContext,
) -> None:
    skill_id = skill_env.skill_unbound_id
    artifacts_before = await _artifact_ids(skill_id)

    response = await client.delete(
        f"/api/v1/skills/{skill_id}", headers=tenant_headers(skill_env)
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"] == {"id": str(skill_id), "is_deleted": True}

    stored = await _skill(skill_id)
    assert stored is not None and stored.is_deleted is True
    assert await _artifact_ids(skill_id) == artifacts_before, "删除 Skill 不物理删除版本记录"

    detail = await client.get(
        f"/api/v1/skills/{skill_id}", headers=tenant_headers(skill_env)
    )
    assert detail.status_code == 404
    assert detail.json()["code"] == "COMMON_NOT_FOUND"

    listing = await client.get(
        "/api/v1/skills",
        params={"keyword": "skill-unbound"},
        headers=tenant_headers(skill_env),
    )
    assert listing.json()["data"]["total"] == 0

    rows = await _audit_rows(skill_id)
    assert len(rows) == 1
    assert rows[0].action == "DELETE"
    assert rows[0].before_json is not None
    assert rows[0].before_json["key"] == "skill-unbound"
    assert rows[0].after_json is None
    assert rows[0].actor_user_id == skill_env.admin_id


async def test_delete_bound_skill_is_rejected_with_reference_count(
    client: AsyncClient,
    skill_env: SkillContext,
) -> None:
    skill_id = skill_env.skill_all_id

    response = await client.delete(
        f"/api/v1/skills/{skill_id}", headers=tenant_headers(skill_env)
    )
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "SKILL_IN_USE"
    assert body["data"] is None
    assert "skill-all" in body["msg"]
    assert "1" in body["msg"]

    stored = await _skill(skill_id)
    assert stored is not None and stored.is_deleted is False
    assert await _audit_rows(skill_id) == []


async def test_delete_other_tenant_skill_returns_not_found(
    client: AsyncClient,
    skill_env: SkillContext,
) -> None:
    response = await client.delete(
        f"/api/v1/skills/{skill_env.skill_other_tenant_id}",
        headers=tenant_headers(skill_env),
    )
    assert response.status_code == 404
    assert response.json()["code"] == "COMMON_NOT_FOUND"


async def _artifact_ids(skill_id: uuid.UUID) -> list[uuid.UUID]:
    async with get_session_factory()() as session:
        rows = await session.scalars(
            sa.select(SkillArtifact.id).where(SkillArtifact.skill_id == skill_id)
        )
        return list(rows.all())
