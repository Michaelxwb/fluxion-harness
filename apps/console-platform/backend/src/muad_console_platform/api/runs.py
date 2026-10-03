"""Run 详情 API（Console 只读）。

Console **不直连 `task.*`**（任务与调度一律转调 Worker Admin API），但 Run 属 `runtime` 且
Runtime 没有对外读接口；`harness-data.md` 允许对跨 Owner Schema 做**只读聚合投影**
（`audit_query_repository` / `overview_query_repository` 是先例），本端点即第三例。
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from muad_api import ApiResponse, ok
from sqlalchemy.ext.asyncio import AsyncSession

from ..application.run_query_service import RunQueryService
from ..infrastructure.db import get_session
from .deps import AccountTenantId

TenantId = AccountTenantId
Session = Annotated[AsyncSession, Depends(get_session)]

router = APIRouter(prefix="/api/v1/runs", tags=["runs"])


@router.get("/{run_id}")
async def get_run(
    run_id: uuid.UUID,
    request: Request,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    data = await RunQueryService(session).get_run(tenant_id, run_id)
    return ok(request.app.state.message_catalog, data)
