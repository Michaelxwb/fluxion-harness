"""摘要模型解析端点（ADR-06）：按既有 `model_definition` 主键取**单条**模型定义。

不得 Mock 的真实边界：真实 Console app + 真实路由 + 真实 PostgreSQL（`control.model_definition`）。

为什么单独一条端点：Runtime 在 Run 创建边界要解析 `compaction.summary.model_ref`（那是一条普通
的模型定义，endpoint / 模型名 / 凭据都与 Agent 主模型不同）。解析规则与解析主模型**同一套**——
同租户 + 必须 enabled —— 没有"退回主模型"这条分支。
"""

import uuid

from httpx import AsyncClient, Response

from console_internal.conftest import TenantContext
from tests.internal_service import internal_service_token, service_headers  # noqa: F401  (fixture 注册)

RESOLVE_MODEL_URL = "/internal/runtime/resolve-model"


def _headers(tenant: TenantContext, tenant_id: str | None = None) -> dict[str, str]:
    return service_headers(tenant_id or tenant.tenant_id)


def _payload(model_id: uuid.UUID, actor_user_id: uuid.UUID) -> dict[str, str]:
    return {"model_id": str(model_id), "actor_user_id": str(actor_user_id)}


def _assert_error(response: Response, status_code: int, code: str) -> None:
    assert response.status_code == status_code
    assert response.json()["code"] == code


async def test_resolve_model_returns_the_definition(
    client: AsyncClient, tenant: TenantContext
) -> None:
    response = await client.post(
        RESOLVE_MODEL_URL,
        json=_payload(tenant.model_id, tenant.actor_user_id),
        headers=_headers(tenant),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == "0"
    assert body["data"]["model"] == {
        "id": str(tenant.model_id),
        "revision": tenant.model_revision,
        "protocol": "OPENAI",
        "model_id": tenant.model_model_id,
        "base_url": tenant.model_base_url,
        "api_key": tenant.model_api_key,
        "params": tenant.model_params,
    }


async def test_disabled_model_returns_conflict(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """与解析主模型同一条口径：disabled 是 409 `MODEL_DISABLED`（不是静默回退）。"""
    response = await client.post(
        RESOLVE_MODEL_URL,
        json=_payload(tenant.disabled_model_id, tenant.actor_user_id),
        headers=_headers(tenant),
    )
    _assert_error(response, 409, "MODEL_DISABLED")


async def test_soft_deleted_and_unknown_models_return_not_found(
    client: AsyncClient, tenant: TenantContext
) -> None:
    for model_id in (tenant.deleted_model_id, uuid.uuid4()):
        response = await client.post(
            RESOLVE_MODEL_URL,
            json=_payload(model_id, tenant.actor_user_id),
            headers=_headers(tenant),
        )
        _assert_error(response, 404, "COMMON_NOT_FOUND")


async def test_other_tenant_model_returns_not_found(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """租户隔离：拿别的租户的模型主键解析 ⇒ 按"不存在"处理（不外泄存在性）。"""
    response = await client.post(
        RESOLVE_MODEL_URL,
        json=_payload(tenant.model_id, tenant.actor_user_id),
        headers=_headers(tenant, tenant.other_tenant_id),
    )
    _assert_error(response, 404, "COMMON_NOT_FOUND")


async def test_requires_service_identity(client: AsyncClient, tenant: TenantContext) -> None:
    """响应含明文 `api_key`，故与 `resolve-definition` / API-09 同门控。"""
    missing = await client.post(
        RESOLVE_MODEL_URL,
        json=_payload(tenant.model_id, tenant.actor_user_id),
        headers={"X-Tenant-Id": tenant.tenant_id},
    )
    assert missing.status_code in (401, 403)

    wrong = await client.post(
        RESOLVE_MODEL_URL,
        json=_payload(tenant.model_id, tenant.actor_user_id),
        headers={"X-Tenant-Id": tenant.tenant_id, "X-Internal-Service": "not-the-token"},
    )
    assert wrong.status_code in (401, 403)
