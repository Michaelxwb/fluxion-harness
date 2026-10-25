"""API-09 Resolve Runtime Credentials：只读 Owner 表当前认证值，不重算授权/catalog。

两种调用口径（同一响应投影，调用方无需两套解析）：

- **冻结引用**（`deployment_id` + `credential_id` + `expected_revision` 给齐）：按冻结的
  成员/凭据核对归属与版本，返回**当前**认证值与真实 Key 身份。版本前进或成员-凭据不再是同一对
  → `MODEL_CREDENTIAL_CHANGED`（调用方必须重新选择/预占，不能拿新 Key 消费旧槽位）；
- **过渡解析**（三件套缺省）：成员选取落地（按调用冻结）之前，取模型首个可用成员——TASK-024/025
  把 Runtime 的冻结引用接上后由那条链路收口。

密钥只进调用内存：响应给出当前明文与由 tenant/provider/指纹派生的 `key_resource_id`，
但**去重指纹本身不出现**（RULE-secret-001）；MCP 可选鉴权语义保留（无鉴权 = `null`）。
"""

from __future__ import annotations

import uuid
from typing import Any

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import ResolveCredentialsRequest
from muad_contracts.model_pool import SelectedModelCredential
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.control import ModelCredential, ModelDefinition
from ..infrastructure.models.mcp import McpServer
from ..infrastructure.repositories.model_credential_repository import ModelCredentialRepository
from ..infrastructure.repositories.model_deployment_repository import ModelDeploymentRepository
from .model_credential_service import derive_key_resource_id


def require_service_identity(token: str | None, expected: str | None) -> None:
    """内部服务身份校验：缺失或不匹配即 FORBIDDEN。

    期望值由调用方（`api/deps.service_token`）从装配根的启动设置取：请求路径不再逐次重建
    `SharedSettings()`（每次构造要读环境与 `.env`，单进程并发下直接变成吞吐上限）。
    """
    if not expected or token != expected:
        raise AppError(ErrorCode.FORBIDDEN)


def selected_credential(
    tenant_id: str, model_id: uuid.UUID, deployment_id: uuid.UUID, credential: ModelCredential
) -> SelectedModelCredential:
    """按**当前行的实值**构造解析结果：明文只在此装载，指纹换算成稳定的计量身份。"""
    return SelectedModelCredential(
        model_id=model_id,
        deployment_id=deployment_id,
        credential_id=credential.id,
        credential_revision=credential.revision,
        key_resource_id=derive_key_resource_id(
            tenant_id, credential.provider_key, credential.api_key_digest
        ),
        quota_group_id=credential.quota_group_id,
        provider_key=credential.provider_key,
        base_url=credential.base_url,
        # 明文只在此装载；pydantic 的 SecretStr 让 repr/异常文本不带出密钥
        api_key=SecretStr(credential.api_key),
    )


async def _frozen_selection(
    session: AsyncSession,
    tenant_id: str,
    model_id: uuid.UUID,
    payload: ResolveCredentialsRequest,
) -> SelectedModelCredential:
    """按冻结引用解析：归属缺失 404；成员-凭据错配或版本前进 409（需要重新预占）。"""
    assert payload.deployment_id is not None
    assert payload.credential_id is not None
    assert payload.expected_revision is not None
    deployment = await ModelDeploymentRepository(session).get(
        tenant_id, model_id, payload.deployment_id
    )
    credential = await ModelCredentialRepository(session).get(tenant_id, payload.credential_id)
    if deployment is None or credential is None:
        raise AppError(ErrorCode.COMMON_NOT_FOUND)
    if deployment.credential_id != credential.id or credential.revision != payload.expected_revision:
        # 冻结的非密钥引用已经与当前事实不符：重新选择成员/预占，而不是拿新 Key 消费旧槽位
        raise AppError(ErrorCode.MODEL_CREDENTIAL_CHANGED)
    return selected_credential(tenant_id, model_id, deployment.id, credential)


async def _interim_selection(
    session: AsyncSession, tenant_id: str, model_id: uuid.UUID
) -> SelectedModelCredential:
    """过渡解析：模型级首个可用成员（按成员名稳定排序）。"""
    deployments = ModelDeploymentRepository(session)
    credential = await deployments.find_execution_credential(tenant_id, model_id)
    if credential is None:
        # 密钥缺失明确失败，不退回环境变量，也不退回逻辑模型自身的旧列
        raise AppError(ErrorCode.CREDENTIAL_MISSING)
    deployment = await deployments.find_by_credential(tenant_id, model_id, credential.id)
    assert deployment is not None  # 与 find_execution_credential 同一谓词
    return selected_credential(tenant_id, model_id, deployment.id, credential)


async def _mcp_credentials(
    session: AsyncSession, tenant_id: str, mcp_server_ids: list[uuid.UUID]
) -> list[dict[str, Any]]:
    """MCP 凭据可选：无鉴权服务返回 `null`；enabled/catalog 变更不重新筛选。"""
    if not mcp_server_ids:
        return []
    rows = await session.execute(
        select(McpServer).where(
            McpServer.id.in_(mcp_server_ids), McpServer.tenant_id == tenant_id
        )
    )
    by_id = {server.id: server for server in rows.scalars().all()}
    servers: list[dict[str, Any]] = []
    for server_id in mcp_server_ids:
        server = by_id.get(server_id)
        if server is None or server.is_deleted:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        servers.append({"mcp_server_id": str(server_id), "auth_secret": server.auth_secret})
    return servers


async def resolve_runtime_credentials(
    session: AsyncSession,
    tenant_id: str,
    payload: ResolveCredentialsRequest,
    *,
    selected: SelectedModelCredential | None = None,
) -> dict[str, Any]:
    """解析当前认证值；`selected` 由调用方在同一请求内已经解析过时传入，避免重复查库。"""
    if selected is None:
        model = await session.get(ModelDefinition, payload.model_id)
        if model is None or model.is_deleted or model.tenant_id != tenant_id:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        if payload.deployment_id is None:
            selected = await _interim_selection(session, tenant_id, payload.model_id)
        else:
            selected = await _frozen_selection(session, tenant_id, payload.model_id, payload)
    return {
        "model": selected.to_internal_payload(),
        "mcp_servers": await _mcp_credentials(session, tenant_id, payload.mcp_server_ids),
    }
