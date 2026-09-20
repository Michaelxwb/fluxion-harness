"""RULE-api-002：MCP 注册 Idempotency-Key 重放与指纹冲突。"""

from __future__ import annotations

from httpx import AsyncClient

from console_mcp.conftest import create_mcp, mcp_env, tenant_headers


async def test_register_replays_first_response_for_same_key(
    client: AsyncClient, env: dict[str, object]
) -> None:
    payload = {
        "name": "Idempotent MCP",
        "key": f"mcp-idem-{__import__('uuid').uuid4().hex[:8]}",
        "endpoint": "http://127.0.0.1:9/mcp",
    }
    headers = {**tenant_headers(env), "Idempotency-Key": "register-key-001"}
    first = await client.post("/api/v1/mcp-servers", json=payload, headers=headers)
    assert first.status_code == 200, first.text
    mcp_id = first.json()["data"]["mcp_id"]

    replay = await client.post("/api/v1/mcp-servers", json=payload, headers=headers)
    assert replay.status_code == 200
    assert replay.json()["data"]["mcp_id"] == mcp_id

    listing = await client.get(
        "/api/v1/mcp-servers",
        params={"keyword": payload["key"]},
        headers=tenant_headers(env),
    )
    assert listing.json()["data"]["total"] == 1

    conflicting = await client.post(
        "/api/v1/mcp-servers",
        json={**payload, "name": "Different Name"},
        headers=headers,
    )
    assert conflicting.status_code == 409
    assert conflicting.json()["code"] == "IDEMPOTENCY_MISMATCH"


def test_fixtures_available() -> None:
    assert mcp_env and create_mcp
