from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import AsyncClient
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.control import PlatformUser
from muad_console_platform.infrastructure.repositories.credential_repository import (
    CredentialRepository,
)
from muad_console_platform.main import app
from muad_platform_sdk import PlatformConfig, SessionMode
from muad_platform_sdk.adapter import PlatformAdapterRegistry
from sqlalchemy import text

from console_platform.conftest import TenantContext
from console_platform.test_users_api import _headers


class AltAdapter:
    key = "alt-http"
    name = "备用 HTTP"
    version = "2"
    session_mode = SessionMode.NONE
    platform_config_schema = {"type": "object", "properties": {}, "additionalProperties": False}
    credential_schema = {"type": "object", "properties": {}, "additionalProperties": False}

    async def authenticate(self, platform: PlatformConfig, credential: Any) -> None:
        del platform, credential
        return None

    async def validate(self, platform: PlatformConfig, session: Any) -> bool:
        del platform, session
        return True

    async def prepare_request(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError


class RecordingSessions:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def clear_platform(self, *, tenant_id: str, platform_id: uuid.UUID) -> int:
        self.calls.append(str(platform_id))
        return 1


@pytest.fixture(autouse=True)
def alt_adapter_registered() -> AsyncIterator[None]:
    registry = app.state.platform_adapters
    assert isinstance(registry, PlatformAdapterRegistry)
    if "alt-http" not in {adapter.key for adapter in registry.list()}:
        registry.register(AltAdapter())
    yield


@pytest.fixture
async def recording_sessions() -> AsyncIterator[RecordingSessions]:
    recorder = RecordingSessions()
    app.state.platform_sessions = recorder
    try:
        yield recorder
    finally:
        del app.state.platform_sessions


@pytest.fixture
async def seeded_platform_user(tenant: TenantContext) -> AsyncIterator[uuid.UUID]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        user = PlatformUser(
            tenant_id=tenant.tenant_id,
            user_code=f"code-{uuid.uuid4()}",
            display_name="Platform Credential User",
        )
        session.add(user)
        await session.commit()
        user_id = user.id
    try:
        yield user_id
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


def _payload(key: str | None = None, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "key": key or f"platform-{uuid.uuid4().hex[:8]}",
        "name": "Demo Platform",
        "resolver_type": "BASE_URL",
        "resolver_config": {"base_url": "https://api.example.com"},
        "adapter_key": "generic-http",
        "adapter_config": {"auth_scheme": "bearer"},
        "credential_mode": "USER_ONLY",
        "enabled": True,
    }
    body.update(overrides)
    return body


async def _credential_status(tenant_id: str, user_id: uuid.UUID, platform_id: uuid.UUID) -> str | None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        credential = await CredentialRepository(session).get_user(tenant_id, user_id, platform_id)
    return credential.status if credential is not None else None


async def test_s01_create_list_detail_roundtrip(client: AsyncClient, tenant: TenantContext) -> None:
    key = f"platform-{uuid.uuid4().hex[:8]}"
    created = await client.post(
        "/api/v1/project-platforms", json=_payload(key), headers=_headers(tenant)
    )
    assert created.status_code == 200
    platform_id = created.json()["data"]["platform_id"]

    listing = await client.get(
        "/api/v1/project-platforms", params={"keyword": key}, headers=_headers(tenant)
    )
    assert listing.status_code == 200
    data = listing.json()["data"]
    assert data["total"] == 1
    item = data["items"][0]
    assert item["key"] == key
    assert item["configured_user_credential_count"] == 0
    assert item["has_shared_credential"] is False

    detail = await client.get(f"/api/v1/project-platforms/{platform_id}", headers=_headers(tenant))
    assert detail.status_code == 200
    body = detail.json()["data"]
    assert body["resolver_config"] == {"base_url": "https://api.example.com"}
    assert body["adapter_metadata"]["key"] == "generic-http"
    assert "auth_secret" not in body

    session_factory = get_session_factory()
    async with session_factory() as session:
        audited = await session.scalar(
            text(
                "SELECT count(*) FROM control.config_audit_log "
                "WHERE resource_id = :platform_id AND action = 'CREATE'"
            ),
            {"platform_id": platform_id},
        )
    assert audited == 1


async def test_create_conflicts_and_validation_do_not_persist(
    client: AsyncClient, tenant: TenantContext
) -> None:
    key = f"platform-{uuid.uuid4().hex[:8]}"
    assert (await client.post(
        "/api/v1/project-platforms", json=_payload(key), headers=_headers(tenant)
    )).status_code == 200
    conflict = await client.post(
        "/api/v1/project-platforms", json=_payload(key), headers=_headers(tenant)
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "PLATFORM_KEY_EXISTS"
    assert key in conflict.json()["msg"], "专用码必须带出冲突的 key"

    unknown_adapter = await client.post(
        "/api/v1/project-platforms",
        json=_payload(f"platform-{uuid.uuid4().hex[:8]}", adapter_key="not-registered"),
        headers=_headers(tenant),
    )
    assert unknown_adapter.status_code == 404
    assert unknown_adapter.json()["code"] == "PLATFORM_ADAPTER_NOT_FOUND"

    invalid_config = await client.post(
        "/api/v1/project-platforms",
        json=_payload(
            f"platform-{uuid.uuid4().hex[:8]}", adapter_config={"auth_scheme": "digest"}
        ),
        headers=_headers(tenant),
    )
    assert invalid_config.status_code == 422
    assert invalid_config.json()["code"] == "COMMON_VALIDATION_ERROR"

    listing = await client.get(
        "/api/v1/project-platforms", headers=_headers(tenant), params={"keyword": "platform-"}
    )
    keys = {item["key"] for item in listing.json()["data"]["items"]}
    assert key in keys


async def test_adapter_change_invalidates_credentials_and_clears_sessions(
    client: AsyncClient,
    tenant: TenantContext,
    seeded_platform_user: uuid.UUID,
    recording_sessions: RecordingSessions,
) -> None:
    created = await client.post(
        "/api/v1/project-platforms",
        json=_payload(),
        headers=_headers(tenant),
    )
    platform_id = uuid.UUID(created.json()["data"]["platform_id"])
    session_factory = get_session_factory()
    async with session_factory() as session:
        await CredentialRepository(session).upsert_user(
            tenant_id=tenant.tenant_id,
            user_id=seeded_platform_user,
            platform_id=platform_id,
            credential_json={"token": "plaintext"},
            schema_version="1",
        )
        await session.commit()

    updated = await client.put(
        f"/api/v1/project-platforms/{platform_id}",
        json={"adapter_key": "alt-http", "adapter_config": {}},
        headers=_headers(tenant),
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["credential_reconfigure_required"] is True
    assert await _credential_status(tenant.tenant_id, seeded_platform_user, platform_id) == "INVALID"
    assert recording_sessions.calls

    unrelated = await client.put(
        f"/api/v1/project-platforms/{platform_id}",
        json={"name": "Renamed Platform"},
        headers=_headers(tenant),
    )
    assert unrelated.status_code == 200
    assert unrelated.json()["data"]["credential_reconfigure_required"] is False


async def test_delete_soft_deletes_platform_and_invalidates_credentials(
    client: AsyncClient, tenant: TenantContext, seeded_platform_user: uuid.UUID
) -> None:
    created = await client.post(
        "/api/v1/project-platforms", json=_payload(), headers=_headers(tenant)
    )
    platform_id = uuid.UUID(created.json()["data"]["platform_id"])
    removed = await client.delete(
        f"/api/v1/project-platforms/{platform_id}", headers=_headers(tenant)
    )
    assert removed.status_code == 200
    missing = await client.get(
        f"/api/v1/project-platforms/{platform_id}", headers=_headers(tenant)
    )
    assert missing.status_code == 404
    assert missing.json()["code"] == "COMMON_NOT_FOUND"


async def test_list_with_user_id_reports_credential_status(
    client: AsyncClient, tenant: TenantContext, seeded_platform_user: uuid.UUID
) -> None:
    created = await client.post(
        "/api/v1/project-platforms", json=_payload(), headers=_headers(tenant)
    )
    platform_id = uuid.UUID(created.json()["data"]["platform_id"])
    session_factory = get_session_factory()
    async with session_factory() as session:
        await CredentialRepository(session).upsert_user(
            tenant_id=tenant.tenant_id,
            user_id=seeded_platform_user,
            platform_id=platform_id,
            credential_json={"token": "plaintext"},
            schema_version="1",
        )
        await session.commit()
    listing = await client.get(
        "/api/v1/project-platforms",
        params={"keyword": "platform-", "user_id": str(seeded_platform_user)},
        headers=_headers(tenant),
    )
    assert listing.status_code == 200
    items = listing.json()["data"]["items"]
    configured = [item for item in items if item["platform_id"] == str(platform_id)]
    assert configured and configured[0]["user_credential_status"] == "ACTIVE"
    assert all("user_credential_status" in item for item in items)
