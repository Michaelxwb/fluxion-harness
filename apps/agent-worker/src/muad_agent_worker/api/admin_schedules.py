"""API-17 Worker Admin API（Schedule 部分）。

Console Platform 专用内部接口：与 `/internal/schedules` 走**同一个**
`ScheduleService`（同一锁序、同一状态矩阵），只多一层 Admin 权限域校验；浏览器不直接
访问 Worker。删除语义是软删：只打标记，不删除 Fire/Execution/事件历史。
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from muad_api import ApiResponse, ok, paginate
from muad_api.security import InternalServiceDep
from muad_contracts import ScheduleStatus
from sqlalchemy.ext.asyncio import AsyncSession

from ..application.platform_settings import resolve_platform_settings
from ..application.schedule_admission import ScheduleAdmissionService
from ..application.schedule_lifecycle import ScheduleService
from ..infrastructure.db import get_session
from ..infrastructure.db_clock import database_now
from .deps import (
    PlatformSettingsClientDep,
    ScheduleMaterialsClientDep,
    ensure_tenant_consistent,
    get_tenant_id,
)
from .schedules import _payload, load_schedule, page_schedules

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
    items, total = await page_schedules(
        session,
        tenant_id=tenant_id,
        owner_user_id=actor_user_id,
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
    schedule = await load_schedule(
        session, tenant_id=tenant_id, schedule_id=schedule_id, owner_user_id=None
    )
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
    outcome = await ScheduleService(session, now=await database_now(session)).pause(
        schedule_id, tenant_id=tenant_id, actor_user_id=None
    )
    schedule = outcome.schedule
    assert schedule is not None
    payload = _payload(schedule)
    await session.commit()
    return ok(request.app.state.message_catalog, payload)


@router.put("/{schedule_id}/resume")
async def admin_resume_schedule(
    schedule_id: uuid.UUID,
    request: Request,
    tenant_id: TenantId,
    session: Session,
    _guard: InternalServiceDep,
    materials: ScheduleMaterialsClientDep,
    settings_client: PlatformSettingsClientDep,
) -> ApiResponse[Any]:
    ensure_tenant_consistent(request, tenant_id)
    admission = None
    if materials is not None:
        snapshot = await settings_client.fetch_snapshot(tenant_id=tenant_id)
        admission = ScheduleAdmissionService(
            materials, settings=resolve_platform_settings(snapshot)
        )
    outcome = await ScheduleService(session, now=await database_now(session)).resume(
        schedule_id, tenant_id=tenant_id, actor_user_id=None, admission=admission
    )
    schedule = outcome.schedule
    assert schedule is not None
    payload = _payload(schedule)
    await session.commit()
    return ok(request.app.state.message_catalog, payload)


@router.delete("/{schedule_id}")
async def admin_delete_schedule(
    schedule_id: uuid.UUID,
    request: Request,
    tenant_id: TenantId,
    session: Session,
    _guard: InternalServiceDep,
) -> ApiResponse[Any]:
    ensure_tenant_consistent(request, tenant_id)
    await ScheduleService(session, now=await database_now(session)).soft_delete(
        schedule_id, tenant_id=tenant_id, actor_user_id=None
    )
    await session.commit()
    return ok(
        request.app.state.message_catalog, {"schedule_id": str(schedule_id), "deleted": True}
    )
