import uuid
from typing import Any

from httpx import AsyncClient
from muad_console_platform.infrastructure.db import get_session_factory
from sqlalchemy import text

from console_platform.conftest import TenantContext
from console_platform.test_users_api import _cleanup_seeded, _headers, _payload


async def _create_user(client: AsyncClient, tenant: TenantContext) -> str:
    created = await client.post("/api/v1/users", json=_payload(), headers=_headers(tenant))
    assert created.status_code == 200
    return created.json()["data"]["id"]


async def _seed_detail(client: AsyncClient, tenant: TenantContext, user_id: str) -> dict[str, Any]:
    agent = await client.post(
        "/api/v1/agents",
        json={
            "key": f"agent-{uuid.uuid4().hex[:8]}",
            "name": "Detail Agent",
            "instructions": "You are helpful.",
            "model_id": str(tenant.model_id),
            "runtime_config": {},
        },
        headers=_headers(tenant),
    )
    assert agent.status_code == 200
    agent_data = agent.json()["data"]
    session_factory = get_session_factory()
    async with session_factory() as session:
        await session.execute(
            text(
                "INSERT INTO control.agent_access_grant (id, user_id, agent_id, granted_by) "
                "VALUES (:id, :user_id, :agent_id, :granted_by)"
            ),
            {
                "id": uuid.uuid4(),
                "user_id": user_id,
                "agent_id": agent_data["id"],
                "granted_by": uuid.uuid4(),
            },
        )
        for category, key in (("PREFERENCE", "pref"), ("WORK_STYLE", "style")):
            await session.execute(
                text(
                    "INSERT INTO runtime.user_memory "
                    "(id, tenant_id, user_id, memory_key, category, content_json, source_type) "
                    "VALUES (:id, :tenant_id, :user_id, :memory_key, :category, "
                    "CAST(:content AS jsonb), 'USER')"
                ),
                {
                    "id": uuid.uuid4(),
                    "tenant_id": tenant.tenant_id,
                    "user_id": user_id,
                    "memory_key": f"{key}-{uuid.uuid4().hex[:8]}",
                    "category": category,
                    "content": '{"note": "likes concise answers"}',
                },
            )
        await session.commit()
    return agent_data


async def test_user_agent_grants_endpoint(client: AsyncClient, tenant: TenantContext) -> None:
    user_id = await _create_user(client, tenant)
    agent = await _seed_detail(client, tenant, user_id)
    try:
        response = await client.get(f"/api/v1/users/{user_id}/agents", headers=_headers(tenant))
        assert response.status_code == 200
        page = response.json()["data"]
        assert page["total"] == 1
        item = page["items"][0]
        assert item["agent_id"] == agent["id"]
        assert item["agent_key"] == agent["key"]
        assert item["agent_name"] == "Detail Agent"
        assert item["enabled"] is True
        assert item["granted_at"]
        assert item["granted_by"]
    finally:
        await _cleanup_seeded(tenant.tenant_id)

    missing = await client.get(f"/api/v1/users/{uuid.uuid4()}/agents", headers=_headers(tenant))
    assert missing.status_code == 404
    assert missing.json()["code"] == "COMMON_NOT_FOUND"


async def test_user_memory_endpoint_with_category_filter(client: AsyncClient, tenant: TenantContext) -> None:
    user_id = await _create_user(client, tenant)
    await _seed_detail(client, tenant, user_id)
    try:
        response = await client.get(f"/api/v1/users/{user_id}/memory", headers=_headers(tenant))
        assert response.status_code == 200
        page = response.json()["data"]
        assert page["total"] == 2
        item = page["items"][0]
        assert item["category"] in ("PREFERENCE", "WORK_STYLE")
        assert item["content"] == {"note": "likes concise answers"}
        assert item["enabled"] is True
        assert item["version"] == 1
        assert item["source_type"] == "USER"

        filtered = await client.get(
            f"/api/v1/users/{user_id}/memory",
            params={"category": "WORK_STYLE"},
            headers=_headers(tenant),
        )
        assert filtered.json()["data"]["total"] == 1
        assert filtered.json()["data"]["items"][0]["category"] == "WORK_STYLE"
    finally:
        await _cleanup_seeded(tenant.tenant_id)
