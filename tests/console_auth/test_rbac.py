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


def _agent_payload(auth: AuthContext, key: str) -> dict[str, object]:
    return {
        "key": key,
        "name": "RBAC Agent",
        "instructions": "You are an RBAC agent.",
        "model_id": str(auth.model_id),
    }


async def test_unauthenticated_api_returns_unauthorized_envelope(
    client: AsyncClient, auth: AuthContext
) -> None:
    response = await client.get("/api/v1/agents", headers=tenant_headers(auth))
    assert response.status_code == 401
    body = response.json()
    assert body["code"] == "UNAUTHORIZED"
    assert body["msg"]
    assert body["trace_id"]


async def test_healthz_stays_public(client: AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["code"] == "0"


async def test_builder_cannot_access_admin_accounts_route(
    client: AsyncClient, auth: AuthContext
) -> None:
    response = await login(client, auth, auth.builder_username, BUILDER_PASSWORD)
    assert response.status_code == 200

    forbidden = await client.get("/api/v1/accounts", headers=tenant_headers(auth))
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == "FORBIDDEN"


async def test_admin_can_access_admin_accounts_route(client: AsyncClient, auth: AuthContext) -> None:
    response = await login(client, auth, auth.admin_username, ADMIN_PASSWORD)
    assert response.status_code == 200

    listing = await client.get("/api/v1/accounts", headers=tenant_headers(auth))
    assert listing.status_code == 200
    assert listing.json()["code"] == "0"
    usernames = [item["username"] for item in listing.json()["data"]]
    assert auth.admin_username in usernames
    assert auth.builder_username in usernames
    assert all("password_hash" not in item for item in listing.json()["data"])


async def test_builder_can_manage_agents(client: AsyncClient, auth: AuthContext) -> None:
    response = await login(client, auth, auth.builder_username, BUILDER_PASSWORD)
    assert response.status_code == 200

    created = await client.post(
        "/api/v1/agents",
        json=_agent_payload(auth, f"agent-{uuid.uuid4()}"),
        headers={**tenant_headers(auth), **csrf_headers(client)},
    )
    assert created.status_code == 200
    assert created.json()["code"] == "0"
