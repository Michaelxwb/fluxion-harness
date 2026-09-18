import uuid
from typing import Any

from httpx import AsyncClient
from muad_console_platform.infrastructure.db import get_session_factory
from sqlalchemy import text

from console_platform.conftest import TenantContext


def _headers(tenant: TenantContext) -> dict[str, str]:
    return {"X-Tenant-Id": tenant.tenant_id}


def _payload(code: str | None = None, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "user_code": code or f"user-{uuid.uuid4().hex[:8]}",
        "display_name": "User Alpha",
        "status": "ACTIVE",
        "metadata": {"dept": "engineering"},
    }
    body.update(overrides)
    return body


async def _seed_related_rows(client: AsyncClient, tenant: TenantContext, user_id: str) -> None:
    agent = await client.post(
        "/api/v1/agents",
        json={
            "key": f"agent-{uuid.uuid4().hex[:8]}",
            "name": "Count Agent",
            "instructions": "You are helpful.",
            "model_id": str(tenant.model_id),
            "runtime_config": {},
        },
        headers=_headers(tenant),
    )
    assert agent.status_code == 200
    agent_id = agent.json()["data"]["id"]
    session_factory = get_session_factory()
    async with session_factory() as session:
        await session.execute(
            text(
                "INSERT INTO control.project_platform "
                "(id, tenant_id, key, name, resolver_type, resolver_config_json, "
                "adapter_key, credential_mode) "
                "VALUES (:id, :tenant_id, :key, :name, 'STATIC', '{}'::jsonb, 'test-adapter', 'NONE')"
            ),
            {
                "id": uuid.uuid4(),
                "tenant_id": tenant.tenant_id,
                "key": f"platform-{uuid.uuid4().hex[:8]}",
                "name": "Count Platform",
            },
        )
        await session.execute(
            text(
                "INSERT INTO control.user_credential_ref "
                "(id, tenant_id, user_id, platform_id, credential_json) "
                "SELECT :id, :tenant_id, :user_id, id, '{\"token\": \"t\"}'::jsonb "
                "FROM control.project_platform WHERE tenant_id = :platform_tenant_id "
                "ORDER BY create_time DESC LIMIT 1"
            ),
            {
                "id": uuid.uuid4(),
                "tenant_id": tenant.tenant_id,
                "user_id": user_id,
                "platform_tenant_id": tenant.tenant_id,
            },
        )
        await session.execute(
            text(
                "INSERT INTO control.agent_access_grant (id, user_id, agent_id, granted_by) "
                "VALUES (:id, :user_id, :agent_id, :granted_by)"
            ),
            {
                "id": uuid.uuid4(),
                "tenant_id": tenant.tenant_id,
                "user_id": user_id,
                "agent_id": agent_id,
                "granted_by": uuid.uuid4(),
            },
        )
        bot_account_id = uuid.uuid4()
        await session.execute(
            text(
                "INSERT INTO control.bot_account "
                "(id, tenant_id, channel, name, bot_id, secret, agent_id) "
                "VALUES (:id, :tenant_id, 'WECOM', 'Count Bot', :bot_id, 'wecom-secret-bot', :agent_id)"
            ),
            {
                "id": bot_account_id,
                "tenant_id": tenant.tenant_id,
                "bot_id": f"bot-{uuid.uuid4().hex[:8]}",
                "agent_id": agent_id,
            },
        )
        await session.execute(
            text(
                "INSERT INTO control.channel_identity "
                "(id, tenant_id, channel, identity_key, external_user_id, bot_account_id, "
                "platform_user_id) VALUES (:id, :tenant_id, 'WECOM', :identity_key, "
                ":external_user_id, :bot_account_id, :user_id)"
            ),
            {
                "id": uuid.uuid4(),
                "tenant_id": tenant.tenant_id,
                "identity_key": f"WECOM:{uuid.uuid4().hex[:8]}",
                "external_user_id": uuid.uuid4().hex[:8],
                "bot_account_id": bot_account_id,
                "user_id": user_id,
            },
        )
        await session.execute(
            text(
                "INSERT INTO runtime.user_memory "
                "(id, tenant_id, user_id, memory_key, category, content_json, source_type) "
                "VALUES (:id, :tenant_id, :user_id, :memory_key, 'PREFERENCE', '{}'::jsonb, 'USER')"
            ),
            {
                "id": uuid.uuid4(),
                "tenant_id": tenant.tenant_id,
                "user_id": user_id,
                "memory_key": f"memory-{uuid.uuid4().hex[:8]}",
            },
        )
        await session.commit()


