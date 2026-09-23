"""API-17 Worker Admin API（Schedule 部分）。

Console Platform 专用内部接口：复用 Internal 查询/启停/删除 service，仅增加
Admin 权限域校验；浏览器不直接访问 Worker。
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from muad_api import ApiResponse, ok, paginate
from muad_contracts import ScheduleStatus
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.db import get_session
from ..scheduler.service import ScheduleService
from .deps import InternalServiceDep, ensure_tenant_consistent, get_tenant_id
from .schedules import _payload

router = APIRouter(prefix="/internal/admin/schedules", tags=["admin-schedules"])

TenantId = Annotated[str, Depends(get_tenant_id)]
Session = Annotated[AsyncSession, Depends(get_session)]


@router.get("")
async def admin_list_schedules(
    request: Request,
    tenant_id: TenantId,
    session: Session,
    _guard: InternalServiceDep,
    actor_user_id: Annotated[uuid.UUID | None, Query()] = None,
    agent_id: Annotated[uuid.UUID | None, Query()] = None,
    status: Annotated[ScheduleStatus | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ApiResponse[Any]:
    items, total = await ScheduleService(session).list_schedules(
        tenant_id,
        actor_user_id=actor_user_id,
        agent_id=agent_id,
        status=status,
        page=page,
        page_size=page_size,
    )
    return ok(
        request.app.state.message_catalog,
        paginate(
            items=[_payload(schedule) for schedule in items],
            page=page,
            page_size=page_size,
            total=total,
        ),
    )


@router.get("/{schedule_id}")
async def admin_get_schedule(
    schedule_id: uuid.UUID,
    request: Request,
    tenant_id: TenantId,
    session: Session,
    _guard: InternalServiceDep,
) -> ApiResponse[Any]:
    schedule = await ScheduleService(session).get_schedule(tenant_id, schedule_id)
    return ok(request.app.state.message_catalog, _payload(schedule))


@router.put("/{schedule_id}/pause")
async def admin_pause_schedule(
    schedule_id: uuid.UUID,
    request: Request,
    tenant_id: TenantId,
    session: Session,
    _guard: InternalServiceDep,
) -> ApiResponse[Any]:
    ensure_tenant_consistent(request, tenant_id)
    schedule = await ScheduleService(session).pause_schedule(tenant_id, schedule_id)
    return ok(request.app.state.message_catalog, _payload(schedule))


@router.put("/{schedule_id}/resume")
async def admin_resume_schedule(
    schedule_id: uuid.UUID,
    request: Request,
    tenant_id: TenantId,
    session: Session,
    _guard: InternalServiceDep,
) -> ApiResponse[Any]:
    ensure_tenant_consistent(request, tenant_id)
    schedule = await ScheduleService(session).resume_schedule(tenant_id, schedule_id)
    return ok(request.app.state.message_catalog, _payload(schedule))


@router.delete("/{schedule_id}")
async def admin_delete_schedule(
    schedule_id: uuid.UUID,
    request: Request,
    tenant_id: TenantId,
    session: Session,
    _guard: InternalServiceDep,
) -> ApiResponse[Any]:
    ensure_tenant_consistent(request, tenant_id)
    await ScheduleService(session).delete_schedule(tenant_id, schedule_id)
    return ok(
        request.app.state.message_catalog, {"schedule_id": str(schedule_id), "deleted": True}
    )
