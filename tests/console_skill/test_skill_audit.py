import json

import sqlalchemy as sa
from httpx import AsyncClient
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.control import ConfigAuditLog

from console_skill.conftest import SkillContext, import_skill, tenant_headers
from console_skill.packages import demo_package


async def _audit_rows(context: SkillContext) -> list[ConfigAuditLog]:
    async with get_session_factory()() as session:
        result = await session.scalars(
            sa.select(ConfigAuditLog)
            .where(ConfigAuditLog.tenant_id == context.tenant_id)
            .order_by(ConfigAuditLog.create_time)
        )
        return list(result.all())


async def test_mutations_write_config_audit_rows(
    client: AsyncClient,
    skill_env: SkillContext,
) -> None:
    imported = await import_skill(
        client,
        skill_env,
        demo_package(name="Audited Skill"),
        version="1.0.0",
    )
    assert imported.status_code == 200
    skill_id = imported.json()["data"]["id"]

    updated = await client.put(
        f"/api/v1/skills/{skill_id}",
        json={"name": "Audited Skill v2", "enabled": False},
        headers=tenant_headers(skill_env),
    )
    assert updated.status_code == 200

    added = await client.post(
        f"/api/v1/skills/{skill_id}/artifacts",
        files={"file": ("skill.zip", demo_package(name="Audited Skill v2"), "application/zip")},
        data={"version": "1.1.0"},
        headers=tenant_headers(skill_env),
    )
    assert added.status_code == 200

    scope = await client.put(
        f"/api/v1/skills/{skill_id}/user-scope",
        json={"user_scope": "ALL"},
        headers=tenant_headers(skill_env),
    )
    assert scope.status_code == 200

    grant = await client.post(
        f"/api/v1/skills/{skill_id}/users/{skill_env.actor_user_id}",
        headers=tenant_headers(skill_env),
    )
    assert grant.status_code == 200

    binding = await client.post(
        f"/api/v1/agents/{skill_env.agent_id}/skills/{skill_id}",
        headers=tenant_headers(skill_env),
    )
    assert binding.status_code == 200

    revoked = await client.delete(
        f"/api/v1/skills/{skill_id}/users/{skill_env.actor_user_id}",
        headers=tenant_headers(skill_env),
    )
    assert revoked.status_code == 200

    unbound = await client.delete(
        f"/api/v1/agents/{skill_env.agent_id}/skills/{skill_id}",
        headers=tenant_headers(skill_env),
    )
    assert unbound.status_code == 200

    rows = await _audit_rows(skill_env)
    actions = {(row.resource_type, row.action) for row in rows}
    assert actions == {
        ("SKILL", "CREATE"),
        ("SKILL", "UPDATE"),
        ("SKILL_ARTIFACT", "CREATE"),
        ("SKILL_USER_GRANT", "CREATE"),
        ("SKILL_USER_GRANT", "DELETE"),
        ("AGENT_SKILL_BINDING", "CREATE"),
        ("AGENT_SKILL_BINDING", "DELETE"),
    }
    assert {row.actor_user_id for row in rows} == {skill_env.admin_id}
    assert all(row.trace_id for row in rows)
    assert all("password" not in json.dumps(row.before_json or {}) for row in rows)
    assert all("password" not in json.dumps(row.after_json or {}) for row in rows)

    skill_rows = [row for row in rows if row.resource_type == "SKILL"]
    assert {str(row.resource_id) for row in skill_rows} == {skill_id}
