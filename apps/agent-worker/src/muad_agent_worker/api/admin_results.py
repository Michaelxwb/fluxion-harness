"""Audited operational recovery preserves the original immutable terminal result."""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Request
from muad_api import ApiResponse, AppError, ok
from muad_api.error_codes import ErrorCode
from muad_api.security import InternalServiceDep
from muad_contracts import ControlOutboxStatus
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from ..application.task_events import TaskEventType, append_event
from ..application.terminal_tasks import lock_task_tree
from ..infrastructure.models.runtime_operations import RuntimeResultOutbox
from ..infrastructure.models.task import TaskExecution
from .deps import RequiredActorUserId
from .tasks import Session, TenantId

router = APIRouter(prefix="/internal/admin/runtime-results", tags=["admin-runtime-results"])


class RetryResultRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=256)


@router.post("/{event_id}/retry")
async def retry_failed_result(
    event_id: UUID,
    body: RetryResultRequest,
    request: Request,
    tenant: TenantId,
    session: Session,
    actor: RequiredActorUserId,
    _guard: InternalServiceDep,
) -> ApiResponse[dict[str, str]]:
    outbox = await session.scalar(
        select(RuntimeResultOutbox).where(
            RuntimeResultOutbox.event_id == event_id,
            RuntimeResultOutbox.tenant_id == tenant,
            RuntimeResultOutbox.is_deleted.is_(False),
        )
    )
    if outbox is None:
        raise AppError(ErrorCode.COMMON_NOT_FOUND)
    task = (
        await session.scalars(
            select(TaskExecution).where(
                TaskExecution.id == outbox.task_id,
                TaskExecution.tenant_id == tenant,
                TaskExecution.is_deleted.is_(False),
            )
        )
    ).one()
    await lock_task_tree(session, task)
    await session.refresh(outbox, with_for_update=True)
    if outbox.status is not ControlOutboxStatus.FAILED:
        raise AppError(ErrorCode.REVISION_CONFLICT)
    outbox.status, outbox.attempts = ControlOutboxStatus.PENDING, 0
    outbox.lease_owner, outbox.lease_until = None, None
    outbox.not_before = outbox.update_time = datetime.now(UTC)
    await append_event(
        session,
        tenant_id=tenant,
        task_id=task.id,
        event_type=TaskEventType.DELIVERY_RETRY,
        payload={
            "delivery_kind": "RUNTIME_RESULT",
            "event_id": str(event_id),
            "operator_id": str(actor),
            "reason": body.reason,
        },
    )
    await session.commit()
    return ok(
        request.app.state.message_catalog,
        {"event_id": str(event_id), "status": ControlOutboxStatus.PENDING.value},
    )
