"""模型池资源 Console API-16/API-18（design §3.4；TASK-016/TASK-018）。

- 凭据管理属 ADMIN 维护面：整组挂 `api/router.py` 的 admin（`require_admin` + `require_csrf`），
  非 ADMIN 连读取都不放行（13-console-auth 设计 §2.4：前端隐藏 + 后端 403 兜底）；
- 读取投影只有 `configured`：明文密钥、去重指纹、计量身份（`key_resource_id`）不进入响应；
- 创建支持 `Idempotency-Key`（RULE-api-002）：规范化 JSON 指纹含 endpoint/tenant/actor 判别键，
  密钥只以摘要入指纹（见 `application/model_credential_service.create_fingerprint`）；
- 错误码只来自 `config/api-messages.yaml`：本模块只抛 `AppError`，`msg`/`http_status`
  由目录决定（`RULE-api-001`）。
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request
from muad_api import ApiResponse, ok, paginate
from muad_contracts.model_pool import ModelCredentialConfig, ModelCredentialCreate, ModelCredentialUpdate
from sqlalchemy.ext.asyncio import AsyncSession

from ..application.audit_service import AuditActor
from ..application.model_credential_service import ModelCredentialService
from ..infrastructure.db import get_session
from ..infrastructure.models.control import ModelCredential
from .deps import AccountTenantId, AdminAccount, get_source_ip

TenantId = AccountTenantId
Session = Annotated[AsyncSession, Depends(get_session)]

router = APIRouter(prefix="/api/v1/model-credentials", tags=["model-credentials"])


def _item(credential: ModelCredential) -> dict[str, Any]:
    """读取投影：仅 `configured`，不给出明文/指纹/计量身份。"""
    return ModelCredentialConfig(
        id=credential.id,
        key=credential.key,
        provider_key=credential.provider_key,
        base_url=credential.base_url,
        quota_group_id=credential.quota_group_id,
        max_inflight=credential.max_inflight,
        enabled=credential.enabled,
        revision=credential.revision,
        configured=bool(credential.api_key.strip()),
    ).model_dump(mode="json")


def _actor(account: AdminAccount, request: Request) -> AuditActor:
    return AuditActor(account_id=account.id, source_ip=get_source_ip(request))


@router.get("")
async def list_credentials(
    request: Request,
    tenant_id: TenantId,
    session: Session,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> ApiResponse[Any]:
    credentials, total = await ModelCredentialService(session).list_credentials(tenant_id, page, page_size)
    return ok(
        request.app.state.message_catalog,
        paginate(
            items=[_item(credential) for credential in credentials],
            page=page,
            page_size=page_size,
            total=total,
        ),
    )


@router.post("")
async def create_credential(
    payload: ModelCredentialCreate,
    request: Request,
    account: AdminAccount,
    tenant_id: TenantId,
    session: Session,
    idempotency_key: Annotated[str | None, Header(max_length=128)] = None,
) -> ApiResponse[Any]:
    credential = await ModelCredentialService(session).create_credential(
        tenant_id, payload, _actor(account, request), idempotency_key=idempotency_key
    )
    return ok(request.app.state.message_catalog, _item(credential))


@router.get("/{credential_id}")
async def get_credential(
    credential_id: uuid.UUID,
    request: Request,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    credential = await ModelCredentialService(session).get_credential(tenant_id, credential_id)
    return ok(request.app.state.message_catalog, _item(credential))


@router.put("/{credential_id}")
async def update_credential(
    credential_id: uuid.UUID,
    payload: ModelCredentialUpdate,
    request: Request,
    account: AdminAccount,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    credential = await ModelCredentialService(session).update_credential(
        tenant_id,
        credential_id,
        payload,
        payload.expected_revision,
        _actor(account, request),
    )
    return ok(request.app.state.message_catalog, _item(credential))


@router.delete("/{credential_id}")
async def delete_credential(
    credential_id: uuid.UUID,
    request: Request,
    account: AdminAccount,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    await ModelCredentialService(session).delete_credential(
        tenant_id, credential_id, _actor(account, request)
    )
    return ok(request.app.state.message_catalog, {"id": str(credential_id), "deleted": True})
