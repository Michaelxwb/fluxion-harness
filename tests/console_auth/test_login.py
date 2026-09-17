import sqlalchemy as sa
from httpx import AsyncClient, Response
from muad_console_platform.api.security import CSRF_COOKIE, SESSION_COOKIE
from muad_console_platform.application.auth_service import hash_password, hash_session_token
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.auth import ConsoleAccount, ConsoleSession

from console_auth.conftest import ADMIN_PASSWORD, BUILDER_PASSWORD, AuthContext, fetch_account, login


async def test_login_returns_account_and_sets_cookies(
    client: AsyncClient, auth: AuthContext
) -> None:
    response = await login(client, auth, auth.admin_username, ADMIN_PASSWORD)
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == "0"
    assert body["data"] == {
        "id": str(auth.admin_id),
        "username": auth.admin_username,
        "display_name": "Admin",
        "role": "ADMIN",
    }
    cookie_headers = response.headers.get_list("set-cookie")
    session_header = next(item for item in cookie_headers if item.startswith(f"{SESSION_COOKIE}="))
    csrf_header = next(item for item in cookie_headers if item.startswith(f"{CSRF_COOKIE}="))
    assert "httponly" in session_header.lower()
    assert "httponly" not in csrf_header.lower()
    assert "samesite=strict" in session_header.lower()
    assert client.cookies.get(SESSION_COOKIE)
    assert client.cookies.get(CSRF_COOKIE)


async def test_login_with_wrong_password_returns_invalid_credentials(
    client: AsyncClient, auth: AuthContext
) -> None:
    response = await login(client, auth, auth.admin_username, "wrong-password-value")
    assert response.status_code == 401
    assert response.json()["code"] == "INVALID_CREDENTIALS"
    assert client.cookies.get(SESSION_COOKIE) is None


async def test_login_with_unknown_user_returns_invalid_credentials(
    client: AsyncClient, auth: AuthContext
) -> None:
    response = await login(client, auth, "missing-account", "wrong-password-value")
    assert response.status_code == 401
    assert response.json()["code"] == "INVALID_CREDENTIALS"


async def test_login_with_disabled_account_returns_invalid_credentials(
    client: AsyncClient, auth: AuthContext
) -> None:
    response = await login(client, auth, auth.disabled_username, BUILDER_PASSWORD)
    assert response.status_code == 401
    assert response.json()["code"] == "INVALID_CREDENTIALS"


async def test_account_locks_after_five_failures(client: AsyncClient, auth: AuthContext) -> None:
    for _ in range(5):
        failed = await login(client, auth, auth.builder_username, "wrong-password-value")
        assert failed.status_code == 401
        assert failed.json()["code"] == "INVALID_CREDENTIALS"

    locked = await login(client, auth, auth.builder_username, BUILDER_PASSWORD)
    assert locked.status_code == 423
    assert locked.json()["code"] == "ACCOUNT_LOCKED"

    account = await fetch_account(auth, auth.builder_username)
    assert account is not None
    assert account.locked_until is not None
    assert account.failed_attempts == 0


async def test_successful_login_resets_failure_state(client: AsyncClient, auth: AuthContext) -> None:
    for _ in range(3):
        failed = await login(client, auth, auth.builder_username, "wrong-password-value")
        assert failed.status_code == 401
    account = await fetch_account(auth, auth.builder_username)
    assert account is not None
    assert account.failed_attempts == 3

    success = await login(client, auth, auth.builder_username, BUILDER_PASSWORD)
    assert success.status_code == 200
    refreshed = await fetch_account(auth, auth.builder_username)
    assert refreshed is not None
    assert refreshed.failed_attempts == 0
    assert refreshed.locked_until is None
    assert refreshed.last_login_at is not None


async def test_login_issues_hashed_session_token(client: AsyncClient, auth: AuthContext) -> None:
    response = await login(client, auth, auth.admin_username, ADMIN_PASSWORD)
    assert response.status_code == 200
    token = client.cookies.get(SESSION_COOKIE)
    assert token
    async with get_session_factory()() as session:
        row = await session.scalar(
            sa.select(ConsoleSession).where(ConsoleSession.token_hash == hash_session_token(token))
        )
    assert row is not None
    assert row.token_hash != token
    assert row.source_ip is not None


async def test_password_is_stored_as_argon2_hash(client: AsyncClient, auth: AuthContext) -> None:
    response = await login(client, auth, auth.admin_username, ADMIN_PASSWORD)
    assert response.status_code == 200
    account = await fetch_account(auth, auth.admin_username)
    assert isinstance(account, ConsoleAccount)
    assert account.password_hash.startswith("$argon2id$")
    assert ADMIN_PASSWORD not in account.password_hash



async def _post_login(
    client: AsyncClient, username: str, password: str, tenant_header: str | None
) -> Response:
    headers = {} if tenant_header is None else {"X-Tenant-Id": tenant_header}
    return await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
        headers=headers,
    )


async def test_login_without_tenant_header_resolves_unique_username(
    client: AsyncClient, auth: AuthContext
) -> None:
    response = await _post_login(client, auth.admin_username, ADMIN_PASSWORD, None)
    assert response.status_code == 200
    assert response.json()["data"]["username"] == auth.admin_username


async def test_login_with_tenant_header_does_not_match_other_tenant(
    client: AsyncClient, auth: AuthContext
) -> None:
    response = await _post_login(
        client, auth.admin_username, ADMIN_PASSWORD, f"other-{auth.tenant_id}"
    )
    assert response.status_code == 401
    assert response.json()["code"] == "INVALID_CREDENTIALS"


async def test_login_without_tenant_header_rejects_ambiguous_username(
    client: AsyncClient, auth: AuthContext
) -> None:
    async with get_session_factory()() as session:
        session.add(
            ConsoleAccount(
                tenant_id=f"other-{auth.tenant_id}",
                username=auth.admin_username,
                display_name="Duplicate",
                password_hash=hash_password(ADMIN_PASSWORD),
                role="BUILDER",
            )
        )
        await session.commit()
    try:
        ambiguous = await _post_login(client, auth.admin_username, ADMIN_PASSWORD, None)
        assert ambiguous.status_code == 401
        assert ambiguous.json()["code"] == "INVALID_CREDENTIALS"

        scoped = await _post_login(
            client, auth.admin_username, ADMIN_PASSWORD, auth.tenant_id
        )
        assert scoped.status_code == 200
        assert scoped.json()["data"]["role"] == "ADMIN"
    finally:
        async with get_session_factory()() as session:
            await session.execute(
                sa.text("DELETE FROM control.console_account WHERE tenant_id = :tenant_id"),
                {"tenant_id": f"other-{auth.tenant_id}"},
            )
            await session.commit()
