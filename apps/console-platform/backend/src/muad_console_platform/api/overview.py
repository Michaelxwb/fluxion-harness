"""API-01 概览聚合（只读）：`GET /api/v1/overview` 与 `GET /api/v1/overview/metrics`。

请求无 body；会话身份由 Console 登录态承载（未认证由 `UNAUTHORIZED` 兜底）。
`metrics` 为指标图数据（任务趋势/状态分布），`days` 限定 1..30（默认 7）。
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from muad_api import ApiResponse, ok
from sqlalchemy.ext.asyncio import AsyncSession

from ..application.overview_query_service import OverviewQueryService
from ..infrastructure.db import get_session
from .deps import AccountTenantId

TenantId = AccountTenantId
Session = Annotated[AsyncSession, Depends(get_session)]

MIN_METRIC_DAYS = 1
MAX_METRIC_DAYS = 30
DEFAULT_METRIC_DAYS = 7

router = APIRouter(prefix="/api/v1/overview", tags=["overview"])


@router.get("")
async def get_overview(
    request: Request,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    data = await OverviewQueryService(session, tenant_id=tenant_id).get_overview()
    return ok(request.app.state.message_catalog, data)


@router.get("/metrics")
async def get_overview_metrics(
    request: Request,
    tenant_id: TenantId,
    session: Session,
    days: Annotated[int, Query(ge=MIN_METRIC_DAYS, le=MAX_METRIC_DAYS)] = DEFAULT_METRIC_DAYS,
) -> ApiResponse[Any]:
    data = await OverviewQueryService(session, tenant_id=tenant_id).get_metrics(days=days)
    return ok(request.app.state.message_catalog, data)
