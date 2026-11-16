"""Worker-owned operation row serializes submission with cancellation tombstones.

owner 联合与 Runtime 同一口径：RUN 行有 `source_run_id`，TASK 行有 `task_execution_id`（值 =
`task.task_execution.id`，跨 schema 逻辑引用、不建物理 FK）。取消按**调用身份**落墓碑：
提交回执丢失或尚未提交时先到的那次取消也必须持久，之后的提交被拒而不是复活执行。
"""

from datetime import UTC, datetime
from uuid import UUID

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import (
    CancelOperationRequest,
    CancelOperationResponse,
    CreateTaskRequest,
    RunToolOperationOwner,
    TaskStatus,
    TaskToolOperationOwner,
    ToolOperationOwner,
)
from sqlalchemy import false, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.runtime_operations import RuntimeOperation
from ..infrastructure.models.task import TaskExecution
from .submissions import SubmissionEndpoint, submission_fingerprint


def _owner_values(owner: ToolOperationOwner) -> dict[str, UUID]:
    if isinstance(owner, TaskToolOperationOwner):
        return {"task_execution_id": owner.task_execution_id}
    return {"source_run_id": owner.run_id}


def _same_owner(operation: RuntimeOperation, owner: ToolOperationOwner) -> bool:
    if isinstance(owner, TaskToolOperationOwner):
        return operation.task_execution_id == owner.task_execution_id and operation.source_run_id is None
    return operation.source_run_id == owner.run_id and operation.task_execution_id is None


async def lock_operation(
    session: AsyncSession,
    tenant: str,
    operation_id: UUID,
    *,
    owner: ToolOperationOwner,
    source_tool_call_id: str,
    actor_user_id: UUID,
) -> RuntimeOperation:
    """墓碑先落库：同一 operation 的提交与取消因此共用一行（唯一键兜底并发）。"""
    await session.execute(
        insert(RuntimeOperation)
        .values(
            tenant_id=tenant,
            operation_id=operation_id,
            source_tool_call_id=source_tool_call_id,
            actor_user_id=actor_user_id,
            **_owner_values(owner),
        )
        .on_conflict_do_nothing(
            index_elements=[RuntimeOperation.tenant_id, RuntimeOperation.operation_id],
            index_where=RuntimeOperation.is_deleted == false(),
        )
    )
    operation = (
        await session.scalars(
            select(RuntimeOperation)
            .where(
                RuntimeOperation.tenant_id == tenant,
                RuntimeOperation.operation_id == operation_id,
                RuntimeOperation.is_deleted.is_(False),
            )
            .with_for_update()
        )
    ).one()
    if (
        not _same_owner(operation, owner)
        or operation.source_tool_call_id != source_tool_call_id
        or operation.actor_user_id != actor_user_id
    ):
        raise AppError(ErrorCode.TOOL_RESULT_BINDING_MISMATCH)
    return operation


async def prepare_submission(session: AsyncSession, request: CreateTaskRequest) -> RuntimeOperation | None:
    source = request.runtime_operation
    if source is None:
        return None
    operation = await lock_operation(
        session,
        request.tenant_id,
        source.operation_id,
        owner=RunToolOperationOwner(run_id=source.source_run_id),
        source_tool_call_id=source.source_tool_call_id,
        actor_user_id=request.actor_user_id,
    )
    fingerprint = submission_fingerprint(
        endpoint=SubmissionEndpoint.TASKS,
        tenant_id=request.tenant_id,
        actor_user_id=request.actor_user_id,
        target_id=source.operation_id,
        action=request,
    )
    if operation.submission_hash is not None and operation.submission_hash != fingerprint:
        raise AppError(ErrorCode.IDEMPOTENCY_MISMATCH)
    if operation.cancel_requested:
        raise AppError(ErrorCode.TASK_OPERATION_CANCELLED)
    operation.submission_hash, operation.completion_mode = fingerprint, source.completion_mode
    operation.update_time = datetime.now(UTC)
    return operation


async def cancel_operation(
    session: AsyncSession, tenant: str, operation_id: UUID, request: CancelOperationRequest
) -> CancelOperationResponse:
    from .task_service import TERMINAL_STATUSES, TaskService

    operation = await lock_operation(
        session,
        tenant,
        operation_id,
        owner=request.owner,
        source_tool_call_id=request.source_tool_call_id,
        actor_user_id=request.actor_user_id,
    )
    operation.cancel_requested, operation.update_time = True, datetime.now(UTC)
    task = None
    if operation.task_id is not None:
        task = await session.scalar(
            select(TaskExecution).where(
                TaskExecution.id == operation.task_id,
                TaskExecution.tenant_id == tenant,
                TaskExecution.is_deleted.is_(False),
            )
        )
        if task is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        if task.status not in TERMINAL_STATUSES:
            await TaskService(session).cancel(tenant, task.id, actor_user_id=request.actor_user_id)
            await session.refresh(task)
    await session.flush()
    return CancelOperationResponse(
        operation_id=operation_id,
        cancel_recorded=True,
        task_id=task.id if task is not None else None,
        task_status=TaskStatus(task.status) if task is not None else None,
        cancel_requested=task.cancel_requested if task is not None else True,
    )
