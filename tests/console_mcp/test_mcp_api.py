"""06 TASK-003 验收测试：E-02 / B-03 / B-04 + 连接测试（S-03 前置）。"""

from __future__ import annotations

import uuid
from typing import Any

from httpx import AsyncClient
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.mcp import AgentMcpBinding, McpUserGrant
from sqlalchemy import text

from console_mcp.conftest import create_mcp, mcp_env, probe_url, tenant_headers

TENANT_HEADERS_KEY = "x-tenant-id"


def _status_code(json_body: dict[str, Any]) -> str:
    return str(json_body["data"]["connection_status"])


async def _fetch_secret(tenant_id: str, mcp_id: str) -> str | None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        row = await session.scalar(
            text("SELECT auth_secret FROM control.mcp_server WHERE id = :id"),
            {"id": mcp_id},
        )
    del tenant_id
    return row


async def test_config_invalid(client: AsyncClient, env: dict[str, object]) -> None:
    invalid_transport = await create_mcp(
        client, env, endpoint="http://127.0.0.1:9/mcp", extra={"transport": "stdio"}
    )
    assert invalid_transport.status_code == 400, invalid_transport.text
    assert invalid_transport.json()["code"] == "MCP_CONFIG_INVALID"

    invalid_endpoint = await create_mcp(client, env, endpoint="ftp://example.invalid/mcp")
    assert invalid_endpoint.status_code == 400
    assert invalid_endpoint.json()["code"] == "MCP_CONFIG_INVALID"

    created = await create_mcp(client, env, endpoint="http://127.0.0.1:9/mcp")
    assert created.status_code == 200, created.text
    mcp_id = created.json()["data"]["mcp_id"]
    edit_transport = await client.put(
        f"/api/v1/mcp-servers/{mcp_id}",
        json={"transport": "stdio"},
        headers=tenant_headers(env),
    )
    assert edit_transport.status_code == 400
    assert edit_transport.json()["code"] == "MCP_CONFIG_INVALID"

    listing = await client.get(
        "/api/v1/mcp-servers", params={"keyword": "mcp-"}, headers=tenant_headers(env)
    )
    keys = {item["key"] for item in listing.json()["data"]["items"]}
    assert all(key.startswith("mcp-") for key in keys)


async def test_secret(client: AsyncClient, env: dict[str, object]) -> None:
    created = await create_mcp(
        client, env, endpoint="http://127.0.0.1:9/mcp", extra={"auth_secret": "plain-secret-value"}
    )
    assert created.status_code == 200
    mcp_id = created.json()["data"]["mcp_id"]

    detail = await client.get(f"/api/v1/mcp-servers/{mcp_id}", headers=tenant_headers(env))
    assert detail.status_code == 200
    body = detail.json()["data"]
    assert body["auth_secret_configured"] is True
    assert "plain-secret-value" not in detail.text

    listing = await client.get("/api/v1/mcp-servers", headers=tenant_headers(env))
    assert "plain-secret-value" not in listing.text

    stored = await _fetch_secret(str(env["tenant_id"]), mcp_id)
    assert stored == "plain-secret-value"

    updated = await client.put(
        f"/api/v1/mcp-servers/{mcp_id}",
        json={"auth_secret": "rotated-secret-value"},
        headers=tenant_headers(env),
    )
    assert updated.status_code == 200
    assert await _fetch_secret(str(env["tenant_id"]), mcp_id) == "rotated-secret-value"
    assert "rotated-secret-value" not in updated.text

    session_factory = get_session_factory()
    async with session_factory() as session:
        audits = (
            await session.execute(
                text(
                    "SELECT before_json, after_json FROM control.config_audit_log "
                    "WHERE resource_type = 'MCP_SERVER' AND tenant_id = :tenant_id"
                ),
                {"tenant_id": str(env["tenant_id"])},
            )
        ).all()
    assert audits, "config_audit_log 必须记录 mcp_server 变更"
    assert all("secret-value" not in str(entry) for entry in audits)


