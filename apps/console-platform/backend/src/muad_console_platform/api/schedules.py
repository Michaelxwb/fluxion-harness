"""API-20 ~ API-23：Console 定时任务查看与运营动作。

Console Platform 只做认证/租户过滤（租户取自登录账号）并把 Worker 真值投影成前端出参；
不直连 task schema，不提供创建或用户意图编辑入口。历史 Task 继续走 `GET /api/v1/tasks`。
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from muad_api import ApiResponse, AppError, ok
from muad_api.context import current_trace_id
from muad_api.error_codes import ErrorCode
from muad_contracts import ScheduleActionRequest, ScheduleStatus
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from ..application import schedule_view
from ..application.entity_name_service import EntityNameService
from ..infrastructure.db import get_session
from ..infrastructure.worker_client import IDEMPOTENCY_HEADER
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
    data = {
        **data,
        "items": [schedule_view.schedule_item(item) for item in data.get("items") or []],
    }
    data = await EntityNameService(session).enrich(tenant_id, data)
    return ok(request.app.state.message_catalog, data)


@router.get("/{schedule_id}")
async def get_schedule(
    schedule_id: uuid.UUID,
    request: Request,
    tenant_id: TenantId,
    worker: WorkerClient,
    session: Session,
) -> ApiResponse[Any]:
    data = await worker.get_schedule(
        tenant_id=tenant_id, schedule_id=schedule_id, trace_id=current_trace_id()
    )
    data = schedule_view.schedule_detail(data)
    enriched = await EntityNameService(session).enrich(tenant_id, {"items": [data]})
    return ok(request.app.state.message_catalog, enriched["items"][0])


@router.get("/{schedule_id}/fires")
async def list_schedule_fires(
    schedule_id: uuid.UUID,
    request: Request,
    tenant_id: TenantId,
    worker: WorkerClient,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ApiResponse[Any]:
    data = await worker.list_schedule_fires(
        tenant_id=tenant_id,
        schedule_id=schedule_id,
        trace_id=current_trace_id(),
        page=page,
        page_size=page_size,
    )
    return ok(request.app.state.message_catalog, schedule_view.fire_history(data))


def _action_request(body: dict[str, Any]) -> ScheduleActionRequest:
    try:
        return ScheduleActionRequest.model_validate(body)
    except ValidationError as exc:
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR) from exc


@router.post("/{schedule_id}/actions")
async def run_schedule_action(
    schedule_id: uuid.UUID,
    body: dict[str, Any],
    request: Request,
    tenant_id: TenantId,
    worker: WorkerClient,
) -> ApiResponse[Any]:
    """API-23：pause|resume|cancel 的唯一入口（旧 `PUT pause/resume` 已移除，无双路由）。

    Worker 的动作端点按 owner 复核可见性，而 Console 的授权范围是**同租户**：先读 owner
    （Worker 真值）再代表它发起动作，同租户可见性因此与旧行为一致；跨租户仍是 404。
    """
    action = _action_request(body)
    # Worker 的动作端点要求幂等键：透传调用方的键（同键同指纹重放），缺省时每次生成新键。
    idempotency_key = request.headers.get(IDEMPOTENCY_HEADER) or uuid.uuid4().hex
    owner_user_id = await worker.schedule_owner(
        tenant_id=tenant_id, schedule_id=schedule_id, trace_id=current_trace_id()
    )
    data = await worker.run_schedule_action(
        tenant_id=tenant_id,
        schedule_id=schedule_id,
        actor_user_id=owner_user_id,
        action=action.action,
        body={
            "action": action.action,
            "scope": str(action.scope) if action.scope is not None else None,
            "reason": action.reason,
        },
        idempotency_key=idempotency_key,
        trace_id=current_trace_id(),
    )
    return ok(request.app.state.message_catalog, schedule_view.action_result(data))


@router.delete("/{schedule_id}")
async def delete_schedule(
    schedule_id: uuid.UUID,
    request: Request,
    tenant_id: TenantId,
    worker: WorkerClient,
) -> ApiResponse[Any]:
    """管理员清理：只打软删标记，Fire/Execution/事件历史保留（Worker 保证）。"""
    data = await worker.delete_schedule(
        tenant_id=tenant_id, schedule_id=schedule_id, trace_id=current_trace_id()
    )
    return ok(request.app.state.message_catalog, data)
