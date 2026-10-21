"""[受理合并取数] 一次请求取齐定义、平台设置快照与当前认证值。

真实边界（业务路径不 mock）：经 `ASGITransport(app=app)` 走真实路由与真实
`require_service_identity`，定义/设置/凭据都来自真实 `ResolveService` /
`PlatformSettingsService` / `resolve_runtime_credentials`（与三个单点端点同一实现）。
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from console_internal.conftest import TenantContext
from tests.internal_service import internal_service_token, service_headers  # noqa: F401  (fixture 注册)

URL = "/internal/runtime/admission-materials"


def _payload(tenant: TenantContext, execution_id: uuid.UUID | None = None) -> dict[str, str]:
    return {
        "agent_id": str(tenant.agent_id),
        "actor_user_id": str(tenant.actor_user_id),
        "execution_id": str(execution_id or uuid.uuid4()),
        "channel": "WEBCHAT",
    }


async def test_returns_definition_settings_and_credentials_in_one_call(
    client: AsyncClient, tenant: TenantContext
) -> None:
    response = await client.post(
        URL, json=_payload(tenant), headers=service_headers(tenant.tenant_id)
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["code"] == "0"
    data = body["data"]

    definition = data["definition"]
    assert definition["agent"]["id"] == str(tenant.agent_id)
    assert definition["model"]["id"] == str(tenant.model_id)
    assert definition["skills"] == []
    assert definition["mcp_servers"] == []

    # 未植入平台设置版本 ⇒ schema 默认（与单点端点同一读法）。
    assert data["settings_revision"] == 0
    assert isinstance(data["settings"], dict) and data["settings"]

    credentials = data["credentials"]
    assert credentials["model"]["model_id"] == str(tenant.model_id)
    assert credentials["model"]["api_key"] == tenant.model_api_key
    assert credentials["mcp_servers"] == []


async def test_requires_service_identity(client: AsyncClient, tenant: TenantContext) -> None:
    response = await client.post(
        URL, json=_payload(tenant), headers={"X-Tenant-Id": tenant.tenant_id}
    )
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


@pytest.mark.parametrize("field", ("agent_id", "actor_user_id", "execution_id"))
async def test_rejects_missing_identity_fields(
    client: AsyncClient, tenant: TenantContext, field: str
) -> None:
    payload = _payload(tenant)
    payload.pop(field)
    response = await client.post(
        URL, json=payload, headers=service_headers(tenant.tenant_id)
    )
    assert response.status_code == 422
    assert response.json()["code"] == "COMMON_VALIDATION_ERROR"
