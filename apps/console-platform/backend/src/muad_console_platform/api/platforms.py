import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from muad_api import ApiResponse, ok, paginate
from muad_platform_sdk import PlatformAdapterRegistry
from sqlalchemy.ext.asyncio import AsyncSession

from ..application.audit_service import AuditActor
from ..application.dto import PlatformCreateRequest, PlatformUpdateRequest
from ..application.platform_ports import PlatformSessionInvalidator
from ..application.platform_service import PlatformService
from ..infrastructure.db import get_session
from .deps import CurrentAccount, get_adapter_registry, get_platform_sessions, get_source_ip, get_tenant_id

TenantId = Annotated[str, Depends(get_tenant_id)]
Session = Annotated[AsyncSession, Depends(get_session)]
Registry = Annotated[PlatformAdapterRegistry, Depends(get_adapter_registry)]
Sessions = Annotated[PlatformSessionInvalidator, Depends(get_platform_sessions)]

router = APIRouter(prefix="/api/v1/project-platforms", tags=["project-platforms"])


def _service(session: Session, registry: Registry, sessions: Sessions) -> PlatformService:
    return PlatformService(session, registry, sessions)


def _actor(account: CurrentAccount, request: Request) -> AuditActor:
    return AuditActor(account_id=account.id, source_ip=get_source_ip(request))


@router.get("")
async def list_platforms(
    request: Request,
    tenant_id: TenantId,
    session: Session,
    registry: Registry,
    sessions: Sessions,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    keyword: str | None = Query(default=None, max_length=128),
    adapter_key: str | None = Query(default=None, max_length=128),
    enabled: bool | None = Query(default=None),
    user_id: Annotated[uuid.UUID | None, Query()] = None,
) -> ApiResponse[Any]:
    items, total = await _service(session, registry, sessions).list_platforms(
        tenant_id, page, page_size, keyword, adapter_key, enabled, user_id
    )
    return ok(
        request.app.state.message_catalog,
        paginate(items=items, page=page, page_size=page_size, total=total),
    )


@router.post("")
async def create_platform(
    payload: PlatformCreateRequest,
    request: Request,
    account: CurrentAccount,
    tenant_id: TenantId,
    session: Session,
    registry: Registry,
    sessions: Sessions,
) -> ApiResponse[Any]:
    platform = await _service(session, registry, sessions).create(
        tenant_id, payload, _actor(account, request)
    )
    return ok(request.app.state.message_catalog, {"platform_id": str(platform.id)})


@router.get("/{platform_id}")
async def get_platform(
    platform_id: uuid.UUID,
    request: Request,
    tenant_id: TenantId,
    session: Session,
    registry: Registry,
    sessions: Sessions,
) -> ApiResponse[Any]:
    return ok(
        request.app.state.message_catalog,
        await _service(session, registry, sessions).get_detail(tenant_id, platform_id),
    )


@router.put("/{platform_id}")
async def update_platform(
    platform_id: uuid.UUID,
    payload: PlatformUpdateRequest,
    request: Request,
    account: CurrentAccount,
    tenant_id: TenantId,
    session: Session,
    registry: Registry,
    sessions: Sessions,
) -> ApiResponse[Any]:
    platform, adapter_changed = await _service(session, registry, sessions).update(
        tenant_id, platform_id, payload, _actor(account, request)
    )
    return ok(
        request.app.state.message_catalog,
        {
            "platform_id": str(platform.id),
            "credential_reconfigure_required": adapter_changed,
        },
    )


@router.delete("/{platform_id}")
async def delete_platform(
    platform_id: uuid.UUID,
    request: Request,
    account: CurrentAccount,
    tenant_id: TenantId,
    session: Session,
    registry: Registry,
    sessions: Sessions,
) -> ApiResponse[Any]:
    await _service(session, registry, sessions).delete(
        tenant_id, platform_id, _actor(account, request)
    )
    return ok(request.app.state.message_catalog, {})
