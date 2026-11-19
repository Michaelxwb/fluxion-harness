"""API-04/05/06/07/08：Schedule 生命周期动作与有界读取（design §3.4.2 / §3.4.5）。

创建/更新不在这里：API-02/03 已被 API-01(Draft)+API-27(确认) 取代，旧的
`POST/PUT /internal/schedules` 一并退役（Draft 是唯一的配置载体）。

所有生命周期动作都带 `Idempotency-Key`（endpoint `schedule-actions`），先经
`task_submission` 的 advisory 锁串行化与首次响应重放，再进 `ScheduleService`。
远端 operation 取消的网络调用在事务提交之后，见 `application/remote_cancellation.py`。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from muad_api import ApiResponse, AppError, ok, paginate
from muad_api.error_codes import ErrorCode
from muad_api.security import require_internal_service
from muad_contracts import CancelScope, ScheduleActionRequest, ScheduleStatus
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..application.platform_settings import resolve_platform_settings
from ..application.ports import PlatformSettingsClient
from ..application.remote_cancellation import ToolOperationCanceller, dispatch_remote_cancels
from ..application.schedule_admission import ScheduleAdmissionService
from ..application.schedule_lifecycle import (
    AdmissionPort,
    LifecycleOutcome,
    ScheduleService,
)
from ..application.schedule_read import HistoryWindow, ScheduleReadService
from ..application.submissions import (
    IDEMPOTENCY_HEADER,
    SubmissionEndpoint,
    TaskSubmissionService,
    resolve_idempotency_key,
    submission_fingerprint,
)
from ..infrastructure.db import get_session
from ..infrastructure.db_clock import database_now
from ..infrastructure.models.task import (
    ScheduleFire,
    TaskExecution,
    TaskSchedule,
    TaskScheduleRevision,
)
from ..scheduler.client import ScheduleMaterialsProtocol
from .deps import (
    ActorUserId,
    AgentId,
    ChannelTargetsClientDep,
    PlatformSettingsClientDep,
    RequiredActorUserId,
    ScheduleMaterialsClientDep,
    ToolOperationCancellerDep,
    ensure_tenant_consistent,
    get_tenant_id,
)

router = APIRouter(
    prefix="/internal/schedules",
    tags=["schedules"],
    dependencies=[Depends(require_internal_service)],
)

TenantId = Annotated[str, Depends(get_tenant_id)]
Session = Annotated[AsyncSession, Depends(get_session)]


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment is not None else None


def _payload(schedule: TaskSchedule) -> dict[str, Any]:
    """Schedule 的 v2 projection：配置在 Revision 里，本行只有身份与生命周期事实。"""
    return {
        "schedule_id": str(schedule.id),
        "tenant_id": schedule.tenant_id,
        "owner_user_id": str(schedule.owner_user_id),
        "agent_id": str(schedule.agent_id),
        "name": schedule.name,
        "status": schedule.status,
        "current_revision_id": (
            str(schedule.current_revision_id) if schedule.current_revision_id else None
        ),
        "config_version": schedule.config_version,
        "next_fire_at": _iso(schedule.next_fire_at),
        "last_fire_at": _iso(schedule.last_fire_at),
        "occurrence_count": schedule.occurrence_count,
        "attention_reason": schedule.attention_reason,
        "attention_since": _iso(schedule.attention_since),
        "ended_at": _iso(schedule.ended_at),
        "end_reason": schedule.end_reason,
        "create_time": schedule.create_time.isoformat(),
        "update_time": schedule.update_time.isoformat(),
    }


def _fire_payload(fire: ScheduleFire, execution_status: str | None) -> dict[str, Any]:
    return {
        "fire_id": str(fire.id),
        "scheduled_at": fire.scheduled_at.isoformat(),
        "status": fire.status,
        "skip_reason": fire.skip_reason,
        "missed_count": fire.missed_count,
        "dst_resolution": fire.dst_resolution,
        "admission_error_code": fire.admission_error_code,
        "execution_id": str(fire.execution_id) if fire.execution_id else None,
        "execution_status": execution_status,
    }


async def load_schedule(
    session: AsyncSession,
    *,
    tenant_id: str,
    schedule_id: uuid.UUID,
    owner_user_id: uuid.UUID | None,
) -> TaskSchedule:
    """按租户 + owner 读取；跨用户/跨租户一律 404（不暴露存在性）。"""
    schedule = await session.scalar(
        select(TaskSchedule).where(
            TaskSchedule.id == schedule_id,
            TaskSchedule.tenant_id == tenant_id,
            TaskSchedule.is_deleted.is_(False),
        )
    )
    if schedule is None or (owner_user_id is not None and schedule.owner_user_id != owner_user_id):
        raise AppError(ErrorCode.SCHEDULE_NOT_FOUND)
    return schedule


async def load_current_revision(
    session: AsyncSession, *, schedule: TaskSchedule
) -> TaskScheduleRevision:
    """当前 Revision：详情/Draft 预览都读它；缺失说明确认事务尚未提交，按 404 处理。"""
    if schedule.current_revision_id is None:
        raise AppError(ErrorCode.COMMON_NOT_FOUND)
    revision = await session.scalar(
        select(TaskScheduleRevision).where(
            TaskScheduleRevision.id == schedule.current_revision_id,
            TaskScheduleRevision.tenant_id == schedule.tenant_id,
        )
    )
    if revision is None:
        raise AppError(ErrorCode.COMMON_NOT_FOUND)
    return revision


async def page_schedules(
    session: AsyncSession,
    *,
    tenant_id: str,
    owner_user_id: uuid.UUID | None = None,
    agent_id: uuid.UUID | None = None,
    status: ScheduleStatus | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[TaskSchedule], int]:
    conditions: list[Any] = [
        TaskSchedule.tenant_id == tenant_id,
        TaskSchedule.is_deleted.is_(False),
    ]
    if owner_user_id is not None:
        conditions.append(TaskSchedule.owner_user_id == owner_user_id)
    if agent_id is not None:
        conditions.append(TaskSchedule.agent_id == agent_id)
    if status is not None:
        conditions.append(TaskSchedule.status == str(status))
    items = (
        await session.scalars(
            select(TaskSchedule)
            .where(*conditions)
            .order_by(TaskSchedule.update_time.desc(), TaskSchedule.id)
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
    ).all()
    total = (
        await session.scalar(select(func.count()).select_from(TaskSchedule).where(*conditions))
        or 0
    )
    return list(items), int(total)


@router.get("/delivery-targets")
async def delivery_targets(
    request: Request,
    tenant_id: TenantId,
    actor_user_id: RequiredActorUserId,
    agent_id: AgentId,
    targets: ChannelTargetsClientDep,
) -> ApiResponse[Any]:
    """用户已绑定的出站目标；不是任务侧历史 route 快照。

    同一外部用户在多个 bot 上重复绑定时只保留最近绑定的那一个，避免模型看到
    同义选项后反问用户选哪个。通道由 Console 的 `OUTBOUND_CHANNELS` 过滤。
    """
    routes = await targets.list_targets(
        tenant_id=tenant_id,
        platform_user_id=actor_user_id,
        agent_id=agent_id,
    )
    seen: set[tuple[str, str, str, str | None]] = set()
    items: list[dict[str, Any]] = []
    labels: list[str] = []
    for route in routes:
        key = (
            route.channel,
            route.external_user_id,
            route.external_conversation_id,
        )
        if key in seen:
            continue
        seen.add(key)
        label = f"{route.channel}:{route.external_user_id}"
        if label in labels:
            label = f"{label}#{(route.external_conversation_id or route.bot_id)[-6:]}"
        labels.append(label)
        items.append(
            {
                "label": label,
                "channel": route.channel,
                "route": route.model_dump(mode="json"),
            }
        )
    return ok(request.app.state.message_catalog, {"items": items})


async def page_fires(
    session: AsyncSession,
    *,
    schedule: TaskSchedule,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[tuple[ScheduleFire, str | None]], int]:
    rows = await session.execute(
        select(ScheduleFire, TaskExecution.status)
        .outerjoin(
            TaskExecution,
            (TaskExecution.id == ScheduleFire.execution_id)
            & (TaskExecution.tenant_id == ScheduleFire.tenant_id),
        )
        .where(
            ScheduleFire.tenant_id == schedule.tenant_id,
            ScheduleFire.schedule_id == schedule.id,
            ScheduleFire.is_deleted.is_(False),
        )
        .order_by(ScheduleFire.scheduled_at.desc(), ScheduleFire.id)
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    total = (
        await session.scalar(
            select(func.count())
            .select_from(ScheduleFire)
            .where(
                ScheduleFire.tenant_id == schedule.tenant_id,
                ScheduleFire.schedule_id == schedule.id,
                ScheduleFire.is_deleted.is_(False),
            )
        )
        or 0
    )
    return [(row[0], row[1]) for row in rows.all()], int(total)


def parse_action(body: dict[str, Any], action: str) -> ScheduleActionRequest:
    """路径就是动作：body 里出现不同 action 时显式失败，不静默忽略。"""
    declared = body.get("action")
    if declared is not None and declared != action:
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
    try:
        return ScheduleActionRequest.model_validate({**body, "action": action})
    except ValidationError as exc:
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR) from exc


async def _admission(
    session: AsyncSession,
    *,
    tenant_id: str,
    materials: ScheduleMaterialsProtocol | None,
    settings_client: PlatformSettingsClient,
) -> AdmissionPort | None:
    if materials is None:
        return None
    snapshot = await settings_client.fetch_snapshot(tenant_id=tenant_id)
    return ScheduleAdmissionService(materials, settings=resolve_platform_settings(snapshot))


async def run_action(
    request: Request,
    session: AsyncSession,
    *,
    tenant_id: str,
    actor_user_id: uuid.UUID,
    schedule_id: uuid.UUID,
    body: dict[str, Any],
    action: str,
    materials: ScheduleMaterialsProtocol | None,
    settings_client: PlatformSettingsClient,
    canceller: ToolOperationCanceller | None,
) -> ApiResponse[Any]:
    """三个动作共用的幂等/事务/远端取消编排（API-04/05/06）。"""
    parsed = parse_action(body, action)
    idempotency_key = resolve_idempotency_key(request.headers.get(IDEMPOTENCY_HEADER), None)
    fingerprint = submission_fingerprint(
        endpoint=SubmissionEndpoint.SCHEDULE_ACTIONS,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        target_id=schedule_id,
        action={
            "action": action,
            "scope": str(parsed.scope) if parsed.scope is not None else None,
            "reason": parsed.reason,
        },
    )
    submissions = TaskSubmissionService(session)
    first = await submissions.admit(
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        endpoint=SubmissionEndpoint.SCHEDULE_ACTIONS,
        fingerprint=fingerprint,
    )
    if first is not None:
        return ok(request.app.state.message_catalog, first.response_json)
    service = ScheduleService(session, now=await database_now(session))
    if action == "pause":
        outcome = await service.pause(
            schedule_id,
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            reason=parsed.reason,
        )
    elif action == "resume":
        outcome = await service.resume(
            schedule_id,
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            admission=await _admission(
                session,
                tenant_id=tenant_id,
                materials=materials,
                settings_client=settings_client,
            ),
            reason=parsed.reason,
        )
    else:
        outcome = await service.cancel(
            schedule_id,
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            # cancel 的 scope 有默认值（FUTURE_ONLY），显式兜底让类型也成立。
            scope=parsed.scope or CancelScope.FUTURE_ONLY,
            reason=parsed.reason,
        )
    schedule = outcome.schedule
    assert schedule is not None
    payload = _payload(schedule)
    payload.update(_admission_payload(outcome))
    await submissions.record_in(
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        endpoint=SubmissionEndpoint.SCHEDULE_ACTIONS,
        actor_user_id=actor_user_id,
        request_fingerprint=fingerprint,
        response=payload,
        schedule_id=schedule_id,
    )
    await session.commit()
    await dispatch_remote_cancels(
        session,
        tenant_id=tenant_id,
        items=outcome.remote_cancels,
        canceller=canceller,
        now=await database_now(session),
    )
    return ok(request.app.state.message_catalog, payload)


def _admission_payload(outcome: LifecycleOutcome) -> dict[str, Any]:
    error = outcome.admission_error
    if error is None:
        return {}
    return {
        "admission_error": {
            "code": error.code,
            "permanent": error.permanent,
            "repair": error.repair,
        }
    }


@router.get("")
async def list_schedules(
    request: Request,
    tenant_id: TenantId,
    session: Session,
    caller_actor: ActorUserId,
    actor_user_id: Annotated[uuid.UUID | None, Query()] = None,
    agent_id: Annotated[uuid.UUID | None, Query()] = None,
    status: Annotated[ScheduleStatus | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ApiResponse[Any]:
    items, total = await ScheduleReadService(session).list_page(
        tenant_id=tenant_id,
        # Runtime 代表用户查询时只能看到自己的 Schedule，忽略 query 里的 actor。
        owner_user_id=caller_actor or actor_user_id,
        agent_id=agent_id,
        status=status,
        page=page,
        page_size=page_size,
    )
    return ok(
        request.app.state.message_catalog,
        paginate(
            items=[item.model_dump(mode="json") for item in items],
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
    caller_actor: ActorUserId,
) -> ApiResponse[Any]:
    schedule = await load_schedule(
        session, tenant_id=tenant_id, schedule_id=schedule_id, owner_user_id=caller_actor
    )
    revision = await load_current_revision(session, schedule=schedule)
    detail = await ScheduleReadService(session).detail(schedule=schedule, revision=revision)
    return ok(request.app.state.message_catalog, detail.model_dump(mode="json"))


@router.get("/{schedule_id}/fires")
async def list_fires(
    schedule_id: uuid.UUID,
    request: Request,
    tenant_id: TenantId,
    session: Session,
    caller_actor: ActorUserId,
    settings_client: PlatformSettingsClientDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ApiResponse[Any]:
    schedule = await load_schedule(
        session, tenant_id=tenant_id, schedule_id=schedule_id, owner_user_id=caller_actor
    )
    # 保留口径是**读面**的事实（B-10）：设置只在授权判定之后取一次，不为不可见的 Schedule 花钱；
    # 取不到设置就显式失败，不拿 schema 默认值冒充租户配置。
    snapshot = await settings_client.fetch_snapshot(tenant_id=tenant_id)
    schedule_settings = resolve_platform_settings(snapshot).schedule
    history = await ScheduleReadService(session).fire_history(
        schedule=schedule,
        page=page,
        page_size=page_size,
        window=HistoryWindow(
            fire_history_days=schedule_settings.max_fire_history_days,
            execution_history_days=schedule_settings.max_execution_history_days,
        ),
    )
    return ok(request.app.state.message_catalog, history.model_dump(mode="json"))


@router.post("/{schedule_id}/pause")
async def pause_schedule(
    schedule_id: uuid.UUID,
    body: dict[str, Any],
    request: Request,
    tenant_id: TenantId,
    session: Session,
    actor_user_id: RequiredActorUserId,
    materials: ScheduleMaterialsClientDep,
    settings_client: PlatformSettingsClientDep,
    canceller: ToolOperationCancellerDep,
) -> ApiResponse[Any]:
    ensure_tenant_consistent(request, tenant_id)
    return await run_action(
        request,
        session,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        schedule_id=schedule_id,
        body=body,
        action="pause",
        materials=materials,
        settings_client=settings_client,
        canceller=canceller,
    )


@router.post("/{schedule_id}/resume")
async def resume_schedule(
    schedule_id: uuid.UUID,
    body: dict[str, Any],
    request: Request,
    tenant_id: TenantId,
    session: Session,
    actor_user_id: RequiredActorUserId,
    materials: ScheduleMaterialsClientDep,
    settings_client: PlatformSettingsClientDep,
    canceller: ToolOperationCancellerDep,
) -> ApiResponse[Any]:
    ensure_tenant_consistent(request, tenant_id)
    return await run_action(
        request,
        session,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        schedule_id=schedule_id,
        body=body,
        action="resume",
        materials=materials,
        settings_client=settings_client,
        canceller=canceller,
    )


@router.post("/{schedule_id}/cancel")
async def cancel_schedule(
    schedule_id: uuid.UUID,
    body: dict[str, Any],
    request: Request,
    tenant_id: TenantId,
    session: Session,
    actor_user_id: RequiredActorUserId,
    materials: ScheduleMaterialsClientDep,
    settings_client: PlatformSettingsClientDep,
    canceller: ToolOperationCancellerDep,
) -> ApiResponse[Any]:
    ensure_tenant_consistent(request, tenant_id)
    return await run_action(
        request,
        session,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        schedule_id=schedule_id,
        body=body,
        action="cancel",
        materials=materials,
        settings_client=settings_client,
        canceller=canceller,
    )


@router.delete("/{schedule_id}")
async def delete_schedule(
    schedule_id: uuid.UUID,
    request: Request,
    tenant_id: TenantId,
    session: Session,
    actor_user_id: RequiredActorUserId,
) -> ApiResponse[Any]:
    """用户删除：软删自己的 Schedule（任意状态）；历史 Fire/Execution/事件保留。"""
    ensure_tenant_consistent(request, tenant_id)
    await ScheduleService(session, now=await database_now(session)).soft_delete(
        schedule_id,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
    )
    await session.commit()
    return ok(
        request.app.state.message_catalog,
        {"schedule_id": str(schedule_id), "deleted": True},
    )