async def _cleanup_seeded(tenant_id: str) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        for statement in (
            "DELETE FROM runtime.user_memory WHERE tenant_id = :tenant_id",
            "DELETE FROM control.channel_identity WHERE tenant_id = :tenant_id",
            "DELETE FROM control.bot_account WHERE tenant_id = :tenant_id",
            "DELETE FROM control.user_credential_ref WHERE tenant_id = :tenant_id",
            "DELETE FROM control.project_platform WHERE tenant_id = :tenant_id",
            "DELETE FROM control.agent_access_grant WHERE user_id IN "
            "(SELECT id FROM control.platform_user WHERE tenant_id = :tenant_id)",
        ):
            await session.execute(text(statement), {"tenant_id": tenant_id})
        await session.commit()


async def test_create_list_detail_and_aggregate_counts(client: AsyncClient, tenant: TenantContext) -> None:
    created = await client.post("/api/v1/users", json=_payload("user-alpha"), headers=_headers(tenant))
    assert created.status_code == 200
    body = created.json()
    assert body["code"] == "0"
    user = body["data"]
    assert user["user_code"] == "user-alpha"
    assert user["status"] == "ACTIVE"
    assert user["metadata"] == {"dept": "engineering"}
    assert user["id"]
    assert user["create_time"]

    await _seed_related_rows(client, tenant, user["id"])

    try:
        await _assert_counts(client, tenant, user["id"], user["user_code"])
    finally:
        await _cleanup_seeded(tenant.tenant_id)

    updated = await client.put(
        f"/api/v1/users/{user['id']}",
        json={"display_name": "Renamed User", "status": "DISABLED"},
        headers=_headers(tenant),
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["display_name"] == "Renamed User"
    assert updated.json()["data"]["status"] == "DISABLED"


async def _assert_counts(client: AsyncClient, tenant: TenantContext, user_id: str, user_code: str) -> None:
    listing = await client.get(
        "/api/v1/users",
        params={"page": 1, "page_size": 20, "keyword": user_code},
        headers=_headers(tenant),
    )
    assert listing.status_code == 200
    page = listing.json()["data"]
    assert page["total"] == 1
    item = page["items"][0]
    assert item["agent_grant_count"] == 1
    assert item["credential_count"] == 1
    assert item["identity_count"] == 1
    assert item["memory_count"] == 1

    detail = await client.get(f"/api/v1/users/{user_id}", headers=_headers(tenant))
    assert detail.status_code == 200
    detail_data = detail.json()["data"]
    assert detail_data["agent_grant_count"] == 1
    assert detail_data["credential_count"] == 1
    assert detail_data["identity_count"] == 1
    assert detail_data["memory_count"] == 1
    assert detail_data["metadata"] == {"dept": "engineering"}


async def test_e03_duplicate_user_code_returns_conflict(client: AsyncClient, tenant: TenantContext) -> None:
    first = await client.post(
        "/api/v1/users", json=_payload("user-dup", display_name="First"), headers=_headers(tenant)
    )
    assert first.status_code == 200
    original_id = first.json()["data"]["id"]

    duplicate = await client.post(
        "/api/v1/users", json=_payload("user-dup", display_name="Second"), headers=_headers(tenant)
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "COMMON_CONFLICT"

    detail = await client.get(f"/api/v1/users/{original_id}", headers=_headers(tenant))
    assert detail.json()["data"]["display_name"] == "First"


async def test_e04_update_rejects_user_code(client: AsyncClient, tenant: TenantContext) -> None:
    created = await client.post("/api/v1/users", json=_payload("user-immutable"), headers=_headers(tenant))
    user_id = created.json()["data"]["id"]

    response = await client.put(
        f"/api/v1/users/{user_id}",
        json={"display_name": "New Name", "user_code": "changed-code"},
        headers=_headers(tenant),
    )
    assert response.status_code == 422
    assert response.json()["code"] == "COMMON_VALIDATION_ERROR"


async def test_list_keyword_and_status_filters(client: AsyncClient, tenant: TenantContext) -> None:
    await client.post(
        "/api/v1/users", json=_payload("filter-alpha", display_name="Alpha"), headers=_headers(tenant)
    )
    await client.post(
        "/api/v1/users",
        json=_payload("filter-beta", display_name="Beta", status="DISABLED"),
        headers=_headers(tenant),
    )

    by_keyword = await client.get(
        "/api/v1/users", params={"keyword": "filter-beta"}, headers=_headers(tenant)
    )
    assert by_keyword.json()["data"]["total"] == 1

    by_status = await client.get(
        "/api/v1/users", params={"status": "DISABLED"}, headers=_headers(tenant)
    )
    items = by_status.json()["data"]["items"]
    assert [item["user_code"] for item in items] == ["filter-beta"]
