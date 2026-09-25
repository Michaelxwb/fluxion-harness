"""API-03/API-04 Admin Run 列表与详情（Console 审计查询面出站调用的 Runtime 内部端点）。

Console Browser 不直接访问 `/internal/*`：本路由器复用 api-kit 的内部服务身份口径
（`X-Internal-Service`，未配置或身份不符一律 FORBIDDEN），跨服务凭据不进响应。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from muad_api import ApiResponse, InternalServiceDep, ok, paginate
from muad_contracts import RunStatus

from ..application.admin_run_service import AdminRunService
from ..infrastructure.admin_run_repository import AdminRunFilters
from .deps import SessionDep, TenantDep

router = APIRouter(prefix="/internal/admin/runs", tags=["admin-runs"])

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 20


@router.get("")
async def admin_list_runs(
    request: Request,
    tenant_id: TenantDep,
    session: SessionDep,
    _guard: InternalServiceDep,
    agent_id: Annotated[uuid.UUID | None, Query()] = None,
    user_id: Annotated[uuid.UUID | None, Query()] = None,
    skill_id: Annotated[uuid.UUID | None, Query()] = None,
    status: Annotated[RunStatus | None, Query()] = None,
    start_time: Annotated[datetime | None, Query()] = None,
    end_time: Annotated[datetime | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> ApiResponse[Any]:
    items, total = await AdminRunService(session).list_runs(
        tenant_id,
        AdminRunFilters(
            agent_id=agent_id,
            user_id=user_id,
            skill_id=skill_id,
            status=status,
            start_time=start_time,
            end_time=end_time,
        ),
        page=page,
        page_size=page_size,
    )
    return ok(
        request.app.state.message_catalog,
        paginate(items=items, page=page, page_size=page_size, total=total),
    )


@router.get("/{run_id}")
async def admin_get_run(
    run_id: uuid.UUID,
    request: Request,
    tenant_id: TenantDep,
    session: SessionDep,
    _guard: InternalServiceDep,
) -> ApiResponse[Any]:
    detail = await AdminRunService(session).get_run_detail(tenant_id, run_id)
    return ok(request.app.state.message_catalog, detail)
