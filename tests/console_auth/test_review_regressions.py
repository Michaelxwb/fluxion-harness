"""Console review regressions: real ASGI requests and PostgreSQL."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from muad_console_platform.api.security import CSRF_COOKIE, SESSION_COOKIE
from muad_console_platform.application.auth_service import hash_session_token
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.auth import ConsoleAccount, ConsoleSession
from muad_console_platform.infrastructure.models.mcp import McpServer
from sqlalchemy import select, update

from console_auth.conftest import (
    ADMIN_PASSWORD,
    BUILDER_PASSWORD,
    AuthContext,
    csrf_headers,
    login,
)


async def test_builder_cannot_grant_or_revoke_agent_users(client: AsyncClient, auth: AuthContext) -> None:
    await login(client, auth, auth.builder_username, BUILDER_PASSWORD)
    path = f"/api/v1/agents/{auth.agent_id}/users/{uuid.uuid4()}"
    for method in (client.post, client.delete):
        response = await method(path, headers=csrf_headers(client))
        assert response.status_code == 403
        assert response.json()["code"] == "FORBIDDEN"


async def test_builder_cannot_change_mcp_scope_through_edit(client: AsyncClient, auth: AuthContext) -> None:
    await login(client, auth, auth.builder_username, BUILDER_PASSWORD)
    created = await client.post(
        "/api/v1/mcp-servers",
        json={"key": f"scope-{uuid.uuid4()}", "name": "Scope", "endpoint": "http://127.0.0.1:9/mcp"},
        headers=csrf_headers(client),
    )
    assert created.status_code == 200
    server_id = created.json()["data"]["mcp_id"]
    try:
        dedicated = await client.put(
            f"/api/v1/mcp-servers/{server_id}/user-scope",
            json={"user_scope": "ALL"}, headers=csrf_headers(client),
        )
        assert dedicated.status_code == 403
        edited = await client.put(
            f"/api/v1/mcp-servers/{server_id}",
            json={"user_scope": "ALL"}, headers=csrf_headers(client),
        )
        assert edited.status_code == 422
        detail = await client.get(f"/api/v1/mcp-servers/{server_id}")
        assert detail.json()["data"]["user_scope"] == "SELECTED"
    finally:
        async with get_session_factory()() as session:
            await session.execute(McpServer.__table__.delete().where(McpServer.id == uuid.UUID(server_id)))
            await session.commit()


async def test_parallel_failed_logins_lock_account(client: AsyncClient, auth: AuthContext) -> None:
    responses = await asyncio.gather(*(
        login(client, auth, auth.builder_username, "wrong-password") for _ in range(5)
    ))
    assert all(response.json()["code"] == "INVALID_CREDENTIALS" for response in responses)
    async with get_session_factory()() as session:
        account = await session.get(ConsoleAccount, auth.builder_id)
        assert account.locked_until is not None
        assert account.locked_until > datetime.now(UTC)
        assert account.failed_attempts == 0
    blocked = await login(client, auth, auth.builder_username, BUILDER_PASSWORD)
    assert blocked.json()["code"] == "ACCOUNT_LOCKED"


async def test_builder_cannot_register_mcp_for_all_users(client: AsyncClient, auth: AuthContext) -> None:
    await login(client, auth, auth.builder_username, BUILDER_PASSWORD)
    response = await client.post(
        "/api/v1/mcp-servers",
        json={"key": f"scope-{uuid.uuid4()}", "name": "Scope",
              "endpoint": "http://127.0.0.1:9/mcp", "user_scope": "ALL"},
        headers=csrf_headers(client),
    )
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


async def expire_to_renewal(token: str) -> None:
    now = datetime.now(UTC)
    async with get_session_factory()() as session:
        await session.execute(
            update(ConsoleSession).where(ConsoleSession.token_hash == hash_session_token(token)).values(
                issued_at=now - timedelta(hours=7), expires_at=now + timedelta(hours=5),
            )
        )
        await session.commit()


async def test_session_renewal_refreshes_both_cookies_once(client: AsyncClient, auth: AuthContext) -> None:
    await login(client, auth, auth.admin_username, ADMIN_PASSWORD)
    token = client.cookies.get(SESSION_COOKIE)
    csrf = client.cookies.get(CSRF_COOKIE)
    await expire_to_renewal(token)
    renewed = await client.get("/api/v1/accounts")
    assert renewed.status_code == 200
    cookies = renewed.headers.get_list("set-cookie")
    assert len(cookies) == 2
    assert all("Max-Age=43200" in cookie for cookie in cookies)
    assert client.cookies.get(SESSION_COOKIE) == token
    assert client.cookies.get(CSRF_COOKIE) == csrf
    async with get_session_factory()() as session:
        row = await session.scalar(select(ConsoleSession).where(
            ConsoleSession.token_hash == hash_session_token(token),
        ))
        assert row.expires_at - row.issued_at == timedelta(hours=12)
    following = await client.get("/api/v1/auth/me")
    assert following.status_code == 200
    assert following.headers.get_list("set-cookie") == []


async def test_logout_does_not_reissue_renewed_cookies(client: AsyncClient, auth: AuthContext) -> None:
    await login(client, auth, auth.admin_username, ADMIN_PASSWORD)
    await expire_to_renewal(client.cookies.get(SESSION_COOKIE))
    response = await client.post("/api/v1/auth/logout", headers=csrf_headers(client))
    assert response.status_code == 200
    cookies = response.headers.get_list("set-cookie")
    assert len(cookies) == 2
    assert all("Max-Age=0" in cookie for cookie in cookies)
    assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_bearer_renewal_does_not_set_browser_cookies(client: AsyncClient, auth: AuthContext) -> None:
    await login(client, auth, auth.admin_username, ADMIN_PASSWORD)
    token = client.cookies.get(SESSION_COOKIE)
    await expire_to_renewal(token)
    response = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.headers.get_list("set-cookie") == []
