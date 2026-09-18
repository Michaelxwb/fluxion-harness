from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import AsyncClient
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.control import PlatformUser
from sqlalchemy import text

from console_platform.conftest import TenantContext
from console_platform.test_users_api import _headers


@pytest.fixture
async def platform_bundle(client: AsyncClient, tenant: TenantContext) -> AsyncIterator[dict[str, Any]]:
    payload = {
        "key": f"platform-{uuid.uuid4().hex[:8]}",
        "name": "Credential Platform",
        "resolver_type": "BASE_URL",
        "resolver_config": {"base_url": "https://api.example.com"},
        "adapter_key": "generic-http",
        "adapter_config": {"auth_scheme": "bearer"},
        "credential_mode": "USER_ONLY",
        "enabled": True,
    }
    created = await client.post("/api/v1/project-platforms", json=payload, headers=_headers(tenant))
    assert created.status_code == 200
    platform_id = created.json()["data"]["platform_id"]
    session_factory = get_session_factory()
    async with session_factory() as session:
        user = PlatformUser(
            tenant_id=tenant.tenant_id,
            user_code=f"code-{uuid.uuid4()}",
            display_name="Credential User",
        )
        session.add(user)
        await session.commit()
        user_id = str(user.id)
    try:
        yield {"platform_id": platform_id, "user_id": user_id, "key": payload["key"]}
    finally:
        async with session_factory() as session:
            for statement in (
                "DELETE FROM control.user_credential_ref WHERE tenant_id = :tenant_id",
                "DELETE FROM control.shared_credential_ref WHERE tenant_id = :tenant_id",
                "DELETE FROM control.project_platform WHERE tenant_id = :tenant_id",
                "DELETE FROM control.platform_user WHERE tenant_id = :tenant_id",
            ):
                await session.execute(text(statement), {"tenant_id": tenant.tenant_id})
            await session.commit()


async def _stored_credential(tenant_id: str, user_id: str, platform_id: str) -> dict[str, Any] | None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT credential_json, status FROM control.user_credential_ref "
                    "WHERE tenant_id = :tenant_id AND user_id = :user_id "
                    "AND platform_id = :platform_id AND is_deleted = false"
                ),
                {"tenant_id": tenant_id, "user_id": user_id, "platform_id": platform_id},
            )
        ).one_or_none()
    return {"credential_json": row[0], "status": row[1]} if row else None


async def test_s02_user_credential_plaintext_is_stored_but_never_echoed(
    client: AsyncClient, tenant: TenantContext, platform_bundle: dict[str, Any]
) -> None:
    platform_id, user_id = platform_bundle["platform_id"], platform_bundle["user_id"]
    url = f"/api/v1/project-platforms/{platform_id}/users/{user_id}/credential"

    saved = await client.put(url, json={"token": "plain-secret-token"}, headers=_headers(tenant))
    assert saved.status_code == 200
    assert saved.json()["data"]["status"] == "ACTIVE"
    assert "plain-secret-token" not in saved.text

    fetched = await client.get(url, headers=_headers(tenant))
    assert fetched.status_code == 200
    body = fetched.json()["data"]
    assert body["configured"] is True
    assert body["status"] == "ACTIVE"
    assert "plain-secret-token" not in fetched.text
    assert "credential_json" not in body

    stored = await _stored_credential(tenant.tenant_id, user_id, platform_id)
    assert stored is not None
    assert stored["credential_json"] == {"token": "plain-secret-token"}
    assert stored["status"] == "ACTIVE"

    session_factory = get_session_factory()
    async with session_factory() as session:
        audit_entries = (
            await session.execute(
                text(
                    "SELECT before_json, after_json FROM control.config_audit_log "
                    "WHERE resource_type = 'user_credential_ref'"
                )
            )
        ).all()
    assert audit_entries
    assert all("plain-secret-token" not in str(entry) for entry in audit_entries)

    removed = await client.delete(url, headers=_headers(tenant))
    assert removed.status_code == 200
    assert await _stored_credential(tenant.tenant_id, user_id, platform_id) is None
    missing = await client.get(url, headers=_headers(tenant))
    assert missing.json()["data"]["configured"] is False


async def test_e04_invalid_credential_payload_is_rejected_without_persisting(
    client: AsyncClient, tenant: TenantContext, platform_bundle: dict[str, Any]
) -> None:
    platform_id, user_id = platform_bundle["platform_id"], platform_bundle["user_id"]
    url = f"/api/v1/project-platforms/{platform_id}/users/{user_id}/credential"
    rejected = await client.put(url, json={"token": 123}, headers=_headers(tenant))
    assert rejected.status_code == 422
    assert rejected.json()["code"] == "COMMON_VALIDATION_ERROR"
    assert await _stored_credential(tenant.tenant_id, user_id, platform_id) is None

    unknown_platform = await client.put(
        f"/api/v1/project-platforms/{uuid.uuid4()}/users/{user_id}/credential",
        json={"token": "x"},
        headers=_headers(tenant),
    )
    assert unknown_platform.status_code == 404


async def test_shared_credential_lifecycle(
    client: AsyncClient, tenant: TenantContext, platform_bundle: dict[str, Any]
) -> None:
    platform_id = platform_bundle["platform_id"]
    url = f"/api/v1/project-platforms/{platform_id}/shared-credential"

    initial = await client.get(url, headers=_headers(tenant))
    assert initial.status_code == 200
    assert initial.json()["data"]["configured"] is False

    saved = await client.put(url, json={"token": "shared-token"}, headers=_headers(tenant))
    assert saved.status_code == 200
    assert saved.json()["data"]["configured"] is True
    assert "shared-token" not in saved.text

    fetched = await client.get(url, headers=_headers(tenant))
    assert fetched.json()["data"]["status"] == "ACTIVE"
    assert "shared-token" not in fetched.text

    removed = await client.delete(url, headers=_headers(tenant))
    assert removed.status_code == 200
    after = await client.get(url, headers=_headers(tenant))
    assert after.json()["data"]["configured"] is False
    assert (await client.delete(url, headers=_headers(tenant))).status_code == 404
