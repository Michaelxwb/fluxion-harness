"""FU-03 后端：GET /api/v1/platform-users/{id}/chat-access（按用户列链接，只读）。

真实边界：ASGI → ConsoleApplicationService → PostgreSQLRegistryStore（fluxion_test，
唯一租户隔离，不整库 reset）。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from fluxion.api.console import create_app as create_console_app
from fluxion.config import DevModeSettings
from fluxion.registry import PostgreSQLRegistryStore
from fluxion.registry.channel_store import ChatAccessRecord
from fluxion.services.console_app import ConsoleApplicationService
from tests.runtime_helpers import TEST_POSTGRES_DSN

TENANT = "tenant-fu03"


def _headers() -> dict[str, str]:
    return {"X-Tenant-ID": TENANT, "X-Actor-ID": "admin-fu03"}


async def _stack() -> tuple[PostgreSQLRegistryStore, AsyncClient]:
    store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN)
    await store.initialize()
    console = ConsoleApplicationService(store)
    settings = DevModeSettings(enabled=True)
    client = AsyncClient(
        transport=ASGITransport(app=create_console_app(console, dev_mode=settings)),
        base_url="http://console",
    )
    return store, client


@pytest.mark.asyncio
async def test_list_user_chat_access_unknown_user_404() -> None:
    _, client = await _stack()
    async with client:
        response = await client.get(
            "/api/v1/platform-users/no-such-user/chat-access", headers=_headers()
        )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_user_chat_access_roundtrip() -> None:
    store, client = await _stack()
    user_id = f"user-{uuid.uuid4().hex[:8]}"
    async with client:
        created = await client.post(
            "/api/v1/platform-users",
            json={"platform_user_id": user_id, "display_name": "FU03"},
            headers=_headers(),
        )
        assert created.status_code == 200
        tenant_id = created.json()["data"]["tenant_id"]
        empty = await client.get(
            f"/api/v1/platform-users/{user_id}/chat-access", headers=_headers()
        )
        assert empty.status_code == 200
        assert empty.json()["data"]["items"] == []

        record = ChatAccessRecord(
            access_id=f"chat_access_{uuid.uuid4().hex}",
            tenant_id=tenant_id,
            platform_user_id=user_id,
            agent_id="agent_demo",
            token_hash=uuid.uuid4().hex + uuid.uuid4().hex,
            created_at=datetime.now(UTC),
        )
        await store.create_chat_access(record)
        listed = await client.get(
            f"/api/v1/platform-users/{user_id}/chat-access", headers=_headers()
        )
        assert listed.status_code == 200
        items = listed.json()["data"]["items"]
        assert len(items) == 1
        assert items[0]["access_id"] == record.access_id
        assert items[0]["agent_id"] == "agent_demo"
        assert "created_at" in items[0]
        assert "token" not in items[0]
        assert "token_hash" not in items[0]

        await store.revoke_chat_access(
            tenant_id=tenant_id, access_id=record.access_id, revoked_at=datetime.now(UTC)
        )
        after_revoke = await client.get(
            f"/api/v1/platform-users/{user_id}/chat-access", headers=_headers()
        )
        assert after_revoke.json()["data"]["items"] == []
