from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from muad_api import ApiResponse, ok, paginate
from muad_api.security import require_internal_service
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
from ..application.task_service import TERMINAL_STATUSES, TaskService
from ..infrastructure.db import get_session
from ..infrastructure.models.task import TaskEvent, TaskExecution
from .deps import (
    ActorUserId,
    PlatformSettingsClientDep,
    ensure_tenant_consistent,
    get_tenant_id,
)

# 内部业务路由与 `/internal/admin/*` 同一门控（2026-10-06 评审 #1）：租户与 actor 都来自
# 请求头，只有确认调用方是受信服务（Runtime/Console）才谈得上「可信上下文」——此前这些
# 路由完全无门控，任何能连上 Worker 端口、知道目标租户/任务 id 的调用方都能读、建、取消。
router = APIRouter(
    prefix="/internal/tasks",
    tags=["tasks"],
    dependencies=[Depends(require_internal_service)],
)

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


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _payload(task: TaskExecution, progress: tuple[int, int] | None = None) -> dict[str, Any]:
    """Task 摘要（设计 §3.4「Task 摘要字段」）。

    `progress` 是 `(子任务总数, 已终态数)`；列表接口由 `child_progress()` 一次 GROUP BY
    取回整页，详情接口直接用已加载的 `children` 长度。缺省 0 表示没有子任务。
    """
    return {
        "task_id": str(task.id),
        "tenant_id": task.tenant_id,
        "agent_id": str(task.agent_id),
        "actor_user_id": str(task.actor_user_id),
        "schedule_id": str(task.schedule_id) if task.schedule_id else None,
        "source_run_id": str(task.source_run_id) if task.source_run_id else None,
        "source_operation_id": str(task.source_operation_id) if task.source_operation_id else None,
        "source_tool_call_id": task.source_tool_call_id,
        "snapshot_hash": task.snapshot_hash,
        "intent_key": task.intent_key,
        "skill_id": str(task.skill_id),
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
        "delivery_mode": task.delivery_mode,
        "delivery_status": task.delivery_status,
        "delivery_attempts": task.delivery_attempts,
        "delivered_at": _iso(task.delivered_at),
        "deadline_at": task.deadline_at.isoformat(),
        "not_before": task.not_before.isoformat(),
        "create_time": task.create_time.isoformat(),
        "update_time": task.update_time.isoformat(),
        "started_at": _iso(task.started_at),
        "finished_at": _iso(task.finished_at),
        "child_total": progress[0] if progress else 0,
        "child_finished": progress[1] if progress else 0,
    }


def _event_payload(event: TaskEvent) -> dict[str, Any]:
    return {
        "seq": event.seq,
        "event_type": event.event_type,
        "payload": event.payload_json,
        "trace_id": event.trace_id,
        "create_time": event.create_time.isoformat(),
    }


def _child_payload(task: TaskExecution) -> dict[str, Any]:
    return {
        "task_id": str(task.id),
        "status": task.status,
        "item_key": task.item_key,
        "create_time": task.create_time.isoformat(),
    }


def detail_payload(
    task: TaskExecution, events: list[TaskEvent], children: list[TaskExecution]
) -> dict[str, Any]:
    """Task 详情 = 摘要 + 设计 §3.4「Task 详情额外字段」+ Timeline + 子任务。"""
    payload = _payload(
        task,
        (len(children), sum(1 for child in children if child.status in TERMINAL_STATUSES)),
    )
    payload.update(
        parent_id=str(task.parent_id) if task.parent_id else None,
        root_id=str(task.root_id) if task.root_id else None,
        item_key=task.item_key,
        result_artifact_id=str(task.result_artifact_id) if task.result_artifact_id else None,
        execution_snapshot=task.execution_snapshot_json,
        execution_snapshot_schema_version=task.execution_snapshot_schema_version,
        snapshot_hash=task.snapshot_hash,
        lease_owner=task.lease_owner,
        lease_until=_iso(task.lease_until),
        heartbeat_at=_iso(task.heartbeat_at),
        timeline=[_event_payload(event) for event in events],
        children=[_child_payload(child) for child in children],
    )
    return payload


