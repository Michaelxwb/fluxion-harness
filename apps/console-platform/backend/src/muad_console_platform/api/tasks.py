"""API-09/10/11：Console 任务列表、详情与取消。

Console Platform 只做认证/租户过滤（租户取自登录账号）并转调 Worker Admin API，不直连 task schema。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from muad_api import ApiResponse, ok
from muad_api.context import current_trace_id
from muad_contracts import TaskStatus, TriggerType

from .deps import AccountTenantId as TenantId
from .deps import WorkerClient

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


@router.get("")
async def list_tasks(
    request: Request,
    tenant_id: TenantId,
    worker: WorkerClient,
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
    data = await worker.list_tasks(
        tenant_id=tenant_id,
        trace_id=current_trace_id(),
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
    return ok(request.app.state.message_catalog, data)


@router.get("/{task_id}")
async def get_task(
    task_id: uuid.UUID,
    request: Request,
    tenant_id: TenantId,
    worker: WorkerClient,
) -> ApiResponse[Any]:
    data = await worker.get_task(
        tenant_id=tenant_id, task_id=task_id, trace_id=current_trace_id()
    )
    return ok(request.app.state.message_catalog, data)


@router.post("/{task_id}/cancel")
async def cancel_task(
    task_id: uuid.UUID,
    request: Request,
    tenant_id: TenantId,
    worker: WorkerClient,
) -> ApiResponse[Any]:
    data = await worker.cancel_task(
        tenant_id=tenant_id, task_id=task_id, trace_id=current_trace_id()
    )
    return ok(request.app.state.message_catalog, data)
