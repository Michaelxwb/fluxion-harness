"""TASK-001：MCP 认证配置校验、结构化详情与审计脱敏。

真实边界：真实 PostgreSQL（详情/审计断言）；纯校验用 `parse_auth_config` 单测。
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from muad_api import AppError
from muad_console_platform.application.dto import (
    MAX_MCP_AUTH_HEADER_COUNT,
    MAX_MCP_AUTH_HEADER_TOTAL_BYTES,
    MAX_MCP_AUTH_HEADER_VALUE_BYTES,
    MAX_MCP_AUTH_SECRET_LENGTH,
)
from muad_console_platform.application.mcp_service import parse_auth_config
from muad_console_platform.infrastructure.db import get_session_factory
from sqlalchemy import text

from console_mcp.conftest import create_mcp, tenant_headers

INVALID_CODE = "MCP_CONFIG_INVALID"


def _invalid(raw: dict[str, Any] | None) -> None:
    with pytest.raises(AppError) as exc:
        parse_auth_config(raw)
    assert exc.value.code == INVALID_CODE


def _headers(count: int, value: str = "value") -> dict[str, str]:
    return {f"X-Header-{index}": value for index in range(count)}


def test_parse_auth_config_defaults_and_normalizes() -> None:
    empty = parse_auth_config(None)
    assert empty.headers == {}
    assert empty.secret_header is None
    assert empty.model_dump(mode="json") == {"headers": {}, "secret_header": None}

    configured = parse_auth_config(
        {"headers": {"X-App-Id": "app-1"}, "secret_header": "X-Api-Key"}
    )
    assert configured.model_dump(mode="json") == {
        "headers": {"X-App-Id": "app-1"},
        "secret_header": "X-Api-Key",
    }

    cleared = parse_auth_config({"headers": {}, "secret_header": ""})
    assert cleared.secret_header is None


@pytest.mark.parametrize(
    "reserved",
    [
        "Host",
        "Content-Length",
        "Transfer-Encoding",
        "Connection",
        "Accept",
        "Content-Type",
        "Mcp-Session-Id",
        "MCP-Protocol-Version",
        "Authorization",
        "Cookie",
    ],
)
def test_parse_auth_config_rejects_reserved_headers(reserved: str) -> None:
    _invalid({"headers": {reserved: "value"}})


@pytest.mark.parametrize(
    "sensitive",
    ["X-Api-Key", "X-Apikey", "X-Private-Key", "X-Token", "X-Secret", "X-Password", "X-Credential"],
)
def test_parse_auth_config_rejects_sensitive_static_header_names(sensitive: str) -> None:
    _invalid({"headers": {sensitive: "value"}})


def test_parse_auth_config_rejects_duplicate_header_names_case_insensitively() -> None:
    _invalid({"headers": {"X-App-Id": "one", "x-app-id": "two"}})


@pytest.mark.parametrize(
    "headers",
    [
        {"X-App-Id": "a\r\nX-Evil: 1"},
        {"X-App-Id": "a\x00b"},
        {"X-App\r\nId": "value"},
    ],
)
def test_parse_auth_config_rejects_crlf_and_nul(headers: dict[str, str]) -> None:
    _invalid({"headers": headers})


@pytest.mark.parametrize(
    "raw",
    [
        {"headers": None},
        {"secret_header": None, "header": "X-Api-Key"},
        {"headers": []},
        {"headers": {"X-App-Id": None}},
        [],
        "not-a-dict",
    ],
)
def test_parse_auth_config_rejects_malformed_shapes(raw: object) -> None:
    _invalid(raw)  # type: ignore[arg-type]


def test_parse_auth_config_rejects_secret_header_conflict() -> None:
    _invalid({"headers": {"X-App-Id": "app-1"}, "secret_header": "x-app-id"})


@pytest.mark.parametrize("reserved", ["Authorization", "Host", "Cookie"])
def test_parse_auth_config_rejects_reserved_secret_header(reserved: str) -> None:
    _invalid({"secret_header": reserved})


@pytest.mark.parametrize("secret_header", ["X-Api\r\nKey", "X Api Key", "X-Api-Key "])
def test_parse_auth_config_rejects_invalid_secret_header_name(secret_header: str) -> None:
    _invalid({"secret_header": secret_header})


def test_parse_auth_config_enforces_header_limits() -> None:
    assert len(parse_auth_config({"headers": _headers(MAX_MCP_AUTH_HEADER_COUNT)}).headers) == 32
    _invalid({"headers": _headers(MAX_MCP_AUTH_HEADER_COUNT + 1)})

    at_limit = "a" * MAX_MCP_AUTH_HEADER_VALUE_BYTES
    assert parse_auth_config({"headers": {"X-App-Id": at_limit}}).headers["X-App-Id"] == at_limit
    _invalid({"headers": {"X-App-Id": at_limit + "a"}})

    too_large = {f"X-Large-{index}": "a" * 4000 for index in range(5)}
    assert sum(len(value) for value in too_large.values()) > MAX_MCP_AUTH_HEADER_TOTAL_BYTES
    _invalid({"headers": too_large})


def test_parse_auth_config_rejects_oversized_secret_header_name() -> None:
    _invalid({"secret_header": "X" * (MAX_MCP_AUTH_HEADER_VALUE_BYTES + 1)})


async def _server_count(tenant_id: str) -> int:
    async with get_session_factory()() as session:
        return int(
            await session.scalar(
                text("SELECT count(*) FROM control.mcp_server WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant_id},
            )
            or 0
        )


async def _audit_rows(tenant_id: str, mcp_id: str) -> list[tuple[Any, Any]]:
    async with get_session_factory()() as session:
        return (
            await session.execute(
                text(
                    "SELECT before_json, after_json FROM control.config_audit_log "
                    "WHERE resource_type = 'MCP_SERVER' AND tenant_id = :tenant_id "
                    "AND resource_id = CAST(:mcp_id AS uuid) ORDER BY create_time"
                ),
                {"tenant_id": tenant_id, "mcp_id": mcp_id},
            )
        ).all()


async def _update_audit_count(tenant_id: str, mcp_id: str) -> int:
    async with get_session_factory()() as session:
        return int(
            await session.scalar(
                text(
                    "SELECT count(*) FROM control.config_audit_log "
                    "WHERE resource_type = 'MCP_SERVER' AND action = 'UPDATE' "
                    "AND tenant_id = :tenant_id AND resource_id = CAST(:mcp_id AS uuid)"
                ),
                {"tenant_id": tenant_id, "mcp_id": mcp_id},
            )
            or 0
        )


@pytest.mark.parametrize(
    "auth_config",
    [
        {"headers": {"Host": "evil"}},
        {"headers": {"X-Api-Key": "leaked-secret"}},
        {"headers": {"X-App-Id": "a\r\nX-Evil: 1"}},
        {"headers": {"X-App-Id": "one", "x-app-id": "two"}},
        {"headers": {"X-App-Id": "app"}, "secret_header": "x-app-id"},
        {"secret_header": "Authorization"},
    ],
)
async def test_create_rejects_invalid_auth_config_without_persisting(
    client: httpx.AsyncClient, env: dict[str, object], auth_config: dict[str, Any]
) -> None:
    tenant_id = str(env["tenant_id"])
    before = await _server_count(tenant_id)
    response = await create_mcp(
        client,
        env,
        endpoint="http://127.0.0.1:9/mcp",
        extra={"auth_config": auth_config},
    )
    assert response.status_code == 400, response.text
    assert response.json()["code"] == INVALID_CODE
    assert await _server_count(tenant_id) == before


async def test_update_rejects_invalid_auth_config_without_mutation(
    client: httpx.AsyncClient, env: dict[str, object]
) -> None:
    tenant_id = str(env["tenant_id"])
    created = await create_mcp(
        client,
        env,
        endpoint="http://127.0.0.1:9/mcp",
        extra={
            "auth_secret": "original-secret",
            "auth_config": {"headers": {"X-App-Id": "app-1"}, "secret_header": "X-Api-Key"},
        },
    )
    assert created.status_code == 200, created.text
    mcp_id = str(created.json()["data"]["mcp_id"])
    before_detail = (
        await client.get(f"/api/v1/mcp-servers/{mcp_id}", headers=tenant_headers(env))
    ).json()["data"]
    before_audits = await _update_audit_count(tenant_id, mcp_id)

    rejected = await client.put(
        f"/api/v1/mcp-servers/{mcp_id}",
        json={"auth_config": {"headers": {"Content-Type": "text/plain"}}},
        headers=tenant_headers(env),
    )
    assert rejected.status_code == 400, rejected.text
    assert rejected.json()["code"] == INVALID_CODE
    after_detail = (
        await client.get(f"/api/v1/mcp-servers/{mcp_id}", headers=tenant_headers(env))
    ).json()["data"]
    assert after_detail["auth_config"] == before_detail["auth_config"]
    assert after_detail["auth_secret_configured"] is True
    assert await _update_audit_count(tenant_id, mcp_id) == before_audits


async def test_detail_returns_structured_auth_config_without_secret(
    client: httpx.AsyncClient, env: dict[str, object]
) -> None:
    secret = f"secret-{uuid.uuid4()}"
    created = await create_mcp(
        client,
        env,
        endpoint="http://127.0.0.1:9/mcp",
        extra={
            "auth_secret": secret,
            "auth_config": {"headers": {"X-App-Id": "app-1"}, "secret_header": "X-Api-Key"},
        },
    )
    assert created.status_code == 200, created.text
    mcp_id = created.json()["data"]["mcp_id"]

    detail = await client.get(f"/api/v1/mcp-servers/{mcp_id}", headers=tenant_headers(env))
    assert detail.status_code == 200
    body = detail.json()["data"]
    assert body["auth_config"] == {
        "headers": {"X-App-Id": "app-1"},
        "secret_header": "X-Api-Key",
    }
    assert body["auth_secret_configured"] is True
    assert secret not in detail.text

    plain = await create_mcp(client, env, endpoint="http://127.0.0.1:9/mcp")
    plain_id = plain.json()["data"]["mcp_id"]
    plain_detail = (
        await client.get(f"/api/v1/mcp-servers/{plain_id}", headers=tenant_headers(env))
    ).json()["data"]
    assert plain_detail["auth_config"] == {"headers": {}, "secret_header": None}


async def test_config_audit_log_excludes_secret_values(
    client: httpx.AsyncClient, env: dict[str, object]
) -> None:
    tenant_id = str(env["tenant_id"])
    secret = f"secret-{uuid.uuid4()}"
    created = await create_mcp(
        client,
        env,
        endpoint="http://127.0.0.1:9/mcp",
        extra={
            "auth_secret": secret,
            "auth_config": {"headers": {"X-App-Id": "app-1"}, "secret_header": "X-Api-Key"},
        },
    )
    assert created.status_code == 200, created.text
    mcp_id = str(created.json()["data"]["mcp_id"])

    audits = await _audit_rows(tenant_id, mcp_id)
    assert audits, "config_audit_log 必须记录 MCP Server 创建"
    rendered = str(audits)
    assert secret not in rendered
    assert "X-App-Id" in rendered and "app-1" in rendered

    cleared = await client.put(
        f"/api/v1/mcp-servers/{mcp_id}",
        json={"auth_secret": ""},
        headers=tenant_headers(env),
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["data"]["auth_secret_configured"] is False
    assert secret not in str(await _audit_rows(tenant_id, mcp_id))


async def test_auth_secret_length_and_control_chars_are_rejected(
    client: httpx.AsyncClient, env: dict[str, object]
) -> None:
    tenant_id = str(env["tenant_id"])
    before = await _server_count(tenant_id)

    too_long = await create_mcp(
        client,
        env,
        endpoint="http://127.0.0.1:9/mcp",
        extra={"auth_secret": "x" * (MAX_MCP_AUTH_SECRET_LENGTH + 1)},
    )
    assert too_long.status_code == 422

    control_chars = await create_mcp(
        client,
        env,
        endpoint="http://127.0.0.1:9/mcp",
        extra={"auth_secret": "secret\r\nX-Evil: 1"},
    )
    assert control_chars.status_code == 400
    assert control_chars.json()["code"] == INVALID_CODE
    assert await _server_count(tenant_id) == before


async def test_custom_headers_reach_connection_test_and_discovery(
    client: httpx.AsyncClient,
    env: dict[str, object],
    header_probe_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_HEADER_PROBE_REQUIRE", "X-App-Id=app-1;X-Api-Key=probe-secret")
    created = await create_mcp(
        client,
        env,
        endpoint=header_probe_url,
        extra={
            "auth_secret": "probe-secret",
            "auth_config": {"headers": {"X-App-Id": "app-1"}, "secret_header": "X-Api-Key"},
        },
    )
    assert created.status_code == 200, created.text
    mcp_id = created.json()["data"]["mcp_id"]

    tested = await client.post(
        f"/api/v1/mcp-servers/{mcp_id}/test", headers=tenant_headers(env)
    )
    assert tested.status_code == 200, tested.text
    assert tested.json()["data"]["connection_status"] == "AVAILABLE"

    discovered = await client.post(
        f"/api/v1/mcp-servers/{mcp_id}/discover-tools", headers=tenant_headers(env)
    )
    assert discovered.status_code == 200, discovered.text
    assert discovered.json()["data"]["tool_count"] == 1

    await client.put(
        f"/api/v1/mcp-servers/{mcp_id}",
        json={"auth_secret": "wrong-secret"},
        headers=tenant_headers(env),
    )
    rejected = await client.post(
        f"/api/v1/mcp-servers/{mcp_id}/test", headers=tenant_headers(env)
    )
    assert rejected.json()["data"]["connection_status"] == "UNAVAILABLE"
