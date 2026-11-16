from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import (
    CompletionMode,
    OperationStatus,
    TaskStatus,
    TaskToolOperationCancelRequest,
    TaskToolOperationCancelResponse,
    TaskToolOperationOwner,
    TaskToolOperationResult,
    TaskToolOperationSubmitRequest,
    TaskToolOperationView,
    TerminalStatus,
)
from pydantic import JsonValue, TypeAdapter
from sqlalchemy import Select, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ...infrastructure.models.async_tools import ToolOperation, ToolResultInbox
from ..tool_result_receipts import OP_TERMINAL
from .operations import add_cancel_command
from .task_facts import TASK_OWNER, require_task_execution

JSON_OBJECT = TypeAdapter(dict[str, JsonValue])
#: cancel-before-submit 的 tombstone 里 `input_hash`/`task_snapshot_hash` 的占位符：
#: 此时还没有调用内容，提交到达后再补真实身份；比较时显式识别，不当作真实指纹。
TASK_OPERATION_TOMBSTONE_HASH = "sha256:" + "0" * 64
TERMINAL_EXECUTION = (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)


async def _lock_identity(
    session: AsyncSession, *, tenant_id: str, task_execution_id: UUID, source_tool_call_id: str
) -> None:
    """同一调用身份串行化：并发重试不能各插一条 operation（partial unique 只是兜底）。"""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
        {"identity": f"{tenant_id}:tool-operation:task:{task_execution_id}:{source_tool_call_id}"},
    )


async def _find_by_call(
    session: AsyncSession,
    *,
    tenant_id: str,
    task_execution_id: UUID,
    source_tool_call_id: str,
    for_update: bool = False,
) -> ToolOperation | None:
    statement = select(ToolOperation).where(
        ToolOperation.tenant_id == tenant_id,
        ToolOperation.owner_task_execution_id == task_execution_id,
        ToolOperation.source_tool_call_id == source_tool_call_id,
        ToolOperation.is_deleted.is_(False),
    )
    if for_update:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    return cast(ToolOperation | None, await session.scalar(statement))


def _recorded_input_hash(operation: ToolOperation) -> str | None:
    """已记录的真实输入指纹；tombstone（取消先于提交）尚未记录。"""
    return None if operation.input_hash == TASK_OPERATION_TOMBSTONE_HASH else operation.input_hash


def _fill_submission(operation: ToolOperation, request: TaskToolOperationSubmitRequest) -> None:
    """取消先于提交：补齐调用身份，但绝不复活终态（状态保持 CANCELLED）。"""
    operation.input_hash = request.input_hash
    operation.completion_mode = request.completion_mode
    operation.submission_json = JSON_OBJECT.validate_python(request.model_dump(mode="json"))
    operation.update_time = datetime.now(UTC)


async def submit(
    session: AsyncSession,
    *,
    tenant_id: str,
    actor_user_id: UUID,
    request: TaskToolOperationSubmitRequest,
) -> TaskToolOperationView:
    """按调用身份创建/找回 operation；重试返回同一行，换参数显式失败。"""
    task_execution_id = request.owner.task_execution_id
    facts = await require_task_execution(
        session, tenant_id=tenant_id, task_execution_id=task_execution_id, actor_user_id=actor_user_id
    )
    await _lock_identity(
        session,
        tenant_id=tenant_id,
        task_execution_id=task_execution_id,
        source_tool_call_id=request.source_tool_call_id,
    )
    existing = await _find_by_call(
        session,
        tenant_id=tenant_id,
        task_execution_id=task_execution_id,
        source_tool_call_id=request.source_tool_call_id,
        for_update=True,
    )
    if existing is not None:
        recorded = _recorded_input_hash(existing)
        if recorded is not None and recorded != request.input_hash:
            raise AppError(ErrorCode.TOOL_OPERATION_INPUT_MISMATCH)
        if recorded is None:
            _fill_submission(existing, request)
            await session.flush()
        return await view(session, existing)
    if facts.cancel_requested or facts.status in TERMINAL_EXECUTION:
        # 执行侧已取消/终态：不再种新 operation（否则留下永远不会被消费的等待）。
        raise AppError(ErrorCode.TASK_OPERATION_CANCELLED)
    now = datetime.now(UTC)
    operation = ToolOperation(
        id=uuid4(),
        tenant_id=tenant_id,
        owner_type=TASK_OWNER,
        run_id=None,
        owner_task_execution_id=task_execution_id,
        actor_user_id=actor_user_id,
        source_tool_call_id=request.source_tool_call_id,
        completion_mode=request.completion_mode,
        status=OperationStatus.SUBMITTED,
        # Task owner 没有提交快照：结果到达时按子 Task 的 `snapshot_hash` 校验并改写这一列。
        task_snapshot_hash=request.input_hash,
        input_hash=request.input_hash,
        submission_json=JSON_OBJECT.validate_python(request.model_dump(mode="json")),
        submitted_at=now,
        update_time=now,
    )
    session.add(operation)
    await session.flush()
    return await view(session, operation)


