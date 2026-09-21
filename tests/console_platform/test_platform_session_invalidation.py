from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import redis.asyncio as redis
from httpx import AsyncClient
from muad_common import SharedSettings
from muad_console_platform.application.platform_adapter_service import build_default_registry
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.control import PlatformUser
from muad_console_platform.main import app
from muad_platform_sdk import (
    PlatformConfig,
    SessionMode,
    platform_session_key,
    platform_sessions_index_key,
    user_actor_scope,
)
from sqlalchemy import text

from console_platform.conftest import TenantContext
from console_platform.test_users_api import _headers

pytestmark = pytest.mark.skipif(
    not SharedSettings().redis_url, reason="REDIS_URL not configured"
)


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


@pytest.fixture(autouse=True)
def alt_adapter_registered() -> AsyncIterator[None]:
    registry = app.state.platform_adapters
    if "alt-http" not in {adapter.key for adapter in registry.list()}:
        registry.register(AltAdapter())
    yield
    app.state.platform_adapters = build_default_registry()


@pytest.fixture
async def redis_client() -> AsyncIterator[Any]:
    client = redis.from_url(SharedSettings().require_redis_url(), decode_responses=True)  # type: ignore[no-untyped-call]
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
async def session_env(
    client: AsyncClient, tenant: TenantContext, redis_client: Any
) -> AsyncIterator[dict[str, str]]:
    created = await client.post(
        "/api/v1/project-platforms",
        json={
            "key": f"platform-{uuid.uuid4().hex[:8]}",
            "name": "Session Platform",
            "resolver_type": "BASE_URL",
            "resolver_config": {"base_url": "https://api.example.com"},
            "adapter_key": "generic-http",
            "adapter_config": {"auth_scheme": "bearer"},
            "credential_mode": "USER_ONLY",
            "enabled": True,
        },
        headers=_headers(tenant),
    )
    assert created.status_code == 200
    platform_id = created.json()["data"]["platform_id"]
    session_factory = get_session_factory()
    async with session_factory() as session:
        user = PlatformUser(
            tenant_id=tenant.tenant_id,
            user_code=f"code-{uuid.uuid4()}",
            display_name="Session User",
        )
        session.add(user)
        await session.commit()
        user_id = str(user.id)
    environment = {"platform_id": platform_id, "user_id": user_id}
    try:
        yield environment
    finally:
        index_key = platform_sessions_index_key(
            tenant_id=tenant.tenant_id, platform_id=platform_id
        )
        members = await redis_client.smembers(index_key)
        if members:
            await redis_client.delete(*members)
        await redis_client.delete(index_key)
        async with session_factory() as session:
            for statement in (
                "DELETE FROM control.user_credential_ref WHERE tenant_id = :tenant_id",
                "DELETE FROM control.shared_credential_ref WHERE tenant_id = :tenant_id",
                "DELETE FROM control.project_platform WHERE tenant_id = :tenant_id",
                "DELETE FROM control.platform_user WHERE tenant_id = :tenant_id",
            ):
                await session.execute(text(statement), {"tenant_id": tenant.tenant_id})
            await session.commit()


async def test_s04_adapter_change_invalidates_credentials_and_clears_session_index(
    client: AsyncClient,
    tenant: TenantContext,
    redis_client: Any,
    session_env: dict[str, str],
) -> None:
    platform_id = uuid.UUID(session_env["platform_id"])
    user_id = uuid.UUID(session_env["user_id"])
    saved = await client.put(
        f"/api/v1/project-platforms/{platform_id}/users/{user_id}/credential",
        json={"token": "plaintext-token"},
        headers=_headers(tenant),
    )
    assert saved.status_code == 200

    session_key = platform_session_key(
        tenant_id=tenant.tenant_id,
        platform_id=str(platform_id),
        actor_scope=user_actor_scope(str(user_id)),
        credential_version="v1",
        adapter_key="generic-http",
        adapter_version="1",
    )
    index_key = platform_sessions_index_key(
        tenant_id=tenant.tenant_id, platform_id=str(platform_id)
    )
    await redis_client.set(session_key, '{"session_id": "runtime-session"}')
    await redis_client.sadd(index_key, session_key)

    updated = await client.put(
        f"/api/v1/project-platforms/{platform_id}",
        json={"adapter_key": "alt-http", "adapter_config": {}},
        headers=_headers(tenant),
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["credential_reconfigure_required"] is True

    assert await redis_client.exists(session_key) == 0
    assert await redis_client.exists(index_key) == 0

    credential = await client.get(
        f"/api/v1/project-platforms/{platform_id}/users/{user_id}/credential",
        headers=_headers(tenant),
    )
    assert credential.json()["data"]["status"] == "INVALID"

    session_factory = get_session_factory()
    async with session_factory() as session:
        audited = await session.scalar(
            text(
                "SELECT count(*) FROM control.config_audit_log "
                "WHERE resource_id = :platform_id AND resource_type = 'project_platform' "
                "AND action = 'UPDATE'"
            ),
            {"platform_id": platform_id},
        )
    assert audited and audited >= 1


async def test_credential_delete_clears_session_index(
    client: AsyncClient,
    tenant: TenantContext,
    redis_client: Any,
    session_env: dict[str, str],
) -> None:
    platform_id = uuid.UUID(session_env["platform_id"])
    user_id = uuid.UUID(session_env["user_id"])
    saved = await client.put(
        f"/api/v1/project-platforms/{platform_id}/users/{user_id}/credential",
        json={"token": "plaintext-token"},
        headers=_headers(tenant),
    )
    assert saved.status_code == 200

    session_key = platform_session_key(
        tenant_id=tenant.tenant_id,
        platform_id=str(platform_id),
        actor_scope=user_actor_scope(str(user_id)),
        credential_version="v1",
        adapter_key="generic-http",
        adapter_version="1",
    )
    index_key = platform_sessions_index_key(
        tenant_id=tenant.tenant_id, platform_id=str(platform_id)
    )
    await redis_client.set(session_key, '{"session_id": "runtime-session"}')
    await redis_client.sadd(index_key, session_key)

    removed = await client.delete(
        f"/api/v1/project-platforms/{platform_id}/users/{user_id}/credential",
        headers=_headers(tenant),
    )
    assert removed.status_code == 200
    assert await redis_client.exists(session_key) == 0
    assert await redis_client.exists(index_key) == 0
