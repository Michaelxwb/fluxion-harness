"""API-11：取消单条 Execution（design §3.4.2 / §3.4.5）。

与 Schedule 的两档取消共用同一套语义（`ALL_ACTIVE` 作用在这棵执行子树上）：从未开始的
直接终态、RUNNING 协作停止、WAITING_EXTERNAL 终止并取消远端 operation、WAITING_HUMAN
终止并原子关闭 OPEN interaction。**不改 Schedule 状态**：一次 occurrence 的取消不等于停掉
整个定时任务。
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from muad_api import ApiResponse, AppError, ok
from muad_api.error_codes import ErrorCode
from muad_api.security import require_internal_service
from muad_contracts import ContractModel
from pydantic import Field, JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..application.remote_cancellation import dispatch_remote_cancels
from ..application.schedule_lifecycle import ExecutionService
from ..application.schedule_read import ScheduleReadService
from ..application.submissions import (
    IDEMPOTENCY_HEADER,
    SubmissionEndpoint,
    TaskSubmissionService,
    resolve_idempotency_key,
    submission_fingerprint,
)
from ..infrastructure.db import get_session
from ..infrastructure.db_clock import database_now
from ..infrastructure.models.task import TaskExecution
from .deps import (
    RequiredActorUserId,
    ToolOperationCancellerDep,
    ensure_tenant_consistent,
    get_tenant_id,
)

router = APIRouter(
    prefix="/internal/executions",
    tags=["executions"],
    dependencies=[Depends(require_internal_service)],
)

TenantId = Annotated[str, Depends(get_tenant_id)]
Session = Annotated[AsyncSession, Depends(get_session)]


class ExecutionCancelRequest(ContractModel):
    """执行取消只接受原因；范围由端点本身决定（这里没有第二套 `cancel_running`）。"""

    reason: str | None = Field(default=None, max_length=128)


@router.post("/{execution_id}/cancel")
async def cancel_execution(
    execution_id: uuid.UUID,
    body: ExecutionCancelRequest,
    request: Request,
    tenant_id: TenantId,
    session: Session,
    actor_user_id: RequiredActorUserId,
    canceller: ToolOperationCancellerDep,
) -> ApiResponse[Any]:
    ensure_tenant_consistent(request, tenant_id)
    idempotency_key = resolve_idempotency_key(request.headers.get(IDEMPOTENCY_HEADER), None)
    fingerprint = submission_fingerprint(
        endpoint=SubmissionEndpoint.EXECUTION_CANCEL,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        target_id=execution_id,
        action={"reason": body.reason},
    )
    submissions = TaskSubmissionService(session)
    first = await submissions.admit(
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        endpoint=SubmissionEndpoint.EXECUTION_CANCEL,
        fingerprint=fingerprint,
    )
    if first is not None:
        return ok(request.app.state.message_catalog, first.response_json)
    outcome = await ExecutionService(session, now=await database_now(session)).cancel(
        execution_id,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        reason=body.reason,
    )
    execution = outcome.execution
    assert execution is not None
    payload: dict[str, JsonValue] = {
        "execution_id": str(execution.id),
        "status": execution.status,
        "cancel_requested": bool(execution.cancel_requested),
    }
    await submissions.record_in(
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        endpoint=SubmissionEndpoint.EXECUTION_CANCEL,
        actor_user_id=actor_user_id,
        request_fingerprint=fingerprint,
        response=payload,
        task_id=execution.id,
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


@router.get("/{execution_id}")
async def get_execution(
    execution_id: uuid.UUID,
    request: Request,
    tenant_id: TenantId,
    session: Session,
    caller_actor: RequiredActorUserId,
    children_page: Annotated[int, Query(ge=1)] = 1,
    children_page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ApiResponse[Any]:
    """API-09：Execution 详情的唯一读点（TaskCenter 与 Console 共用同一投影）。

    可见性按 `tenant + actor`：别人的执行一律 404，不泄露存在性。等待载荷只在
    `WAITING_EXTERNAL` 时返回；其余状态里它只是内部事实（见 `ScheduleReadService`）。
    """
    execution = await session.scalar(
        select(TaskExecution).where(
            TaskExecution.id == execution_id,
            TaskExecution.tenant_id == tenant_id,
            TaskExecution.actor_user_id == caller_actor,
            TaskExecution.is_deleted.is_(False),
        )
    )
    if execution is None:
        raise AppError(ErrorCode.COMMON_NOT_FOUND)
    detail = await ScheduleReadService(session).execution_detail(
        execution=execution,
        children_page=children_page,
        children_page_size=children_page_size,
    )
    return ok(request.app.state.message_catalog, detail.model_dump(mode="json"))