async def cancel(
    session: AsyncSession,
    *,
    tenant_id: str,
    actor_user_id: UUID,
    request: TaskToolOperationCancelRequest,
) -> TaskToolOperationCancelResponse:
    """先持久化取消意图，再投递控制命令；提交回执丢失或尚未提交都能落墓碑。"""
    task_execution_id = request.owner.task_execution_id
    await require_task_execution(
        session, tenant_id=tenant_id, task_execution_id=task_execution_id, actor_user_id=actor_user_id
    )
    await _lock_identity(
        session,
        tenant_id=tenant_id,
        task_execution_id=task_execution_id,
        source_tool_call_id=request.source_tool_call_id,
    )
    operation = await _find_by_call(
        session,
        tenant_id=tenant_id,
        task_execution_id=task_execution_id,
        source_tool_call_id=request.source_tool_call_id,
        for_update=True,
    )
    now = datetime.now(UTC)
    if operation is None:
        operation = ToolOperation(
            id=uuid4(),
            tenant_id=tenant_id,
            owner_type=TASK_OWNER,
            run_id=None,
            owner_task_execution_id=task_execution_id,
            actor_user_id=actor_user_id,
            source_tool_call_id=request.source_tool_call_id,
            completion_mode=CompletionMode.JOIN,
            status=OperationStatus.CANCELLED,
            cancel_requested=True,
            task_snapshot_hash=TASK_OPERATION_TOMBSTONE_HASH,
            input_hash=TASK_OPERATION_TOMBSTONE_HASH,
            submission_json={
                "cancel_intent": {
                    "source_tool_call_id": request.source_tool_call_id,
                    "actor_user_id": str(actor_user_id),
                }
            },
            completed_at=now,
            update_time=now,
        )
        session.add(operation)
        await session.flush()
    elif operation.status not in OP_TERMINAL:
        operation.cancel_requested, operation.status, operation.update_time = (
            True,
            OperationStatus.CANCELLED,
            now,
        )
        operation.completed_at = now
    elif operation.cancel_requested:
        return TaskToolOperationCancelResponse(
            operation_id=operation.id,
            status=OperationStatus(operation.status),
            cancel_requested=True,
        )
    else:
        # 已终态（如结果已到）不写取消标记：迟到取消不得改写终态事实。
        return TaskToolOperationCancelResponse(
            operation_id=operation.id,
            status=OperationStatus(operation.status),
            cancel_requested=False,
        )
    await add_cancel_command(session, operation, now)
    await session.flush()
    return TaskToolOperationCancelResponse(
        operation_id=operation.id,
        status=OperationStatus(operation.status),
        cancel_requested=operation.cancel_requested,
    )


