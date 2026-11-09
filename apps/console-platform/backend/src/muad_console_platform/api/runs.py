"""Run 详情与关联操作 API（Console 只读，API-05/06）。

任务与调度管理一律转调 Worker Admin API；Run 详情允许只读关联任务状态。
Run 属 `runtime` 且 Runtime 没有对外读接口；`harness-data.md` 允许对跨 Owner Schema
做只读聚合投影（`audit_query_repository` / `overview_query_repository` 是先例）。
租户一律取登录账号（`AccountTenantId`），不信任浏览器可改写的 `X-Tenant-Id`。
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from muad_api import ApiResponse, ok, paginate
from muad_api.context import current_locale
from sqlalchemy.ext.asyncio import AsyncSession

from ..application.execution_export_service import ExecutionExportService
from ..application.run_event_query_service import RunEventQueryService
from ..application.run_event_view import RunEventDetail
from ..application.run_query_service import RunQueryService
from ..infrastructure.db import get_session
from .deps import AccountTenantId, AdminAccount
from .execution_export import execution_export_response

TenantId = AccountTenantId
Session = Annotated[AsyncSession, Depends(get_session)]

router = APIRouter(prefix="/api/v1/runs", tags=["runs"])

#: API-06：默认 15、上限 100（与前端分页契约一致）。
OPERATIONS_DEFAULT_PAGE_SIZE = 15
OPERATIONS_MAX_PAGE_SIZE = 100


@router.get("/{run_id}/events/{seq}")
async def get_run_event(
    run_id: uuid.UUID,
    request: Request,
    tenant_id: TenantId,
    session: Session,
    seq: Annotated[int, Path(ge=1)],
) -> ApiResponse[RunEventDetail]:
    data = await RunEventQueryService(session).get_event(tenant_id, run_id, seq)
    return ok(request.app.state.message_catalog, data)


@router.get("/{run_id}/export")
async def export_run(
    run_id: uuid.UUID,
    tenant_id: TenantId,
    _account: AdminAccount,
    session: Session,
    export_format: str = Query(default="zip", alias="format"),
) -> Response:
    result = await ExecutionExportService(session).export_run(
        tenant_id, run_id, export_format, locale=current_locale()
    )
    return execution_export_response(result)


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
