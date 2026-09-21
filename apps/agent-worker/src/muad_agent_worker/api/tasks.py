from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from muad_api import ApiResponse, ok, paginate
from muad_contracts import CreateTaskRequest, TaskStatus, TriggerType
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..application.submissions import (
    ENDPOINT_CREATE_TASK,
    IDEMPOTENCY_HEADER,
    TaskSubmissionService,
    resolve_idempotency_key,
    submission_fingerprint,
)
from ..application.task_service import TaskService
from ..infrastructure.db import get_session
from ..infrastructure.models.task import TaskExecution
from .deps import ensure_tenant_consistent, get_tenant_id

router = APIRouter(prefix="/internal/tasks", tags=["tasks"])

logger = logging.getLogger(__name__)

TenantId = Annotated[str, Depends(get_tenant_id)]
Session = Annotated[AsyncSession, Depends(get_session)]


async def _publish_wakeup(request: Request) -> None:
    """提交事务已提交后再发 wakeup hint；失败只告警，不影响任务（PG 扫描仍可推进）。"""
    notifier = getattr(request.app.state, "wakeup_notifier", None)
    if notifier is None:
        return
    try:
        await notifier.notify()
    except Exception:
        logger.warning("task_wakeup_hint_failed")


def _payload(task: TaskExecution) -> dict[str, Any]:
    return {
        "task_id": str(task.id),
        "tenant_id": task.tenant_id,
        "agent_id": str(task.agent_id),
        "actor_user_id": str(task.actor_user_id),
        "schedule_id": str(task.schedule_id) if task.schedule_id else None,
        "source_run_id": str(task.source_run_id) if task.source_run_id else None,
        "intent_key": task.intent_key,
        "status": task.status,
        "trigger_type": task.trigger_type,
        "task_type": task.task_type,
        "input": task.input_json,
        "result": task.result_json,
        "error_code": task.error_code,
        "error_message": task.error_message,
        "attempt": task.attempt,
        "max_attempts": task.max_attempts,
        "cancel_requested": task.cancel_requested,
        "delivery_status": task.delivery_status,
        "delivery_attempts": task.delivery_attempts,
        "deadline_at": task.deadline_at.isoformat(),
        "not_before": task.not_before.isoformat(),
        "create_time": task.create_time.isoformat(),
        "update_time": task.update_time.isoformat(),
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "finished_at": task.finished_at.isoformat() if task.finished_at else None,
    }


@router.post("")
async def create_task(
    body: CreateTaskRequest,
    request: Request,
    session: Session,
) -> ApiResponse[Any]:
    ensure_tenant_consistent(request, body.tenant_id)
    submissions = TaskSubmissionService(session)
    idempotency_key = resolve_idempotency_key(
        request.headers.get(IDEMPOTENCY_HEADER), body.idempotency_key
    )
    fingerprint = submission_fingerprint(ENDPOINT_CREATE_TASK, body)

    async def replay() -> ApiResponse[Any] | None:
        row = await submissions.find_replay(
            tenant_id=body.tenant_id,
            idempotency_key=idempotency_key,
            endpoint=ENDPOINT_CREATE_TASK,
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
            task = await TaskService(session).create(body)
            response = {"task_id": str(task.id), "status": task.status}
            await submissions.record_in(
                tenant_id=body.tenant_id,
                idempotency_key=idempotency_key,
                endpoint=ENDPOINT_CREATE_TASK,
                actor_user_id=body.actor_user_id,
                request_fingerprint=fingerprint,
                response=response,
                task_id=task.id,
            )
    except IntegrityError:
        # 并发落败者：首次提交已由赢家落库，读取其结果重放。
        already = await replay()
        if already is None:
            raise
        return already
    await session.commit()
    await _publish_wakeup(request)
    return ok(request.app.state.message_catalog, response)


@router.get("")
async def list_tasks(
    request: Request,
    tenant_id: TenantId,
    session: Session,
    status: Annotated[TaskStatus | None, Query()] = None,
    trigger_type: Annotated[TriggerType | None, Query()] = None,
    agent_id: Annotated[uuid.UUID | None, Query()] = None,
    actor_user_id: Annotated[uuid.UUID | None, Query()] = None,
    schedule_id: Annotated[uuid.UUID | None, Query()] = None,
    start_time: Annotated[datetime | None, Query()] = None,
    end_time: Annotated[datetime | None, Query()] = None,
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
async def get_task(
    task_id: uuid.UUID,
    request: Request,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    task = await TaskService(session).get(tenant_id, task_id)
    return ok(request.app.state.message_catalog, _payload(task))


@router.post("/{task_id}/cancel")
async def cancel_task(
    task_id: uuid.UUID,
    request: Request,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    status = await TaskService(session).cancel(tenant_id, task_id)
    return ok(request.app.state.message_catalog, {"task_id": str(task_id), "status": status})
