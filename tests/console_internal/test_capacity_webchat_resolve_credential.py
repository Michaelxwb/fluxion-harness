"""[B-220] 所选成员凭据的内部实时解析（API-09）。

真实边界：真实 Console ASGI 应用 + 真实服务身份门控 + 真实 PostgreSQL（`control.model_*`）；
不 mock 解析服务/数据库。

断言要点：
- 按**冻结引用**（model + member + credential + expected_revision）核对归属：缺失/软删/跨租户
  一律 404（不外泄存在性）；成员与凭据不再是同一对、或凭据版本已前进 → 409
  `MODEL_CREDENTIAL_CHANGED`（调用方需要重新选择/预占，不能拿新 Key 消费旧槽位）；
- 成功响应返回**真实 Key 身份**（`key_resource_id` 由 tenant/provider/指纹派生）与版本，
  只供调用内存使用：去重指纹 `api_key_digest` 绝不进入响应；
- 无共享额度组的 Key 其 `quota_group_id` 为 `null`（该 Key 独占自身容量）；
- MCP 可选鉴权语义保留（有 secret 返回秘密、无鉴权返回 `null`）；
- 冻结三件套必须同生同灭（部分给齐 = 422），`execution_ref` 只接受 RUN。
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from muad_console_platform.application.model_credential_service import derive_key_resource_id
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.control import ModelCredential, ModelDeployment
from muad_console_platform.infrastructure.models.mcp import McpServer
from sqlalchemy import inspect, text, update

from console_internal.conftest import (
    MODEL_API_KEY,
    MODEL_BASE_URL,
    PROVIDER_KEY,
    TenantContext,
)
from tests.internal_service import internal_service_token, service_headers  # noqa: F401  (fixture 注册)

URL = "/internal/runtime/resolve-credentials"
SECRET_FIELDS = {"api_key_digest", "key_resource_id_secret"}


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _headers(tenant: TenantContext, tenant_id: str | None = None) -> dict[str, str]:
    return service_headers(tenant_id or tenant.tenant_id)


def _payload(tenant: TenantContext, **overrides: Any) -> dict[str, Any]:
    """默认发**冻结三件套**（fixture 的成员/凭据/版本 1）；缺省即尚未冻结的过渡解析。"""
    body: dict[str, Any] = {
        "execution_ref": {"type": "RUN", "id": str(uuid.uuid4())},
        "actor_user_id": str(tenant.actor_user_id),
        "model_id": str(tenant.model_id),
        "deployment_id": str(tenant.model_deployment_id),
        "credential_id": str(tenant.model_credential_id),
        "expected_revision": 1,
        "mcp_server_ids": [],
    }
    body.update(overrides)
    return body


async def test_b220_frozen_references_resolve_the_selected_member_credential(
    client: AsyncClient, tenant: TenantContext
) -> None:
    response = await client.post(URL, json=_payload(tenant), headers=_headers(tenant))
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    selected = data["model"]
    assert selected == {
        "model_id": str(tenant.model_id),
        "deployment_id": str(tenant.model_deployment_id),
        "credential_id": str(tenant.model_credential_id),
        "credential_revision": 1,
        "key_resource_id": str(
            derive_key_resource_id(
                tenant.tenant_id, PROVIDER_KEY, _digest(MODEL_API_KEY)
            )
        ),
        "quota_group_id": None,  # 无共享额度组：该 Key 独占自身容量（lease 同口径允许 NULL）
        "provider_key": PROVIDER_KEY,
        "base_url": MODEL_BASE_URL,
        "api_key": MODEL_API_KEY,
    }
    # 去重指纹/派生细节不外泄：响应只给真实 Key 身份本身
    assert SECRET_FIELDS & set(selected) == set()
    assert _digest(MODEL_API_KEY) not in response.text
    assert data["mcp_servers"] == []

    # 受信调用方可以不声明 actor（只作审计关联）：同一冻结引用仍然解析成功
    without_actor = _payload(tenant)
    without_actor.pop("actor_user_id")
    anonymous_actor = await client.post(URL, json=without_actor, headers=_headers(tenant))
    assert anonymous_actor.status_code == 200, anonymous_actor.text
    assert anonymous_actor.json()["data"]["model"]["credential_id"] == str(
        tenant.model_credential_id
    )


async def test_b220_credential_revision_mismatch_requires_a_new_reservation(
    client: AsyncClient, tenant: TenantContext
) -> None:
    rotated = "sk-rotated-b220-canary"
    async with get_session_factory()() as session:
        await session.execute(
            update(ModelCredential)
            .where(ModelCredential.id == tenant.model_credential_id)
            .values(api_key=rotated, api_key_digest=_digest(rotated), revision=2)
        )
        await session.commit()

    stale = await client.post(URL, json=_payload(tenant), headers=_headers(tenant))
    assert stale.status_code == 409, stale.text
    assert stale.json()["code"] == "MODEL_CREDENTIAL_CHANGED"
    # 冲突响应不得回显新旧任何明文
    assert MODEL_API_KEY not in stale.text and rotated not in stale.text

    fresh = await client.post(
        URL, json=_payload(tenant, expected_revision=2), headers=_headers(tenant)
    )
    assert fresh.status_code == 200, fresh.text
    selected = fresh.json()["data"]["model"]
    assert selected["credential_revision"] == 2 and selected["api_key"] == rotated
    # 换值即换计量身份：新 Key 不继承旧身份的窗口/占用
    assert selected["key_resource_id"] == str(
        derive_key_resource_id(tenant.tenant_id, PROVIDER_KEY, _digest(rotated))
    )


async def test_b220_frozen_member_credential_pair_must_still_match(
    client: AsyncClient, tenant: TenantContext
) -> None:
    other_secret = "sk-other-pair-b220"
    async with get_session_factory()() as session:
        other = ModelCredential(
            tenant_id=tenant.tenant_id,
            key=f"cred-b220-{uuid.uuid4().hex[:6]}",
            provider_key=PROVIDER_KEY,
            base_url=MODEL_BASE_URL,
            api_key=other_secret,
            api_key_digest=_digest(other_secret),
            max_inflight=8,
        )
        session.add(other)
        await session.commit()
        other_id = other.id

    mismatched = await client.post(
        URL, json=_payload(tenant, credential_id=str(other_id)), headers=_headers(tenant)
    )
    assert mismatched.status_code == 409, mismatched.text
    assert mismatched.json()["code"] == "MODEL_CREDENTIAL_CHANGED"
    assert other_secret not in mismatched.text


async def test_b220_unknown_or_foreign_references_are_not_found(
    client: AsyncClient, tenant: TenantContext
) -> None:
    unknown_member = await client.post(
        URL, json=_payload(tenant, deployment_id=str(uuid.uuid4())), headers=_headers(tenant)
    )
    assert unknown_member.status_code == 404
    assert unknown_member.json()["code"] == "COMMON_NOT_FOUND"

    unknown_credential = await client.post(
        URL, json=_payload(tenant, credential_id=str(uuid.uuid4())), headers=_headers(tenant)
    )
    assert unknown_credential.status_code == 404
    assert unknown_credential.json()["code"] == "COMMON_NOT_FOUND"

    # 跨租户：模型/成员/凭据都按归属缺失处理（头里的租户决定归属）
    foreign = await client.post(URL, json=_payload(tenant), headers=_headers(tenant, tenant.other_tenant_id))
    assert foreign.status_code == 404
    assert foreign.json()["code"] == "COMMON_NOT_FOUND"
    assert MODEL_API_KEY not in foreign.text


async def test_b220_soft_deleted_member_or_credential_is_not_an_entry(
    client: AsyncClient, tenant: TenantContext
) -> None:
    async with get_session_factory()() as session:
        await session.execute(
            update(ModelDeployment)
            .where(ModelDeployment.id == tenant.model_deployment_id)
            .values(is_deleted=True)
        )
        await session.commit()
    deleted_member = await client.post(URL, json=_payload(tenant), headers=_headers(tenant))
    assert deleted_member.status_code == 404
    assert deleted_member.json()["code"] == "COMMON_NOT_FOUND"

    async with get_session_factory()() as session:
        await session.execute(
            update(ModelDeployment)
            .where(ModelDeployment.id == tenant.model_deployment_id)
            .values(is_deleted=False)
        )
        await session.execute(
            update(ModelCredential)
            .where(ModelCredential.id == tenant.model_credential_id)
            .values(is_deleted=True)
        )
        await session.commit()
    deleted_credential = await client.post(URL, json=_payload(tenant), headers=_headers(tenant))
    assert deleted_credential.status_code == 404
    assert deleted_credential.json()["code"] == "COMMON_NOT_FOUND"
    assert MODEL_API_KEY not in deleted_credential.text


@pytest.mark.parametrize(
    "overrides",
    [
        {"deployment_id": None},
        {"credential_id": None},
        {"expected_revision": None},
        {"expected_revision": 0},
        {"execution_ref": {"type": "SESSION", "id": "not-a-uuid"}},
        {"model_id": "not-a-uuid"},
    ],
)
async def test_b220_frozen_triplet_and_execution_ref_are_strict(
    client: AsyncClient, tenant: TenantContext, overrides: dict[str, Any]
) -> None:
    body = _payload(tenant)
    # 部分给齐冻结三件套 / 非法归属：都必须在契约层被拒，不落任何解析
    for key, value in overrides.items():
        if value is None:
            body.pop(key)
        else:
            body[key] = value
    response = await client.post(URL, json=body, headers=_headers(tenant))
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "COMMON_VALIDATION_ERROR"


async def test_b220_service_identity_gates_both_branches(
    client: AsyncClient, tenant: TenantContext
) -> None:
    unfrozen = _payload(tenant, deployment_id=None, credential_id=None, expected_revision=None)
    for body in (_payload(tenant), unfrozen):
        missing = await client.post(URL, json=body, headers={"X-Tenant-Id": tenant.tenant_id})
        assert missing.status_code in (401, 403)
        wrong = await client.post(
            URL,
            json=body,
            headers={"X-Tenant-Id": tenant.tenant_id, "X-Internal-Service": "not-the-token"},
        )
        assert wrong.status_code in (401, 403)


async def test_b220_mcp_optional_auth_semantics_are_preserved(
    client: AsyncClient, tenant: TenantContext
) -> None:
    async with get_session_factory()() as session:
        secret_server = McpServer(
            tenant_id=tenant.tenant_id,
            key=f"mcp-b220-{uuid.uuid4().hex[:6]}",
            name="MCP with secret",
            endpoint="http://127.0.0.1:9/mcp",
            auth_secret="mcp-secret-b220",
            enabled=False,  # 已禁用不影响解析：冻结引用按当前认证值读取
        )
        open_server = McpServer(
            tenant_id=tenant.tenant_id,
            key=f"mcp-b220-{uuid.uuid4().hex[:6]}",
            name="MCP without auth",
            endpoint="http://127.0.0.1:9/mcp",
            auth_secret=None,
        )
        session.add_all([secret_server, open_server])
        await session.commit()
        ids = [secret_server.id, open_server.id]

    body = _payload(tenant, mcp_server_ids=[str(value) for value in ids])
    response = await client.post(URL, json=body, headers=_headers(tenant))
    assert response.status_code == 200, response.text
    servers = response.json()["data"]["mcp_servers"]
    assert [(item["mcp_server_id"], item["auth_secret"]) for item in servers] == [
        (str(ids[0]), "mcp-secret-b220"),
        (str(ids[1]), None),
    ]

    async with get_session_factory()() as session:
        await session.execute(
            McpServer.__table__.delete().where(McpServer.id.in_(ids))
        )
        await session.commit()


async def test_b220_unfrozen_call_still_returns_the_same_wire_shape(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """过渡分支（尚未冻结成员选择）返回与冻结分支同一投影，调用方无需两套解析。"""
    frozen = await client.post(URL, json=_payload(tenant), headers=_headers(tenant))
    interim = await client.post(
        URL,
        json=_payload(tenant, deployment_id=None, credential_id=None, expected_revision=None),
        headers=_headers(tenant),
    )
    assert interim.status_code == 200, interim.text
    assert set(interim.json()["data"]["model"]) == set(frozen.json()["data"]["model"])
    assert interim.json()["data"]["model"]["credential_id"] == str(tenant.model_credential_id)


async def test_b220_key_rows_are_the_only_authority_for_the_plaintext() -> None:
    """结构事实：逻辑模型没有可回退的密钥列，解析只能来自成员凭据表。"""
    async with get_session_factory()() as session:
        columns = await session.run_sync(
            lambda sync: {
                table: {
                    column["name"]
                    for column in inspect(sync.connection()).get_columns(table, "control")
                }
                for table in ("model_definition", "model_credential")
            }
        )
        legacy = await session.scalar(
            text(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_schema = 'control' AND table_name = 'model_definition' "
                "AND column_name IN ('api_key', 'base_url')"
            )
        )
    assert {"api_key", "base_url"} <= columns["model_credential"]
    assert {"api_key", "base_url"} & columns["model_definition"] == set()
    assert legacy == 0
