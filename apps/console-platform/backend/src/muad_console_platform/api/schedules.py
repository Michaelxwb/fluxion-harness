"""API-12 ~ API-16：Console 定时任务查询与管理。

Console Platform 只做认证/租户过滤（租户取自登录账号）并转调 Worker Admin API；历史 Task 继续走
`GET /api/v1/tasks?schedule_id=`。
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from muad_api import ApiResponse, ok
from muad_api.context import current_trace_id
from muad_contracts import ScheduleStatus
from sqlalchemy.ext.asyncio import AsyncSession

from ..application.entity_name_service import EntityNameService
from ..infrastructure.db import get_session
from .deps import AccountTenantId as TenantId
from .deps import WorkerClient

router = APIRouter(prefix="/api/v1/schedules", tags=["schedules"])

Session = Annotated[AsyncSession, Depends(get_session)]


@router.get("")
async def list_schedules(
    request: Request,
    tenant_id: TenantId,
    worker: WorkerClient,
    session: Session,
    actor_user_id: Annotated[uuid.UUID | None, Query()] = None,
    agent_id: Annotated[uuid.UUID | None, Query()] = None,
    status: Annotated[ScheduleStatus | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ApiResponse[Any]:
    data = await worker.list_schedules(
        tenant_id=tenant_id,
        trace_id=current_trace_id(),
        actor_user_id=actor_user_id,
        agent_id=agent_id,
        status=status,
        page=page,
        page_size=page_size,
    )
    data = await EntityNameService(session).enrich(tenant_id, data)
    return ok(request.app.state.message_catalog, data)


@router.get("/{schedule_id}")
async def get_schedule(
    schedule_id: uuid.UUID,
    request: Request,
    tenant_id: TenantId,
    worker: WorkerClient,
) -> ApiResponse[Any]:
    data = await worker.get_schedule(
        tenant_id=tenant_id, schedule_id=schedule_id, trace_id=current_trace_id()
    )
    return ok(request.app.state.message_catalog, data)


@router.put("/{schedule_id}/pause")
async def pause_schedule(
    schedule_id: uuid.UUID,
    request: Request,
    tenant_id: TenantId,
    worker: WorkerClient,
) -> ApiResponse[Any]:
    data = await worker.pause_schedule(
        tenant_id=tenant_id, schedule_id=schedule_id, trace_id=current_trace_id()
    )
    return ok(request.app.state.message_catalog, data)


@router.put("/{schedule_id}/resume")
async def resume_schedule(
    schedule_id: uuid.UUID,
    request: Request,
    tenant_id: TenantId,
    worker: WorkerClient,
) -> ApiResponse[Any]:
    data = await worker.resume_schedule(
        tenant_id=tenant_id, schedule_id=schedule_id, trace_id=current_trace_id()
    )
    return ok(request.app.state.message_catalog, data)


@router.delete("/{schedule_id}")
async def delete_schedule(
    schedule_id: uuid.UUID,
    request: Request,
    tenant_id: TenantId,
    worker: WorkerClient,
) -> ApiResponse[Any]:
    data = await worker.delete_schedule(
        tenant_id=tenant_id, schedule_id=schedule_id, trace_id=current_trace_id()
    )
    return ok(request.app.state.message_catalog, data)
