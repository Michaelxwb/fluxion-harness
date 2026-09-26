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
    body = listing.json()
    assert body["code"] == "0"
    # 列表必须走统一分页封套，不得返回裸数组（RULE-api-001）
    assert set(body["data"]) == {"items", "page", "page_size", "total"}
    assert body["data"]["page"] == 1
    assert body["data"]["page_size"] == 20
    usernames = [item["username"] for item in body["data"]["items"]]
    assert auth.admin_username in usernames
    assert auth.builder_username in usernames
    assert body["data"]["total"] >= len(usernames)
    assert all("password_hash" not in item for item in body["data"]["items"])


async def test_admin_accounts_list_is_paginated(client: AsyncClient, auth: AuthContext) -> None:
    response = await login(client, auth, auth.admin_username, ADMIN_PASSWORD)
    assert response.status_code == 200
    headers = tenant_headers(auth)

    first = await client.get("/api/v1/accounts", params={"page": 1, "page_size": 1}, headers=headers)
    assert first.status_code == 200
    page_one = first.json()["data"]
    assert page_one["page_size"] == 1
    assert len(page_one["items"]) == 1
    assert page_one["total"] >= 2  # 至少 admin 与 builder 两个种子账号

    second = await client.get("/api/v1/accounts", params={"page": 2, "page_size": 1}, headers=headers)
    assert second.status_code == 200
    page_two = second.json()["data"]
    assert len(page_two["items"]) == 1
    # 分页必须真正切分，而不是重复返回首页
    assert page_two["items"][0]["username"] != page_one["items"][0]["username"]

    # 边界：page_size 越界 422
    rejected = await client.get("/api/v1/accounts", params={"page_size": 101}, headers=headers)
    assert rejected.status_code == 422


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
