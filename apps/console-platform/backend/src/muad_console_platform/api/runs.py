"""Run 详情与关联操作 API（Console 只读，API-05/06）。

Console **不直连 `task.*`**（任务与调度一律转调 Worker Admin API），但 Run 属 `runtime` 且
Runtime 没有对外读接口；`harness-data.md` 允许对跨 Owner Schema 做**只读聚合投影**
（`audit_query_repository` / `overview_query_repository` 是先例），本端点即第三例。
租户一律取登录账号（`AccountTenantId`），不信任浏览器可改写的 `X-Tenant-Id`。
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from muad_api import ApiResponse, ok, paginate
from sqlalchemy.ext.asyncio import AsyncSession

from ..application.run_query_service import RunQueryService
from ..infrastructure.db import get_session
from .deps import AccountTenantId

TenantId = AccountTenantId
Session = Annotated[AsyncSession, Depends(get_session)]

router = APIRouter(prefix="/api/v1/runs", tags=["runs"])

#: API-06：默认 15、上限 100（与前端分页契约一致）。
OPERATIONS_DEFAULT_PAGE_SIZE = 15
OPERATIONS_MAX_PAGE_SIZE = 100


@router.get("/{run_id}")
async def get_run(
    run_id: uuid.UUID,
    request: Request,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    data = await RunQueryService(session).get_run(tenant_id, run_id)
    return ok(request.app.state.message_catalog, data)


@router.get("/{run_id}/operations")
async def list_run_operations(
    run_id: uuid.UUID,
    request: Request,
    tenant_id: TenantId,
    session: Session,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=OPERATIONS_DEFAULT_PAGE_SIZE, ge=1, le=OPERATIONS_MAX_PAGE_SIZE),
) -> ApiResponse[Any]:
    items, total = await RunQueryService(session).list_operations(
        tenant_id, run_id, page=page, page_size=page_size
    )
    return ok(
        request.app.state.message_catalog,
        paginate(items=items, page=page, page_size=page_size, total=total),
    )
