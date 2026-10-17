import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from muad_api import ApiResponse, ok, paginate
from muad_contracts.model_pool import (
    ModelDeploymentConfig,
    ModelDeploymentInput,
    ModelDeploymentUpdate,
)
from sqlalchemy.ext.asyncio import AsyncSession

from ..application.audit_service import AuditActor
from ..application.dto import ModelBatchTestRequest, ModelCreateRequest, ModelListItem, ModelUpdateRequest
from ..application.model_deployment_service import ModelDeploymentService
from ..application.model_service import ModelService, model_configured
from ..application.model_test_service import ModelTestService
from ..infrastructure.db import get_session
from ..infrastructure.models.control import ModelDefinition, ModelDeployment
from .deps import AccountTenantId, AdminAccount, CurrentAccount, get_source_ip

TenantId = AccountTenantId
Session = Annotated[AsyncSession, Depends(get_session)]

router = APIRouter(prefix="/api/v1/models", tags=["models"])


def _item(model: ModelDefinition) -> dict[str, Any]:
    return ModelListItem(
        id=model.id,
        key=model.key,
        name=model.name,
        protocol=model.protocol,
        model_id=model.model_id,
        base_url=model.base_url,
        api_key_configured=model_configured(model),
        params=model.params_json,
        revision=model.revision,
        enabled=model.enabled,
        last_test_status=model.last_test_status,
        last_test_at=model.last_test_at,
        create_time=model.create_time,
        update_time=model.update_time,
    ).model_dump(mode="json")


def _actor(account: CurrentAccount, request: Request) -> AuditActor:
    return AuditActor(account_id=account.id, source_ip=get_source_ip(request))


def _deployment_item(deployment: ModelDeployment) -> dict[str, Any]:
    """成员读取投影：关系字段 + revision；不含任何密钥/连接字段。"""
    return ModelDeploymentConfig(
        id=deployment.id,
        key=deployment.key,
        model_id=deployment.model_id,
        credential_id=deployment.credential_id,
        weight=deployment.weight,
        enabled=deployment.enabled,
        revision=deployment.revision,
    ).model_dump(mode="json")


@router.get("")
async def list_models(
    request: Request,
    tenant_id: TenantId,
    session: Session,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    keyword: str | None = Query(default=None, max_length=128),
    enabled: bool | None = Query(default=None),
    last_test_status: str | None = Query(default=None, pattern="^(UNTESTED|AVAILABLE|FAILED)$"),
) -> ApiResponse[Any]:
    models, total = await ModelService(session).list_models(
        tenant_id, page, page_size, keyword, enabled, last_test_status
    )
    return ok(
        request.app.state.message_catalog,
        paginate(
            items=[_item(model) for model in models],
            page=page,
            page_size=page_size,
            total=total,
        ),
    )


@router.post("")
async def create_model(
    payload: ModelCreateRequest,
    request: Request,
    account: CurrentAccount,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    model = await ModelService(session).create_model(tenant_id, payload, _actor(account, request))
    return ok(request.app.state.message_catalog, _item(model))


@router.post("/batch-test")
async def batch_test_models(
    payload: ModelBatchTestRequest,
    request: Request,
    account: CurrentAccount,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    items = await ModelTestService(session).batch_test(tenant_id, payload.model_ids)
    return ok(request.app.state.message_catalog, {"items": items})


@router.get("/{model_id}")
async def get_model(
    model_id: uuid.UUID,
    request: Request,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    model = await ModelService(session).get_model(tenant_id, model_id)
    return ok(request.app.state.message_catalog, _item(model))


@router.put("/{model_id}")
async def update_model(
    model_id: uuid.UUID,
    payload: ModelUpdateRequest,
    request: Request,
    account: CurrentAccount,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    model = await ModelService(session).update_model(
        tenant_id, model_id, payload, _actor(account, request)
    )
    return ok(request.app.state.message_catalog, _item(model))


@router.delete("/{model_id}")
async def delete_model(
    model_id: uuid.UUID,
    request: Request,
    account: CurrentAccount,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    await ModelService(session).delete_model(tenant_id, model_id, _actor(account, request))
    return ok(request.app.state.message_catalog, {"id": str(model_id), "deleted": True})


# ------------------------------------------------- API-01 成员关系（ADMIN；单关系 POST/PUT/DELETE）
@router.get("/{model_id}/deployments")
async def list_model_deployments(
    model_id: uuid.UUID,
    request: Request,
    account: AdminAccount,
    tenant_id: TenantId,
    session: Session,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> ApiResponse[Any]:
    deployments, total = await ModelDeploymentService(session).list_deployments(
        tenant_id, model_id, page, page_size
    )
    return ok(
        request.app.state.message_catalog,
        paginate(
            items=[_deployment_item(deployment) for deployment in deployments],
            page=page,
            page_size=page_size,
            total=total,
        ),
    )


@router.post("/{model_id}/deployments")
async def create_model_deployment(
    model_id: uuid.UUID,
    payload: ModelDeploymentInput,
    request: Request,
    account: AdminAccount,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    deployment = await ModelDeploymentService(session).create_deployment(
        tenant_id, model_id, payload, _actor(account, request)
    )
    return ok(request.app.state.message_catalog, _deployment_item(deployment))


@router.put("/{model_id}/deployments/{deployment_id}")
async def update_model_deployment(
    model_id: uuid.UUID,
    deployment_id: uuid.UUID,
    payload: ModelDeploymentUpdate,
    request: Request,
    account: AdminAccount,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    deployment = await ModelDeploymentService(session).update_deployment(
        tenant_id, model_id, deployment_id, payload, _actor(account, request)
    )
    return ok(request.app.state.message_catalog, _deployment_item(deployment))


@router.delete("/{model_id}/deployments/{deployment_id}")
async def delete_model_deployment(
    model_id: uuid.UUID,
    deployment_id: uuid.UUID,
    request: Request,
    account: AdminAccount,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    await ModelDeploymentService(session).delete_deployment(
        tenant_id, model_id, deployment_id, _actor(account, request)
    )
    return ok(request.app.state.message_catalog, {"id": str(deployment_id), "deleted": True})
