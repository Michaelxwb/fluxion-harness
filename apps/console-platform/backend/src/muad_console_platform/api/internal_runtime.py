from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from muad_api import ApiResponse, ok
from muad_contracts import ResolveDefinitionRequest
from sqlalchemy.ext.asyncio import AsyncSession

from ..application.resolve_service import ResolveService
from ..infrastructure.db import get_session
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
    resolved = await ResolveService(session).resolve_definition(tenant_id, payload)
    return ok(request.app.state.message_catalog, resolved.model_dump(mode="json"))