async def view(session: AsyncSession, operation: ToolOperation) -> TaskToolOperationView:
    """读模型：operation 事实 + 至多一份就绪结果；读取本身不改任何行。"""
    inbox = await session.scalar(
        select(ToolResultInbox)
        .where(
            ToolResultInbox.tenant_id == operation.tenant_id,
            ToolResultInbox.operation_id == operation.id,
            ToolResultInbox.is_deleted.is_(False),
        )
        .order_by(ToolResultInbox.create_time.desc(), ToolResultInbox.id.desc())
        .limit(1)
    )
    tool_name = operation.submission_json.get("tool_name")
    assert operation.owner_task_execution_id is not None
    return TaskToolOperationView(
        operation_id=operation.id,
        owner=TaskToolOperationOwner(task_execution_id=operation.owner_task_execution_id),
        source_tool_call_id=operation.source_tool_call_id,
        tool_name=tool_name if isinstance(tool_name, str) else None,
        completion_mode=operation.completion_mode,
        status=OperationStatus(operation.status),
        cancel_requested=operation.cancel_requested,
        task_id=operation.task_id,
        result=None if inbox is None else _result_view(inbox),
    )


def _result_view(inbox: ToolResultInbox) -> TaskToolOperationResult:
    payload = inbox.payload_json
    return TaskToolOperationResult(
        event_id=inbox.event_id,
        task_event_id=inbox.receipt_event_id,
        task_event_seq=inbox.task_event_seq,
        terminal_status=TerminalStatus(str(payload["terminal_status"])),
        completed_at=datetime.fromisoformat(str(payload["completed_at"])),
        late=inbox.late,
        result=cast(dict[str, JsonValue] | None, payload.get("result")),
        error_code=cast(str | None, payload.get("error_code")),
        error_message=cast(str | None, payload.get("error_message")),
    )


def _owner_statement(
    *, tenant_id: str, task_execution_id: UUID, ready: bool | None
) -> Select[tuple[ToolOperation]]:
    received = (
        select(ToolResultInbox.operation_id)
        .where(
            ToolResultInbox.tenant_id == tenant_id,
            ToolResultInbox.is_deleted.is_(False),
        )
        .scalar_subquery()
    )
    statement = select(ToolOperation).where(
        ToolOperation.tenant_id == tenant_id,
        ToolOperation.owner_task_execution_id == task_execution_id,
        ToolOperation.is_deleted.is_(False),
    )
    if ready is True:
        statement = statement.where(ToolOperation.id.in_(received))
    elif ready is False:
        statement = statement.where(ToolOperation.id.not_in(received))
    return statement


async def list_operations(
    session: AsyncSession,
    *,
    tenant_id: str,
    actor_user_id: UUID,
    task_execution_id: UUID,
    ready: bool | None = None,
    limit: int,
    offset: int,
) -> tuple[list[TaskToolOperationView], int]:
    """按 owner 列 operation（周期补扫/崩后重建等待的入口）；`ready` 只过滤结果是否已就绪。

    读取不改任何行：调用方按自己的消费水位（checkpoint/wait_payload）判定已消费哪些结果。
    """
    await require_task_execution(
        session, tenant_id=tenant_id, task_execution_id=task_execution_id, actor_user_id=actor_user_id
    )
    statement = _owner_statement(
        tenant_id=tenant_id, task_execution_id=task_execution_id, ready=ready
    )
    total = int(
        await session.scalar(
            select(func.count()).select_from(statement.order_by(None).subquery())
        )
        or 0
    )
    operations = list(
        await session.scalars(
            statement.order_by(ToolOperation.create_time, ToolOperation.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return [await view(session, operation) for operation in operations], total


async def get_operation(
    session: AsyncSession, *, tenant_id: str, actor_user_id: UUID, operation_id: UUID
) -> TaskToolOperationView:
    """按 operation id 读取；只服务 TASK owner，归属按 Execution 行复核。"""
    operation = await session.scalar(
        select(ToolOperation).where(
            ToolOperation.id == operation_id,
            ToolOperation.tenant_id == tenant_id,
            ToolOperation.owner_type == TASK_OWNER,
            ToolOperation.owner_task_execution_id.is_not(None),
            ToolOperation.is_deleted.is_(False),
        )
    )
    if operation is None:
        raise AppError(ErrorCode.COMMON_NOT_FOUND)
    await require_task_execution(
        session,
        tenant_id=tenant_id,
        task_execution_id=cast(UUID, operation.owner_task_execution_id),
        actor_user_id=actor_user_id,
    )
    return await view(session, operation)
