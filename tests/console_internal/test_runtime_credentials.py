"""[B-128] API-09 Resolve Runtime Credentials（真实 HTTP + 真实 PostgreSQL Owner 表）。

服务身份校验、租户/资源归属、密钥轮换、CREDENTIAL_MISSING、不重算授权、不返回 catalog。
"""

from __future__ import annotations

import hashlib
import uuid

import pytest
from httpx import AsyncClient
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.control import ModelCredential
from muad_console_platform.infrastructure.models.mcp import McpServer
from sqlalchemy import update

from console_internal.conftest import MODEL_BASE_URL, PROVIDER_KEY, TenantContext

URL = "/internal/runtime/resolve-credentials"
DEFINITION_URL = "/internal/runtime/resolve-definition"
MEMBERSHIP_FIELDS = {
    "model_id",
    "deployment_id",
    "credential_id",
    "credential_revision",
    "key_resource_id",
    "quota_group_id",
    "provider_key",
    "base_url",
    "api_key",
}


def _definition_payload() -> dict[str, object]:
    return {
        "agent_id": str(uuid.uuid4()),
        "actor_user_id": str(uuid.uuid4()),
        "channel": "WECOM",
    }


def _service_headers(tenant: TenantContext) -> dict[str, str]:
    return {"X-Tenant-Id": tenant.tenant_id, "X-Internal-Service": "test-internal-token"}


SECRET_DIGEST = hashlib.sha256(b"sk-happy").hexdigest()


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


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


async def test_resolve_definition_requires_service_identity(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """[越权修复] resolve-definition 响应含模型明文 `api_key`，故与 API-08/09 同门控。

    修复前该端点**无**服务身份校验，且挂在顶层 router（不经 authenticated/admin）——
    集群内任意进程 POST 即可拿到任意模型的明文 API Key。
    """
    missing = await client.post(
        DEFINITION_URL, json=_definition_payload(), headers={"X-Tenant-Id": tenant.tenant_id}
    )
    assert missing.status_code in (401, 403)

    wrong = await client.post(
        DEFINITION_URL,
        json=_definition_payload(),
        headers={"X-Tenant-Id": tenant.tenant_id, "X-Internal-Service": "not-the-token"},
    )
    assert wrong.status_code in (401, 403)


async def test_resolve_definition_passes_gate_with_service_identity(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """[越权修复] 带正确服务身份则通过门控（后续失败必须是业务码，不是门控 403）。

    与上一条构成对照：证明 403 确实来自服务身份校验，而不是任何请求都 403。
    """
    allowed = await client.post(
        DEFINITION_URL, json=_definition_payload(), headers=_service_headers(tenant)
    )
    assert allowed.status_code in (200, 404), allowed.text[:200]


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
    """[B-128] 返回当前 api_key 与真实 Key 身份；轮换成员凭据后读取新值且身份随值切换。"""
    response = await client.post(URL, json=_payload(tenant), headers=_service_headers(tenant))
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["model"]["model_id"] == str(tenant.model_id)
    assert data["model"]["api_key"] == tenant.model_api_key
    assert data["model"]["credential_id"] == str(tenant.model_credential_id)
    assert data["model"]["deployment_id"] == str(tenant.model_deployment_id)
    assert data["model"]["provider_key"] == PROVIDER_KEY
    assert data["model"]["base_url"] == MODEL_BASE_URL
    # 响应只给当前 Key 身份+版本（解锁密前缀）；去重指纹不出现
    assert set(data["model"].keys()) == MEMBERSHIP_FIELDS
    assert "api_key_digest" not in response.text and SECRET_DIGEST not in response.text
    first_identity = data["model"]["key_resource_id"]
    assert data["mcp_servers"] == []

    # 轮换发生在成员凭据行（逻辑模型已无密钥列）：同一身份换值（含去重摘要），读取侧立刻看到新值
    async with get_session_factory()() as session:
        await session.execute(
            update(ModelCredential)
            .where(ModelCredential.id == tenant.model_credential_id)
            .values(api_key="rotated-key-value", api_key_digest=_digest("rotated-key-value"))
        )
        await session.commit()

    rotated = await client.post(URL, json=_payload(tenant), headers=_service_headers(tenant))
    assert rotated.status_code == 200
    assert rotated.json()["data"]["model"]["api_key"] == "rotated-key-value"
    assert rotated.json()["data"]["model"]["key_resource_id"] != first_identity

    async with get_session_factory()() as session:
        await session.execute(
            update(ModelCredential)
            .where(ModelCredential.id == tenant.model_credential_id)
            .values(api_key=tenant.model_api_key, api_key_digest=_digest(tenant.model_api_key))
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
    """[B-128] 无可用成员凭据（凭据停用）→ CREDENTIAL_MISSING，不退回环境变量/旧列。"""
    async with get_session_factory()() as session:
        await session.execute(
            update(ModelCredential)
            .where(ModelCredential.id == tenant.model_credential_id)
            .values(enabled=False)
        )
        await session.commit()

    response = await client.post(URL, json=_payload(tenant), headers=_service_headers(tenant))
    assert response.status_code in (404, 409, 422)
    assert response.json()["code"] == "CREDENTIAL_MISSING"

    async with get_session_factory()() as session:
        await session.execute(
            update(ModelCredential)
            .where(ModelCredential.id == tenant.model_credential_id)
            .values(enabled=True)
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


async def test_runtime_credentials_support_mcp_without_auth(
    client: AsyncClient, tenant: TenantContext,
) -> None:
    async with get_session_factory()() as session:
        server = McpServer(
            tenant_id=tenant.tenant_id, key=f"no-auth-{uuid.uuid4()}", name="No auth",
            endpoint="http://127.0.0.1:9/mcp", auth_secret=None,
        )
        session.add(server)
        await session.commit()
        server_id = server.id
    try:
        payload = _payload(tenant)
        payload["mcp_server_ids"] = [str(server_id)]
        response = await client.post(URL, json=payload, headers=_service_headers(tenant))
        assert response.status_code == 200
        assert response.json()["data"]["mcp_servers"] == [
            {"mcp_server_id": str(server_id), "auth_secret": None},
        ]
    finally:
        async with get_session_factory()() as session:
            await session.execute(McpServer.__table__.delete().where(McpServer.id == server_id))
            await session.commit()
