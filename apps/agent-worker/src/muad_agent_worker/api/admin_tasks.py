"""API-17 Worker Admin API（Task 部分）。

Console Platform 专用内部接口：复用 Internal 查询/取消 service，仅增加 Admin
权限域校验；浏览器不直接访问 Worker。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from muad_api import ApiResponse, ok, paginate
from muad_contracts import TaskStatus, TriggerType
from sqlalchemy.ext.asyncio import AsyncSession

from ..application.task_service import TaskService
from ..infrastructure.db import get_session
from .deps import InternalServiceDep, get_tenant_id
from .tasks import _payload, _publish_cancel_hint, detail_payload

router = APIRouter(prefix="/internal/admin/tasks", tags=["admin-tasks"])

TenantId = Annotated[str, Depends(get_tenant_id)]
Session = Annotated[AsyncSession, Depends(get_session)]


@router.get("")
async def admin_list_tasks(
    request: Request,
    tenant_id: TenantId,
    session: Session,
    _guard: InternalServiceDep,
    status: Annotated[TaskStatus | None, Query()] = None,
    trigger_type: Annotated[TriggerType | None, Query()] = None,
    agent_id: Annotated[uuid.UUID | None, Query()] = None,
    actor_user_id: Annotated[uuid.UUID | None, Query()] = None,
    schedule_id: Annotated[uuid.UUID | None, Query()] = None,
    start_time: Annotated[datetime | None, Query()] = None,
    end_time: Annotated[datetime | None, Query()] = None,
    deadline_from: Annotated[datetime | None, Query()] = None,
    deadline_to: Annotated[datetime | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ApiResponse[Any]:
    items, total = await TaskService(session).list(
        tenant_id,
        status=status,
        trigger_type=trigger_type,
        agent_id=agent_id,
        actor_user_id=actor_user_id,
        schedule_id=schedule_id,
        start_time=start_time,
        end_time=end_time,
        deadline_from=deadline_from,
        deadline_to=deadline_to,
        page=page,
        page_size=page_size,
    )
    return ok(
        request.app.state.message_catalog,
        paginate(
            items=[_payload(task) for task in items],
            page=page,
            page_size=page_size,
            total=total,
        ),
    )


@router.get("/{task_id}")
async def admin_get_task(
    task_id: uuid.UUID,
    request: Request,
    tenant_id: TenantId,
    session: Session,
    _guard: InternalServiceDep,
) -> ApiResponse[Any]:
    task, events, children = await TaskService(session).detail(tenant_id, task_id)
    return ok(request.app.state.message_catalog, detail_payload(task, events, children))


@router.post("/{task_id}/cancel")
async def admin_cancel_task(
    task_id: uuid.UUID,
    request: Request,
    tenant_id: TenantId,
    session: Session,
    _guard: InternalServiceDep,
) -> ApiResponse[Any]:
    status, cancel_requested = await TaskService(session).cancel(tenant_id, task_id)
    await session.commit()
    await _publish_cancel_hint(request, task_id, status, cancel_requested)
    return ok(
        request.app.state.message_catalog,
        {"task_id": str(task_id), "status": status, "cancel_requested": cancel_requested},
    )
