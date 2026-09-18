import uuid
from typing import Any

from httpx import AsyncClient
from muad_console_platform.application.grant_service import GrantService
from muad_console_platform.application.memory_service import MemoryService
from muad_console_platform.infrastructure.db import get_session_factory
from sqlalchemy import text

from console_platform.conftest import TenantContext
from console_platform.test_users_api import _cleanup_seeded, _headers, _payload


async def _create_user(client: AsyncClient, tenant: TenantContext) -> str:
    created = await client.post("/api/v1/users", json=_payload(), headers=_headers(tenant))
    return created.json()["data"]["id"]


async def _create_agent(client: AsyncClient, tenant: TenantContext) -> dict[str, Any]:
    agent = await client.post(
        "/api/v1/agents",
        json={
            "key": f"agent-{uuid.uuid4().hex[:8]}",
            "name": "Side Relation Agent",
            "instructions": "You are helpful.",
            "model_id": str(tenant.model_id),
            "runtime_config": {},
        },
        headers=_headers(tenant),
    )
    return agent.json()["data"]


async def _seed_memory(tenant: TenantContext, user_id: str, count: int) -> list[str]:
    session_factory = get_session_factory()
    memory_ids: list[str] = []
    async with session_factory() as session:
        for index in range(count):
            memory_id = uuid.uuid4()
            memory_ids.append(str(memory_id))
            await session.execute(
                text(
                    "INSERT INTO runtime.user_memory "
                    "(id, tenant_id, user_id, memory_key, category, content_json, source_type) "
                    "VALUES (:id, :tenant_id, :user_id, :memory_key, 'PREFERENCE', '{}'::jsonb, 'USER')"
                ),
                {
                    "id": memory_id,
                    "tenant_id": tenant.tenant_id,
                    "user_id": user_id,
                    "memory_key": f"memory-{index}-{uuid.uuid4().hex[:6]}",
                },
            )
        await session.commit()
    return memory_ids


async def test_s04_grant_and_revoke_share_application_service(
    client: AsyncClient, tenant: TenantContext
) -> None:
    user_id = await _create_user(client, tenant)
    agent = await _create_agent(client, tenant)
    try:
        granted = await client.post(
            f"/api/v1/users/{user_id}/agents/{agent['id']}", headers=_headers(tenant)
        )
        assert granted.status_code == 200
        grant = granted.json()["data"]
        assert grant["user_id"] == user_id
        assert grant["agent_id"] == agent["id"]
        assert grant["granted_at"]
        assert grant["granted_by"]

        session_factory = get_session_factory()
        async with session_factory() as session:
            items, total = await GrantService(session).list_by_user(
                tenant.tenant_id, uuid.UUID(user_id), 1, 20
            )
        assert total == 1
        assert str(items[0]["agent_id"]) == agent["id"]

        listing = await client.get(f"/api/v1/users/{user_id}/agents", headers=_headers(tenant))
        assert listing.json()["data"]["total"] == 1

        revoked = await client.delete(
            f"/api/v1/users/{user_id}/agents/{agent['id']}", headers=_headers(tenant)
        )
        assert revoked.status_code == 200
        assert revoked.json()["data"]["revoked"] is True
        empty = await client.get(f"/api/v1/users/{user_id}/agents", headers=_headers(tenant))
        assert empty.json()["data"]["total"] == 0

        regranted = await client.post(
            f"/api/v1/users/{user_id}/agents/{agent['id']}", headers=_headers(tenant)
        )
        assert regranted.status_code == 200
        again = await client.get(f"/api/v1/users/{user_id}/agents", headers=_headers(tenant))
        assert again.json()["data"]["total"] == 1

        async with session_factory() as session:
            audit_count = await session.scalar(
                text(
                    "SELECT count(*) FROM control.config_audit_log "
                    "WHERE resource_id = :rid AND resource_type = 'AGENT_ACCESS_GRANT'"
                ),
                {"rid": uuid.UUID(agent["id"])},
            )
        assert audit_count == 3
    finally:
        await _cleanup_seeded(tenant.tenant_id)


async def test_s04_unknown_agent_or_user_returns_not_found(
    client: AsyncClient, tenant: TenantContext
) -> None:
    user_id = await _create_user(client, tenant)
    unknown_agent = await client.post(
        f"/api/v1/users/{user_id}/agents/{uuid.uuid4()}", headers=_headers(tenant)
    )
    assert unknown_agent.status_code == 404
    assert unknown_agent.json()["code"] == "COMMON_NOT_FOUND"

    unknown_user = await client.post(
        f"/api/v1/users/{uuid.uuid4()}/agents/{uuid.uuid4()}", headers=_headers(tenant)
    )
    assert unknown_user.status_code == 404

    revoke_missing = await client.delete(
        f"/api/v1/users/{user_id}/agents/{uuid.uuid4()}", headers=_headers(tenant)
    )
    assert revoke_missing.status_code == 404

    try:
        pass
    finally:
        await _cleanup_seeded(tenant.tenant_id)


async def test_s05_memory_delete_and_clear_share_application_service(
    client: AsyncClient, tenant: TenantContext
) -> None:
    user_id = await _create_user(client, tenant)
    memory_ids = await _seed_memory(tenant, user_id, 2)
    try:
        session_factory = get_session_factory()
        async with session_factory() as session:
            _, total = await MemoryService(session).list_by_user(
                tenant.tenant_id, uuid.UUID(user_id), 1, 20, None
            )
        assert total == 2

        removed = await client.delete(
            f"/api/v1/users/{user_id}/memory/{memory_ids[0]}", headers=_headers(tenant)
        )
        assert removed.status_code == 200
        assert removed.json()["data"]["deleted"] is True

        repeat = await client.delete(
            f"/api/v1/users/{user_id}/memory/{memory_ids[0]}", headers=_headers(tenant)
        )
        assert repeat.status_code == 404

        remaining = await client.get(f"/api/v1/users/{user_id}/memory", headers=_headers(tenant))
        assert remaining.json()["data"]["total"] == 1

        cleared = await client.delete(f"/api/v1/users/{user_id}/memory", headers=_headers(tenant))
        assert cleared.status_code == 200
        assert cleared.json()["data"]["deleted_count"] == 1

        empty = await client.get(f"/api/v1/users/{user_id}/memory", headers=_headers(tenant))
        assert empty.json()["data"]["total"] == 0

        cleared_again = await client.delete(f"/api/v1/users/{user_id}/memory", headers=_headers(tenant))
        assert cleared_again.json()["data"]["deleted_count"] == 0

        async with session_factory() as session:
            audit_count = await session.scalar(
                text(
                    "SELECT count(*) FROM control.config_audit_log "
                    "WHERE resource_type = 'USER_MEMORY' AND resource_id = ANY(:ids)"
                ),
                {"ids": [uuid.UUID(item) for item in memory_ids]},
            )
        assert audit_count == 2
    finally:
        await _cleanup_seeded(tenant.tenant_id)
