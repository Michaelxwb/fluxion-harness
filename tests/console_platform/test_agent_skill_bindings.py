"""[S-02][E-03][RULE-rel-001] Agent-Skill 绑定契约（真实 HTTP + 真实 PostgreSQL + resolve）。"""

from __future__ import annotations

import uuid

from httpx import AsyncClient

from console_platform.conftest import TenantContext


def _headers(tenant: TenantContext) -> dict[str, str]:
    return {"X-Tenant-Id": tenant.tenant_id}


async def _make_skill(client: AsyncClient, tenant: TenantContext) -> str:
    """导入一个最小合法 Skill 包，返回 skill_id。"""
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "SKILL.md",
            "---\nname: Bind Skill\ndescription: binding test.\n---\n\n# Bind\n",
        )
        archive.writestr("scripts/run.py", "print(1)\n")
    response = await client.post(
        "/api/v1/skills/import",
        files={"file": ("skill.zip", buffer.getvalue(), "application/zip")},
        data={"version": "1.0.0", "key": f"bind-{uuid.uuid4().hex[:8]}"},
        headers=_headers(tenant),
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["id"]


async def _make_agent(client: AsyncClient, tenant: TenantContext) -> str:
    created = await client.post(
        "/api/v1/agents",
        json={
            "key": f"bind-agent-{uuid.uuid4().hex[:8]}",
            "name": "Bind Agent",
            "instructions": "inst",
            "model_id": str(tenant.model_id),
        },
        headers=_headers(tenant),
    )
    assert created.status_code == 200, created.text
    return created.json()["data"]["id"]


async def test_s02_bind_skill_immediate_and_paginated_envelope(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """[S-02] 绑定立即写入（单关系事务、无全局保存）；列表分页封套 + 契约字段名。"""
    agent_id = await _make_agent(client, tenant)
    skill_id = await _make_skill(client, tenant)

    bound = await client.post(
        f"/api/v1/agents/{agent_id}/skills/{skill_id}",
        json={"sort_order": 3},
        headers=_headers(tenant),
    )
    assert bound.status_code == 200, bound.text
    assert bound.json()["data"]["sort_order"] == 3

    listed = await client.get(
        f"/api/v1/agents/{agent_id}/skills?page=1&page_size=10",
        headers=_headers(tenant),
    )
    assert listed.status_code == 200
    page = listed.json()["data"]
    for field in ("items", "page", "page_size", "total"):
        assert field in page
    assert page["total"] == 1
    item = page["items"][0]
    for field in (
        "skill_id",
        "key",
        "name",
        "user_scope",
        "enabled",
        "current_artifact_version",
        "sort_order",
        "create_time",
    ):
        assert field in item, f"缺字段 {field}"
    assert item["current_artifact_version"] == "1.0.0"


async def test_s02_resolve_reflects_binding_immediately(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """[S-02 延伸] 绑定后 resolve-definition 立即可见（ALL scope Skill）。"""
    from muad_console_platform.infrastructure.db import get_session_factory
    from muad_console_platform.infrastructure.models.control import PlatformUser

    agent_id = await _make_agent(client, tenant)
    # 用种子 ALL scope 的 skill 不易得，改为导入 skill 后把 user_scope 置 ALL
    skill_id = await _make_skill(client, tenant)
    async with get_session_factory()() as session:
        from muad_console_platform.infrastructure.models.control import Skill
        from sqlalchemy import update

        await session.execute(
            update(Skill).where(Skill.id == uuid.UUID(skill_id)).values(user_scope="ALL")
        )
        user = PlatformUser(
            tenant_id=tenant.tenant_id,
            user_code=f"u-{uuid.uuid4()}",
            display_name="resolver",
        )
        session.add(user)
        await session.flush()
        user_id = user.id
        from muad_console_platform.infrastructure.models.control import AgentAccessGrant

        session.add(
            AgentAccessGrant(user_id=user_id, agent_id=uuid.UUID(agent_id), granted_by=user_id)
        )
        await session.commit()

    bind = await client.post(
        f"/api/v1/agents/{agent_id}/skills/{skill_id}", headers=_headers(tenant)
    )
    assert bind.status_code == 200

    resolved = await client.post(
        "/internal/runtime/resolve-definition",
        json={"agent_id": agent_id, "actor_user_id": str(user_id), "channel": "WECOM"},
        headers=_headers(tenant),
    )
    assert resolved.status_code == 200, resolved.text
    keys = {skill["key"] for skill in resolved.json()["data"]["skills"]}
    assert any(key.startswith("bind-") for key in keys)

    async with get_session_factory()() as session:
        from muad_console_platform.infrastructure.models.control import AgentAccessGrant

        await session.execute(
            AgentAccessGrant.__table__.delete().where(AgentAccessGrant.user_id == user_id)
        )
        await session.execute(
            PlatformUser.__table__.delete().where(PlatformUser.id == user_id)
        )
        await session.commit()


async def test_e03_bind_disabled_skill_allowed_but_filtered(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """[E-03] 禁用 Skill 可绑定（行保留 enabled=false）；不存在/软删 → COMMON_NOT_FOUND。"""
    agent_id = await _make_agent(client, tenant)
    skill_id = await _make_skill(client, tenant)

    from muad_console_platform.infrastructure.db import get_session_factory
    from muad_console_platform.infrastructure.models.control import Skill
    from sqlalchemy import update

    async with get_session_factory()() as session:
        await session.execute(
            update(Skill).where(Skill.id == uuid.UUID(skill_id)).values(enabled=False)
        )
        await session.commit()

    bound = await client.post(
        f"/api/v1/agents/{agent_id}/skills/{skill_id}", headers=_headers(tenant)
    )
    assert bound.status_code == 200
    assert bound.json()["data"]["enabled"] is False

    listed = (
        await client.get(f"/api/v1/agents/{agent_id}/skills", headers=_headers(tenant))
    ).json()["data"]
    assert listed["total"] == 1  # 禁用行保留供管理侧解绑

    missing = await client.post(
        f"/api/v1/agents/{agent_id}/skills/{uuid.uuid4()}", headers=_headers(tenant)
    )
    assert missing.status_code == 404
    assert missing.json()["code"] == "COMMON_NOT_FOUND"


async def test_rel_001_unbind_idempotent_and_restore_updates_sort_order(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """[RULE-rel-001] 解除幂等返回 {agent_id,skill_id,is_deleted:true}；
    再绑定恢复同一逻辑关系并更新 sort_order。"""
    agent_id = await _make_agent(client, tenant)
    skill_id = await _make_skill(client, tenant)

    await client.post(
        f"/api/v1/agents/{agent_id}/skills/{skill_id}",
        json={"sort_order": 1},
        headers=_headers(tenant),
    )
    unbound = await client.delete(
        f"/api/v1/agents/{agent_id}/skills/{skill_id}", headers=_headers(tenant)
    )
    assert unbound.status_code == 200
    body = unbound.json()["data"]
    assert body == {"agent_id": agent_id, "skill_id": skill_id, "is_deleted": True}

    # 幂等：再次解除仍成功
    again = await client.delete(
        f"/api/v1/agents/{agent_id}/skills/{skill_id}", headers=_headers(tenant)
    )
    assert again.status_code == 200
    assert again.json()["data"]["is_deleted"] is True

    # 再绑定：恢复同一逻辑关系并应用新 sort_order
    restored = await client.post(
        f"/api/v1/agents/{agent_id}/skills/{skill_id}",
        json={"sort_order": 7},
        headers=_headers(tenant),
    )
    assert restored.status_code == 200
    assert restored.json()["data"]["sort_order"] == 7

    listed = (
        await client.get(f"/api/v1/agents/{agent_id}/skills", headers=_headers(tenant))
    ).json()["data"]
    assert listed["total"] == 1
