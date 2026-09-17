import uuid

from httpx import AsyncClient

from console_auth.conftest import (
    ADMIN_PASSWORD,
    BUILDER_PASSWORD,
    AuthContext,
    csrf_headers,
    login,
    tenant_headers,
)

NEW_PASSWORD = "console-new-password"


async def test_admin_creates_account_and_new_account_can_login(
    client: AsyncClient, auth: AuthContext
) -> None:
    admin_login = await login(client, auth, auth.admin_username, ADMIN_PASSWORD)
    assert admin_login.status_code == 200

    username = f"created-{uuid.uuid4()}"
    created = await client.post(
        "/api/v1/accounts",
        json={
            "username": username,
            "display_name": "Created Builder",
            "password": NEW_PASSWORD,
            "role": "BUILDER",
        },
        headers={**tenant_headers(auth), **csrf_headers(client)},
    )
    assert created.status_code == 200
    body = created.json()
    assert body["code"] == "0"
    assert body["data"]["username"] == username
    assert body["data"]["role"] == "BUILDER"
    assert set(body["data"]) == {"id", "username", "display_name", "role"}

    relogin = await login(client, auth, username, NEW_PASSWORD)
    assert relogin.status_code == 200


async def test_admin_cannot_create_duplicate_username(
    client: AsyncClient, auth: AuthContext
) -> None:
    await login(client, auth, auth.admin_username, ADMIN_PASSWORD)
    duplicate = await client.post(
        "/api/v1/accounts",
        json={
            "username": auth.builder_username,
            "display_name": "Duplicate",
            "password": NEW_PASSWORD,
            "role": "BUILDER",
        },
        headers={**tenant_headers(auth), **csrf_headers(client)},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "COMMON_CONFLICT"


async def test_create_account_rejects_short_password(client: AsyncClient, auth: AuthContext) -> None:
    await login(client, auth, auth.admin_username, ADMIN_PASSWORD)
    response = await client.post(
        "/api/v1/accounts",
        json={
            "username": f"short-{uuid.uuid4()}",
            "display_name": "Short",
            "password": "short",
            "role": "BUILDER",
        },
        headers={**tenant_headers(auth), **csrf_headers(client)},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "COMMON_VALIDATION_ERROR"


async def test_account_changes_own_password(client: AsyncClient, auth: AuthContext) -> None:
    await login(client, auth, auth.builder_username, BUILDER_PASSWORD)

    wrong_current = await client.post(
        "/api/v1/auth/password",
        json={"current_password": "not-the-current-password", "new_password": NEW_PASSWORD},
        headers={**tenant_headers(auth), **csrf_headers(client)},
    )
    assert wrong_current.status_code == 401
    assert wrong_current.json()["code"] == "INVALID_CREDENTIALS"

    changed = await client.post(
        "/api/v1/auth/password",
        json={"current_password": BUILDER_PASSWORD, "new_password": NEW_PASSWORD},
        headers={**tenant_headers(auth), **csrf_headers(client)},
    )
    assert changed.status_code == 200
    assert changed.json()["data"] == {"changed": True}

    await client.post("/api/v1/auth/logout", headers=csrf_headers(client))
    old_password = await login(client, auth, auth.builder_username, BUILDER_PASSWORD)
    assert old_password.status_code == 401
    new_password = await login(client, auth, auth.builder_username, NEW_PASSWORD)
    assert new_password.status_code == 200
