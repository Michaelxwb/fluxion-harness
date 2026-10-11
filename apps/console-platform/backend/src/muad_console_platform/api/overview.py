"""Authenticated, tenant-scoped, read-only Overview snapshot."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from muad_api import ApiResponse, ok
from sqlalchemy.ext.asyncio import AsyncSession

from ..application.overview_query_service import OverviewQueryService
from ..infrastructure.db import get_session
from ..infrastructure.repositories.overview_models import OverviewPayload
from .deps import AccountTenantId

Session = Annotated[AsyncSession, Depends(get_session)]
router = APIRouter(prefix="/api/v1/overview", tags=["overview"])


@router.get("")
async def get_overview(
    request: Request,
    tenant_id: AccountTenantId,
    session: Session,
    days: Annotated[int, Query(ge=1, le=30)] = 7,
) -> ApiResponse[OverviewPayload]:
    data = await OverviewQueryService(session, tenant_id=tenant_id).get_overview(days=days)
    return ok(request.app.state.message_catalog, data)
