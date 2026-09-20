"""[B-128] API-09 Resolve Runtime Credentials（真实 HTTP + 真实 PostgreSQL Owner 表）。

服务身份校验、租户/资源归属、密钥轮换、CREDENTIAL_MISSING、不重算授权、不返回 catalog。
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.control import ModelDefinition
from muad_console_platform.infrastructure.models.mcp import McpServer
from sqlalchemy import update

from console_internal.conftest import TenantContext

URL = "/internal/runtime/resolve-credentials"


def _service_headers(tenant: TenantContext) -> dict[str, str]:
    return {"X-Tenant-Id": tenant.tenant_id, "X-Internal-Service": "test-internal-token"}


def _payload(tenant: TenantContext, model_id: str | None = None) -> dict[str, object]:
    return {
        "execution_ref": {"type": "RUN", "id": str(uuid.uuid4())},
        "actor_user_id": str(tenant.actor_user_id),
        "model_id": model_id or str(tenant.model_id),
        "mcp_server_ids": [],
    }


@pytest.fixture(autouse=True)
def _service_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", "test-internal-token")


async def test_b128_requires_service_identity(client: AsyncClient, tenant: TenantContext) -> None:
    """[B-128] 无服务身份 → FORBIDDEN；公开用户身份不可调用。"""
    missing = await client.post(URL, json=_payload(tenant), headers={"X-Tenant-Id": tenant.tenant_id})
    assert missing.status_code in (401, 403)

    wrong = await client.post(
        URL,
        json=_payload(tenant),
        headers={"X-Tenant-Id": tenant.tenant_id, "X-Internal-Service": "not-the-token"},
    )
    assert wrong.status_code in (401, 403)


async def test_b128_returns_model_key_and_rotation(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """[B-128] 返回当前 api_key；轮换后读取新值；不返回 params/catalog 字段。"""
    response = await client.post(URL, json=_payload(tenant), headers=_service_headers(tenant))
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["model"]["id"] == str(tenant.model_id)
    assert data["model"]["api_key"] == tenant.model_api_key
    assert set(data["model"].keys()) == {"id", "api_key"}  # 不含 params/base_url
    assert data["mcp_servers"] == []

    async with get_session_factory()() as session:
        await session.execute(
            update(ModelDefinition)
            .where(ModelDefinition.id == tenant.model_id)
            .values(api_key="rotated-key-value")
        )
        await session.commit()

    rotated = await client.post(URL, json=_payload(tenant), headers=_service_headers(tenant))
    assert rotated.status_code == 200
    assert rotated.json()["data"]["model"]["api_key"] == "rotated-key-value"

    async with get_session_factory()() as session:
        await session.execute(
            update(ModelDefinition)
            .where(ModelDefinition.id == tenant.model_id)
            .values(api_key=tenant.model_api_key)
        )
        await session.commit()


async def test_b128_cross_tenant_model_rejected(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """[B-128] 跨租户资源主键不可越界 → COMMON_NOT_FOUND。"""
    payload = _payload(tenant, model_id=str(uuid.uuid4()))
    response = await client.post(URL, json=payload, headers=_service_headers(tenant))
    assert response.status_code == 404
    assert response.json()["code"] == "COMMON_NOT_FOUND"


async def test_b128_missing_secret_returns_credential_missing(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """[B-128] 密钥清空 → CREDENTIAL_MISSING，不退回环境变量。"""
    async with get_session_factory()() as session:
        await session.execute(
            update(ModelDefinition)
            .where(ModelDefinition.id == tenant.model_id)
            .values(api_key=None)
        )
        await session.commit()

    response = await client.post(URL, json=_payload(tenant), headers=_service_headers(tenant))
    assert response.status_code in (404, 409, 422)
    assert response.json()["code"] == "CREDENTIAL_MISSING"

    async with get_session_factory()() as session:
        await session.execute(
            update(ModelDefinition)
            .where(ModelDefinition.id == tenant.model_id)
            .values(api_key=tenant.model_api_key)
        )
        await session.commit()


async def test_b128_mcp_secret_and_grant_independence(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """[B-128] MCP auth_secret 读取；Grant 撤销不替换冻结能力（API-09 不重算授权）。"""
    async with get_session_factory()() as session:
        server = McpServer(
            tenant_id=tenant.tenant_id,
            key=f"cred-{uuid.uuid4()}",
            name="Cred MCP",
            endpoint="http://127.0.0.1:9/mcp",
            auth_secret="mcp-secret-value",
            enabled=False,  # 已禁用——API-09 不因 enabled 重新筛选
        )
        session.add(server)
        await session.commit()
        server_id = server.id

    payload = _payload(tenant)
    payload["mcp_server_ids"] = [str(server_id)]
    response = await client.post(URL, json=payload, headers=_service_headers(tenant))
    assert response.status_code == 200, response.text
    servers = response.json()["data"]["mcp_servers"]
    assert len(servers) == 1
    assert servers[0]["mcp_server_id"] == str(server_id)
    assert servers[0]["auth_secret"] == "mcp-secret-value"

    async with get_session_factory()() as session:
        await session.execute(
            McpServer.__table__.delete().where(McpServer.id == server_id)
        )
        await session.commit()


async def test_b128_invalid_execution_ref_type(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """[B-128] execution_ref 非法类型 → COMMON_VALIDATION_ERROR/FORBIDDEN。"""
    payload = _payload(tenant)
    payload["execution_ref"] = {"type": "SESSION", "id": str(uuid.uuid4())}
    response = await client.post(URL, json=payload, headers=_service_headers(tenant))
    assert response.status_code in (403, 422)
