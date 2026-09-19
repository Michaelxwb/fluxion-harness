import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from muad_api import ApiResponse, ok, paginate
from muad_platform_sdk import PlatformAdapterRegistry
from sqlalchemy.ext.asyncio import AsyncSession

from ..application.audit_service import AuditActor
from ..application.credential_service import CredentialService
from ..infrastructure.db import get_session
from .deps import CurrentAccount, get_adapter_registry, get_source_ip, get_tenant_id

TenantId = Annotated[str, Depends(get_tenant_id)]
Session = Annotated[AsyncSession, Depends(get_session)]
Registry = Annotated[PlatformAdapterRegistry, Depends(get_adapter_registry)]

router = APIRouter(prefix="/api/v1/project-platforms", tags=["project-platform-credentials"])


def _service(session: Session, registry: Registry) -> CredentialService:
    return CredentialService(session, registry)


def _actor(account: CurrentAccount, request: Request) -> AuditActor:
    return AuditActor(account_id=account.id, source_ip=get_source_ip(request))


@router.get("/{platform_id}/user-credentials")
async def list_user_credentials(
    platform_id: uuid.UUID,
    request: Request,
    tenant_id: TenantId,
    session: Session,
    registry: Registry,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    keyword: str | None = Query(default=None, max_length=128),
) -> ApiResponse[Any]:
    items, total = await _service(session, registry).list_user_credentials(
        tenant_id, platform_id, page, page_size, keyword
    )
    return ok(
        request.app.state.message_catalog,
        paginate(items=items, page=page, page_size=page_size, total=total),
    )


@router.get("/{platform_id}/users/{user_id}/credential")
async def get_user_credential(
    platform_id: uuid.UUID,
    user_id: uuid.UUID,
    request: Request,
    tenant_id: TenantId,
    session: Session,
    registry: Registry,
) -> ApiResponse[Any]:
    return ok(
        request.app.state.message_catalog,
        await _service(session, registry).get_user_credential(tenant_id, platform_id, user_id),
    )


@router.put("/{platform_id}/users/{user_id}/credential")
async def save_user_credential(
    platform_id: uuid.UUID,
    user_id: uuid.UUID,
    payload: dict[str, Any],
    request: Request,
    account: CurrentAccount,
    tenant_id: TenantId,
    session: Session,
    registry: Registry,
) -> ApiResponse[Any]:
    return ok(
        request.app.state.message_catalog,
        await _service(session, registry).save_user_credential(
            tenant_id, platform_id, user_id, payload, _actor(account, request)
        ),
    )


@router.delete("/{platform_id}/users/{user_id}/credential")
async def delete_user_credential(
    platform_id: uuid.UUID,
    user_id: uuid.UUID,
    request: Request,
    account: CurrentAccount,
    tenant_id: TenantId,
    session: Session,
    registry: Registry,
) -> ApiResponse[Any]:
    await _service(session, registry).delete_user_credential(
        tenant_id, platform_id, user_id, _actor(account, request)
    )
    return ok(request.app.state.message_catalog, {})


@router.get("/{platform_id}/shared-credential")
async def get_shared_credential(
    platform_id: uuid.UUID,
    request: Request,
    tenant_id: TenantId,
    session: Session,
    registry: Registry,
) -> ApiResponse[Any]:
    return ok(
        request.app.state.message_catalog,
        await _service(session, registry).get_shared_credential(tenant_id, platform_id),
    )


@router.put("/{platform_id}/shared-credential")
async def save_shared_credential(
    platform_id: uuid.UUID,
    payload: dict[str, Any],
    request: Request,
    account: CurrentAccount,
    tenant_id: TenantId,
    session: Session,
    registry: Registry,
) -> ApiResponse[Any]:
    return ok(
        request.app.state.message_catalog,
        await _service(session, registry).save_shared_credential(
            tenant_id, platform_id, payload, _actor(account, request)
        ),
    )


@router.delete("/{platform_id}/shared-credential")
async def delete_shared_credential(
    platform_id: uuid.UUID,
    request: Request,
    account: CurrentAccount,
    tenant_id: TenantId,
    session: Session,
    registry: Registry,
) -> ApiResponse[Any]:
    await _service(session, registry).delete_shared_credential(
        tenant_id, platform_id, _actor(account, request)
    )
    return ok(request.app.state.message_catalog, {})
