"""API-01 概览聚合（只读）：`GET /api/v1/overview`。

请求无 query/body；会话身份由 Console 登录态承载（未认证由 `UNAUTHORIZED` 兜底）。
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from muad_api import ApiResponse, ok
from sqlalchemy.ext.asyncio import AsyncSession

from ..application.overview_query_service import OverviewQueryService
from ..infrastructure.db import get_session
from .deps import get_tenant_id

TenantId = Annotated[str, Depends(get_tenant_id)]
Session = Annotated[AsyncSession, Depends(get_session)]

router = APIRouter(prefix="/api/v1/overview", tags=["overview"])


@router.get("")
async def get_overview(
    request: Request,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    data = await OverviewQueryService(session, tenant_id=tenant_id).get_overview()
    return ok(request.app.state.message_catalog, data)