async def list_payloads(
    service: TaskService, tenant_id: str, items: list[TaskExecution]
) -> list[dict[str, Any]]:
    """整页 Task 摘要，子任务进度由一次 `GROUP BY` 取回（不做逐行查询）。"""
    progress = await service.child_progress(tenant_id, [task.id for task in items])
    return [_payload(task, progress.get(task.id)) for task in items]


async def _publish_cancel_hint(
    request: Request, task_id: uuid.UUID, status: str, cancel_requested: bool
) -> None:
    """RUNNING 协作取消在 PG 提交后写 `task:cancel:{task_id}` hint；失败只告警（PG 仍权威）。"""
    if status != str(TaskStatus.RUNNING) or not cancel_requested:
        return
    hints = getattr(request.app.state, "cancel_hints", None)
    if hints is None:
        return
    try:
        await hints.mark(task_id)
    except Exception:
        logger.warning("task_cancel_hint_failed", extra={"task_id": str(task_id)})


@router.post("")
async def create_task(
    body: CreateTaskRequest,
    request: Request,
    session: Session,
    settings_client: PlatformSettingsClientDep,
) -> ApiResponse[Any]:
    ensure_tenant_consistent(request, body.tenant_id)
    submissions = TaskSubmissionService(session)
    idempotency_key = resolve_idempotency_key(request.headers.get(IDEMPOTENCY_HEADER), body.idempotency_key)
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
            task = await TaskService(session, settings_client=settings_client).create(body)
            response = _admission_payload(task)
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


def _admission_payload(task: TaskExecution) -> dict[str, object]:
    return {
        "task_id": str(task.id),
        "status": task.status,
        "source_run_id": str(task.source_run_id) if task.source_run_id else None,
        "source_operation_id": str(task.source_operation_id) if task.source_operation_id else None,
        "source_tool_call_id": task.source_tool_call_id,
        "actor_user_id": str(task.actor_user_id),
        "snapshot_hash": task.snapshot_hash,
    }


@router.get("")
async def list_tasks(
    request: Request,
    tenant_id: TenantId,
    session: Session,
    caller_actor: ActorUserId,
    status: Annotated[TaskStatus | None, Query()] = None,
    trigger_type: Annotated[TriggerType | None, Query()] = None,
    agent_id: Annotated[uuid.UUID | None, Query()] = None,
    actor_user_id: Annotated[uuid.UUID | None, Query()] = None,
    skill_id: Annotated[uuid.UUID | None, Query()] = None,
    schedule_id: Annotated[uuid.UUID | None, Query()] = None,
    start_time: Annotated[datetime | None, Query()] = None,
    end_time: Annotated[datetime | None, Query()] = None,
    deadline_from: Annotated[datetime | None, Query()] = None,
    deadline_to: Annotated[datetime | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ApiResponse[Any]:
    service = TaskService(session)
    items, total = await service.list(
        tenant_id,
        status=status,
        trigger_type=trigger_type,
        agent_id=agent_id,
        # Runtime 代表用户查询时只能看到自己的 Task，忽略 query 里的 actor。
        actor_user_id=caller_actor or actor_user_id,
        skill_id=skill_id,
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
            items=await list_payloads(service, tenant_id, items),
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
    caller_actor: ActorUserId,
) -> ApiResponse[Any]:
    task, events, children = await TaskService(session).detail(tenant_id, task_id, actor_user_id=caller_actor)
    return ok(request.app.state.message_catalog, detail_payload(task, events, children))


@router.post("/{task_id}/cancel")
async def cancel_task(
    task_id: uuid.UUID,
    request: Request,
    tenant_id: TenantId,
    session: Session,
    caller_actor: ActorUserId,
) -> ApiResponse[Any]:
    status, cancel_requested = await TaskService(session).cancel(
        tenant_id, task_id, actor_user_id=caller_actor
    )
    await session.commit()
    await _publish_cancel_hint(request, task_id, status, cancel_requested)
    return ok(
        request.app.state.message_catalog,
        {"task_id": str(task_id), "status": status, "cancel_requested": cancel_requested},
    )