async def test_b04_envelope_pagination_and_counts(
    client: AsyncClient, env: dict[str, object]
) -> None:
    b04_key = f"mcp-{uuid.uuid4()}"
    first = await create_mcp(client, env, endpoint="http://127.0.0.1:9/mcp", key=b04_key)
    second = await create_mcp(client, env, endpoint="http://127.0.0.1:9/mcp", key=f"mcp-{uuid.uuid4()}", extra={"enabled": False, "user_scope": "ALL"})
    assert first.status_code == 200 and second.status_code == 200
    first_id = first.json()["data"]["mcp_id"]

    session_factory = get_session_factory()
    async with session_factory() as session:
        session.add_all(
            [
                McpUserGrant(
                    mcp_server_id=uuid.UUID(first_id),
                    user_id=uuid.UUID(str(env["actor_user_id"])),
                    granted_by=uuid.UUID(str(env["admin_id"])),
                ),
                AgentMcpBinding(
                    agent_id=uuid.UUID(str(env["agent_id"])),
                    mcp_server_id=uuid.UUID(first_id),
                ),
            ]
        )
        await session.commit()

    listing = await client.get("/api/v1/mcp-servers", headers=tenant_headers(env))
    assert listing.status_code == 200
    envelope = listing.json()
    for field in ("code", "msg", "data", "trace_id", "request_id", "timestamp"):
        assert field in envelope
    page = envelope["data"]
    for field in ("items", "page", "page_size", "total"):
        assert field in page
    by_id = {item["mcp_id"]: item for item in page["items"]}
    assert by_id[first_id]["tool_count"] == 0
    assert by_id[first_id]["using_agent_count"] == 1
    assert by_id[first_id]["selected_user_count"] == 1
    assert by_id[first_id]["transport"] == "streamable-http"

    disabled_only = await client.get(
        "/api/v1/mcp-servers", params={"enabled": "false"}, headers=tenant_headers(env)
    )
    assert disabled_only.json()["data"]["total"] == 1
    scoped = await client.get(
        "/api/v1/mcp-servers", params={"user_scope": "ALL"}, headers=tenant_headers(env)
    )
    assert scoped.json()["data"]["total"] == 1

    for bad in ({"page": 0}, {"page_size": 0}, {"page_size": 101}):
        rejected = await client.get(
            "/api/v1/mcp-servers", params=bad, headers=tenant_headers(env)
        )
        assert rejected.status_code == 422

    detail = await client.get(f"/api/v1/mcp-servers/{first_id}", headers=tenant_headers(env))
    detail_body = detail.json()["data"]
    assert detail_body["tool_catalog_revision"] == 0
    assert detail_body["connect_timeout_ms"] == 5000
    assert detail_body["tool_cache_ttl_sec"] == 300
    assert detail_body["auth_config"] == {}

    duplicate = await create_mcp(
        client, env, endpoint="http://127.0.0.1:9/mcp", key=b04_key
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "COMMON_CONFLICT"

    updated = await client.put(
        f"/api/v1/mcp-servers/{first_id}",
        json={"name": "Renamed MCP", "connect_timeout_ms": 2000},
        headers=tenant_headers(env),
    )
    assert updated.status_code == 200
    updated_body = updated.json()["data"]
    assert updated_body["mcp_id"] == first_id
    assert updated_body["tool_catalog_revision"] == 0
    renamed = await client.get(f"/api/v1/mcp-servers/{first_id}", headers=tenant_headers(env))
    assert renamed.json()["data"]["name"] == "Renamed MCP"
    assert renamed.json()["data"]["connect_timeout_ms"] == 2000

    removed = await client.delete(f"/api/v1/mcp-servers/{first_id}", headers=tenant_headers(env))
    assert removed.status_code == 200
    missing = await client.get(f"/api/v1/mcp-servers/{first_id}", headers=tenant_headers(env))
    assert missing.status_code == 404
    assert missing.json()["code"] == "COMMON_NOT_FOUND"
    after_delete = await client.get("/api/v1/mcp-servers", headers=tenant_headers(env))
    assert first_id not in {item["mcp_id"] for item in after_delete.json()["data"]["items"]}

    unknown = await client.get(
        f"/api/v1/mcp-servers/{uuid.uuid4()}", headers=tenant_headers(env)
    )
    assert unknown.status_code == 404


async def test_connection_test_probe(
    client: AsyncClient, env: dict[str, object], probe_url: str
) -> None:
    created = await create_mcp(client, env, endpoint=probe_url)
    assert created.status_code == 200, created.text
    mcp_id = created.json()["data"]["mcp_id"]

    tested = await client.post(
        f"/api/v1/mcp-servers/{mcp_id}/test", json={}, headers=tenant_headers(env)
    )
    assert tested.status_code == 200, tested.text
    body = tested.json()["data"]
    assert _status_code(tested.json()) == "AVAILABLE"
    assert body["connection_status"] == "AVAILABLE"
    assert isinstance(body["latency_ms"], int) and body["latency_ms"] >= 0
    assert body["server_info"]["name"]
    assert "tested_at" in body

    detail = await client.get(f"/api/v1/mcp-servers/{mcp_id}", headers=tenant_headers(env))
    assert detail.json()["data"]["connection_status"] == "AVAILABLE"
    assert detail.json()["data"]["tool_count"] == 0
    assert detail.json()["data"]["tool_catalog_revision"] == 0

    dead = await create_mcp(client, env, endpoint="http://127.0.0.1:9/mcp")
    dead_id = dead.json()["data"]["mcp_id"]
    failed = await client.post(
        f"/api/v1/mcp-servers/{dead_id}/test",
        json={"timeout_ms": 500},
        headers=tenant_headers(env),
    )
    assert failed.status_code == 200
    failed_body = failed.json()["data"]
    assert failed_body["connection_status"] == "UNAVAILABLE"
    assert failed_body["error_code"]
    detail_after = await client.get(
        f"/api/v1/mcp-servers/{dead_id}", headers=tenant_headers(env)
    )
    assert detail_after.json()["data"]["connection_status"] == "UNAVAILABLE"
    assert "secret" not in failed.text.lower() or "auth_secret_configured" in failed.text


def test_module_exports_env_fixture_names() -> None:
    assert mcp_env and probe_url and create_mcp and tenant_headers
