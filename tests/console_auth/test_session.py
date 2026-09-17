import secrets
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from httpx import AsyncClient
from muad_console_platform.api.security import CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE
from muad_console_platform.application.auth_service import hash_session_token
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.auth import ConsoleSession

from console_auth.conftest import ADMIN_PASSWORD, BUILDER_PASSWORD, AuthContext, csrf_headers, login


async def _create_session(
    auth: AuthContext,
    *,
    issued_at: datetime,
    expires_at: datetime,
    last_seen_at: datetime,
    revoked_at: datetime | None = None,
) -> str:
    token = secrets.token_urlsafe(32)
    async with get_session_factory()() as session:
        session.add(
            ConsoleSession(
                account_id=auth.admin_id,
                token_hash=hash_session_token(token),
                issued_at=issued_at,
                expires_at=expires_at,
                last_seen_at=last_seen_at,
                revoked_at=revoked_at,
            )
        )
        await session.commit()
    return token


async def _fetch_session(token: str) -> ConsoleSession | None:
    async with get_session_factory()() as session:
        return await session.scalar(
            sa.select(ConsoleSession).where(
                ConsoleSession.token_hash == hash_session_token(token)
            )
        )


async def test_me_resolves_session_cookie(client: AsyncClient, auth: AuthContext) -> None:
    response = await login(client, auth, auth.admin_username, ADMIN_PASSWORD)
    assert response.status_code == 200

    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 200
    body = me.json()
    assert body["code"] == "0"
    assert body["data"] == {
        "id": str(auth.admin_id),
        "username": auth.admin_username,
        "display_name": "Admin",
        "role": "ADMIN",
    }


async def test_me_without_session_returns_unauthorized(client: AsyncClient) -> None:
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"


async def test_expired_session_returns_unauthorized(client: AsyncClient, auth: AuthContext) -> None:
    now = datetime.now(UTC)
    token = await _create_session(
        auth,
        issued_at=now - timedelta(hours=13),
        expires_at=now - timedelta(hours=1),
        last_seen_at=now - timedelta(hours=13),
    )
    client.cookies.set(SESSION_COOKIE, token)
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"


async def test_revoked_session_returns_unauthorized(client: AsyncClient, auth: AuthContext) -> None:
    now = datetime.now(UTC)
    token = await _create_session(
        auth,
        issued_at=now,
        expires_at=now + timedelta(hours=12),
        last_seen_at=now,
        revoked_at=now,
    )
    client.cookies.set(SESSION_COOKIE, token)
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"


async def test_session_slides_when_more_than_half_elapsed(
    client: AsyncClient, auth: AuthContext
) -> None:
    now = datetime.now(UTC)
    token = await _create_session(
        auth,
        issued_at=now - timedelta(hours=7),
        expires_at=now + timedelta(hours=5),
        last_seen_at=now - timedelta(hours=7),
    )
    client.cookies.set(SESSION_COOKIE, token)
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 200

    row = await _fetch_session(token)
    assert row is not None
    assert row.expires_at > datetime.now(UTC) + timedelta(hours=11)
    assert row.last_seen_at > datetime.now(UTC) - timedelta(minutes=1)


async def test_logout_revokes_session(client: AsyncClient, auth: AuthContext) -> None:
    login_response = await login(client, auth, auth.admin_username, ADMIN_PASSWORD)
    assert login_response.status_code == 200
    token = client.cookies.get(SESSION_COOKIE)
    assert token

    logout = await client.post("/api/v1/auth/logout", headers=csrf_headers(client))
    assert logout.status_code == 200
    assert logout.json()["data"] == {"logged_out": True}

    row = await _fetch_session(token)
    assert row is not None
    assert row.revoked_at is not None

    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 401
    assert me.json()["code"] == "UNAUTHORIZED"


async def test_logout_without_csrf_token_returns_forbidden(
    client: AsyncClient, auth: AuthContext
) -> None:
    response = await login(client, auth, auth.builder_username, BUILDER_PASSWORD)
    assert response.status_code == 200

    missing = await client.post("/api/v1/auth/logout")
    assert missing.status_code == 403
    assert missing.json()["code"] == "FORBIDDEN"

    wrong = await client.post(
        "/api/v1/auth/logout",
        headers={CSRF_HEADER: "not-the-csrf-cookie"},
    )
    assert wrong.status_code == 403
    assert wrong.json()["code"] == "FORBIDDEN"


async def test_logout_with_matching_csrf_token_succeeds(
    client: AsyncClient, auth: AuthContext
) -> None:
    response = await login(client, auth, auth.builder_username, BUILDER_PASSWORD)
    assert response.status_code == 200

    logout = await client.post(
        "/api/v1/auth/logout",
        headers={CSRF_HEADER: client.cookies.get(CSRF_COOKIE) or ""},
    )
    assert logout.status_code == 200


async def test_safe_methods_do_not_require_csrf(client: AsyncClient, auth: AuthContext) -> None:
    response = await login(client, auth, auth.admin_username, ADMIN_PASSWORD)
    assert response.status_code == 200

    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 200
