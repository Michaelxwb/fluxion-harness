from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request
from muad_api import ApiResponse, ok
from muad_contracts import ResolveDefinitionRequest
from sqlalchemy.ext.asyncio import AsyncSession

from ..application.resolve_egress_service import resolve_egress_access
from ..application.resolve_service import ResolveService
from ..application.runtime_credentials import (
    require_service_identity,
    resolve_runtime_credentials,
)
from ..infrastructure.db import get_session
from ..metrics import RESOLVE_DEFINITION_METRIC, count_outcome
from .deps import get_tenant_id

TenantId = Annotated[str, Depends(get_tenant_id)]
Session = Annotated[AsyncSession, Depends(get_session)]

router = APIRouter(prefix="/internal/runtime", tags=["internal-runtime"])


@router.post("/resolve-definition")
async def resolve_definition(
    payload: ResolveDefinitionRequest,
    request: Request,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    with count_outcome(RESOLVE_DEFINITION_METRIC):
        resolved = await ResolveService(session).resolve_definition(tenant_id, payload)
    return ok(request.app.state.message_catalog, resolved.model_dump(mode="json"))


@router.post("/resolve-credentials")
async def resolve_credentials(
    payload: dict[str, Any],
    request: Request,
    tenant_id: TenantId,
    session: Session,
    x_internal_service: Annotated[str | None, Header()] = None,
) -> ApiResponse[Any]:
    """API-09：仅受信 Runtime 可调用；按冻结主键读取当前认证值。"""
    require_service_identity(x_internal_service)
    data = await resolve_runtime_credentials(session, tenant_id, payload)
    return ok(request.app.state.message_catalog, data)


@router.post("/resolve-egress-access")
async def resolve_egress(
    payload: dict[str, Any],
    request: Request,
    tenant_id: TenantId,
    session: Session,
    x_internal_service: Annotated[str | None, Header()] = None,
) -> ApiResponse[Any]:
    """API-08：Egress Boundary 的平台解析、凭据选择与策略判定。"""
    require_service_identity(x_internal_service)
    data = await resolve_egress_access(session, tenant_id, payload)
    return ok(request.app.state.message_catalog, data)
