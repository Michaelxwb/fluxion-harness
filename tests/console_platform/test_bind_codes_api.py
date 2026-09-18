import uuid
from datetime import UTC, datetime, timedelta
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


async def _seed_identity(client: AsyncClient, tenant: TenantContext, user_id: str) -> dict[str, Any]:
    agent = await client.post(
        "/api/v1/agents",
        json={
            "key": f"agent-{uuid.uuid4().hex[:8]}",
            "name": "Identity Agent",
            "instructions": "You are helpful.",
            "model_id": str(tenant.model_id),
            "runtime_config": {},
        },
        headers=_headers(tenant),
    )
    agent_id = agent.json()["data"]["id"]
    bot_account_id = uuid.uuid4()
    bot_id = f"bot-{uuid.uuid4().hex[:8]}"
    identity_id = uuid.uuid4()
    external_user_id = f"ext-{uuid.uuid4().hex[:8]}"
    session_factory = get_session_factory()
    async with session_factory() as session:
        await session.execute(
            text(
                "INSERT INTO control.bot_account "
                "(id, tenant_id, channel, name, bot_id, secret_ref, agent_id) "
                "VALUES (:id, :tenant_id, 'WECOM', 'Identity Bot', :bot_id, 'secret://wecom/bot', :agent_id)"
            ),
            {"id": bot_account_id, "tenant_id": tenant.tenant_id, "bot_id": bot_id, "agent_id": agent_id},
        )
        await session.execute(
            text(
                "INSERT INTO control.channel_identity "
                "(id, tenant_id, channel, identity_key, external_user_id, bot_account_id, "
                "platform_user_id) VALUES (:id, :tenant_id, 'WECOM', :identity_key, "
                ":external_user_id, :bot_account_id, :user_id)"
            ),
            {
                "id": identity_id,
                "tenant_id": tenant.tenant_id,
                "identity_key": f"WECOM:{bot_id}:{external_user_id}",
                "external_user_id": external_user_id,
                "bot_account_id": bot_account_id,
                "user_id": user_id,
            },
        )
        await session.commit()
    return {"identity_id": str(identity_id), "external_user_id": external_user_id, "bot_id": bot_id}


async def test_generate_bind_code_revokes_previous_active_code(
    client: AsyncClient, tenant: TenantContext
) -> None:
    user_id = await _create_user(client, tenant)
    try:
        first = await client.post(f"/api/v1/users/{user_id}/bind-codes", headers=_headers(tenant))
        assert first.status_code == 200
        first_data = first.json()["data"]
        assert first_data["status"] == "ACTIVE"
        assert first_data["bind_code"]
        expires_at = datetime.fromisoformat(first_data["expires_at"])
        delta = expires_at - datetime.now(UTC)
        assert timedelta(minutes=9) < delta < timedelta(minutes=11)

        session_factory = get_session_factory()
        async with session_factory() as session:
            stored = (
                await session.execute(
                    text("SELECT code_hash, status FROM control.bind_code WHERE platform_user_id = :uid"),
                    {"uid": user_id},
                )
            ).all()
        assert len(stored) == 1
        assert stored[0][0] != first_data["bind_code"]
        assert stored[0][1] == "ACTIVE"

        second = await client.post(f"/api/v1/users/{user_id}/bind-codes", headers=_headers(tenant))
        assert second.status_code == 200
        assert second.json()["data"]["bind_code"] != first_data["bind_code"]
        async with session_factory() as session:
            statuses = (
                await session.execute(
                    text(
                        "SELECT status, count(*) FROM control.bind_code "
                        "WHERE platform_user_id = :uid GROUP BY status"
                    ),
                    {"uid": user_id},
                )
            ).all()
        assert dict(statuses) == {"REVOKED": 1, "ACTIVE": 1}
    finally:
        await _cleanup_seeded(tenant.tenant_id)


async def test_list_and_unbind_identities(client: AsyncClient, tenant: TenantContext) -> None:
    user_id = await _create_user(client, tenant)
    seeded = await _seed_identity(client, tenant, user_id)
    try:
        listing = await client.get(f"/api/v1/users/{user_id}/identities", headers=_headers(tenant))
        assert listing.status_code == 200
        page = listing.json()["data"]
        assert page["total"] == 1
        item = page["items"][0]
        assert item["id"] == seeded["identity_id"]
        assert item["channel"] == "WECOM"
        assert item["external_user_id"] == seeded["external_user_id"]
        assert item["bot_id"] == seeded["bot_id"]
        assert item["user_status"] == "ACTIVE"

        removed = await client.delete(
            f"/api/v1/users/{user_id}/identities/{seeded['identity_id']}", headers=_headers(tenant)
        )
        assert removed.status_code == 200
        assert removed.json()["data"]["unbound"] is True

        empty = await client.get(f"/api/v1/users/{user_id}/identities", headers=_headers(tenant))
        assert empty.json()["data"]["total"] == 0

        again = await client.delete(
            f"/api/v1/users/{user_id}/identities/{seeded['identity_id']}", headers=_headers(tenant)
        )
        assert again.status_code == 404
        assert again.json()["code"] == "COMMON_NOT_FOUND"

        missing_user = await client.get(f"/api/v1/users/{uuid.uuid4()}/identities", headers=_headers(tenant))
        assert missing_user.status_code == 404
    finally:
        await _cleanup_seeded(tenant.tenant_id)
