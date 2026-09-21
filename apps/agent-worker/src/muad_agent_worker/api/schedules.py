from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from muad_api import ApiResponse, ok, paginate
from muad_contracts import CreateScheduleRequest, ScheduleStatus, UpdateScheduleRequest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..application.submissions import (
    ENDPOINT_CREATE_SCHEDULE,
    IDEMPOTENCY_HEADER,
    TaskSubmissionService,
    submission_fingerprint,
)
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
    service = ScheduleService(session)
    idempotency_key = request.headers.get(IDEMPOTENCY_HEADER)
    if not idempotency_key:
        schedule = await service.create_schedule(tenant_id, body)
        return ok(request.app.state.message_catalog, _payload(schedule))

    submissions = TaskSubmissionService(session)
    fingerprint = submission_fingerprint(ENDPOINT_CREATE_SCHEDULE, body)

    async def replay() -> ApiResponse[Any] | None:
        row = await submissions.find_replay(
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            endpoint=ENDPOINT_CREATE_SCHEDULE,
            fingerprint=fingerprint,
        )
        if row is None:
            return None
        return ok(request.app.state.message_catalog, row.response_json)

    already = await replay()
    if already is not None:
        return already

    try:
        async with session.begin_nested():
            schedule = await service.create_schedule(tenant_id, body)
            response = _payload(schedule)
            await submissions.record_in(
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
                endpoint=ENDPOINT_CREATE_SCHEDULE,
                actor_user_id=body.actor_user_id,
                request_fingerprint=fingerprint,
                response=response,
                schedule_id=schedule.id,
            )
    except IntegrityError:
        # 并发落败者：读取赢家已提交的首次结果重放。
        already = await replay()
        if already is None:
            raise
        return already
    await session.commit()
    return ok(request.app.state.message_catalog, response)


@router.put("/{schedule_id}")
async def update_schedule(
    schedule_id: uuid.UUID,
    body: UpdateScheduleRequest,
    request: Request,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    ensure_tenant_consistent(request, tenant_id)
    schedule = await ScheduleService(session).update_schedule(tenant_id, schedule_id, body)
    return ok(request.app.state.message_catalog, _payload(schedule))


@router.get("")
async def list_schedules(
    request: Request,
    tenant_id: TenantId,
    session: Session,
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
async def get_schedule(
    schedule_id: uuid.UUID,
    request: Request,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    schedule = await ScheduleService(session).get_schedule(tenant_id, schedule_id)
    return ok(request.app.state.message_catalog, _payload(schedule))


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
