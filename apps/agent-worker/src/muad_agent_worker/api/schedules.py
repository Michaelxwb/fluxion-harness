from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from muad_api import ApiResponse, ok
from muad_contracts import CreateScheduleRequest
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.db import get_session
from ..infrastructure.models.task import TaskSchedule
from ..scheduler.service import ScheduleService
from .deps import ensure_tenant_consistent, get_tenant_id

router = APIRouter(prefix="/internal/schedules", tags=["schedules"])

TenantId = Annotated[str, Depends(get_tenant_id)]
Session = Annotated[AsyncSession, Depends(get_session)]


def _payload(schedule: TaskSchedule) -> dict[str, Any]:
    return {
        "schedule_id": str(schedule.id),
        "tenant_id": schedule.tenant_id,
        "name": schedule.name,
        "agent_id": str(schedule.agent_id),
        "actor_user_id": str(schedule.actor_user_id),
        "intent_key": schedule.intent_key,
        "skill_id": str(schedule.skill_id),
        "input_template": schedule.input_template_json,
        "schedule_type": schedule.schedule_type,
        "cron_expr": schedule.cron_expr,
        "timezone": schedule.timezone,
        "run_at": schedule.run_at.isoformat() if schedule.run_at else None,
        "status": schedule.status,
        "next_fire_at": schedule.next_fire_at.isoformat() if schedule.next_fire_at else None,
        "last_fire_at": schedule.last_fire_at.isoformat() if schedule.last_fire_at else None,
        "revision": schedule.revision,
        "completed_at": schedule.completed_at.isoformat() if schedule.completed_at else None,
        "create_time": schedule.create_time.isoformat(),
        "update_time": schedule.update_time.isoformat(),
    }


@router.post("")
async def create_schedule(
    body: CreateScheduleRequest,
    request: Request,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    ensure_tenant_consistent(request, tenant_id)
    schedule = await ScheduleService(session).create_schedule(tenant_id, body)
    return ok(request.app.state.message_catalog, _payload(schedule))


@router.get("")
async def list_schedules(
    request: Request,
    tenant_id: TenantId,
    session: Session,
    actor_user_id: Annotated[uuid.UUID | None, Query()] = None,
) -> ApiResponse[Any]:
    schedules = await ScheduleService(session).list_schedules(
        tenant_id,
        actor_user_id=actor_user_id,
    )
    return ok(request.app.state.message_catalog, [_payload(schedule) for schedule in schedules])


@router.put("/{schedule_id}/pause")
async def pause_schedule(
    schedule_id: uuid.UUID,
    request: Request,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    ensure_tenant_consistent(request, tenant_id)
    schedule = await ScheduleService(session).pause_schedule(tenant_id, schedule_id)
    return ok(request.app.state.message_catalog, _payload(schedule))


@router.put("/{schedule_id}/resume")
async def resume_schedule(
    schedule_id: uuid.UUID,
    request: Request,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    ensure_tenant_consistent(request, tenant_id)
    schedule = await ScheduleService(session).resume_schedule(tenant_id, schedule_id)
    return ok(request.app.state.message_catalog, _payload(schedule))


@router.delete("/{schedule_id}")
async def delete_schedule(
    schedule_id: uuid.UUID,
    request: Request,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    ensure_tenant_consistent(request, tenant_id)
    await ScheduleService(session).delete_schedule(tenant_id, schedule_id)
    return ok(request.app.state.message_catalog, {"deleted": True})
