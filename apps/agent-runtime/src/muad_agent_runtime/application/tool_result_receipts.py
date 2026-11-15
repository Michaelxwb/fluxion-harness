"""Persist result facts and receipt metadata before acknowledging Worker delivery."""

from datetime import UTC, datetime
from hashlib import sha256
from typing import cast
from uuid import NAMESPACE_URL, UUID, uuid5

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import (
    OperationStatus,
    RunStatus,
    TaskToolOperationOwner,
    ToolResultReceipt,
    ToolResultRequest,
    canonical_json,
)
from pydantic import JsonValue, TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.async_tools import RunContinuation, ToolOperation, ToolResultInbox
from ..infrastructure.models.runtime import CanonicalEvent, Conversation, RunRecord
from .async_tools.task_facts import (
    TASK_OWNER,
    read_task_event,
    read_task_execution_snapshot,
)
from .run_events import EventWriter

INBOX_TASK_EVENT = "TASK_EVENT"
INBOX_RUN_EVENT = "RUN_EVENT"
RUN_TERMINAL = (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED)
OP_TERMINAL = (
    OperationStatus.COMPLETED,
    OperationStatus.FAILED,
    OperationStatus.CANCELLED,
    OperationStatus.LATE,
)
JSON_OBJECT = TypeAdapter(dict[str, JsonValue])


async def lock_run_source(
    session: AsyncSession, tenant: str, run: RunRecord
) -> tuple[RunRecord, RunContinuation | None]:
    await session.execute(
        select(Conversation.id)
        .where(
            Conversation.id == run.conversation_id,
            Conversation.tenant_id == tenant,
            Conversation.is_deleted.is_(False),
        )
        .with_for_update()
    )
    locked = await session.scalar(
        select(RunRecord)
        .where(RunRecord.id == run.id, RunRecord.tenant_id == tenant, RunRecord.is_deleted.is_(False))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if locked is None:
        raise AppError(ErrorCode.TOOL_RESULT_BINDING_MISMATCH)
    continuation = await session.scalar(
        select(RunContinuation)
        .where(
            RunContinuation.run_id == run.id,
            RunContinuation.tenant_id == tenant,
            RunContinuation.is_deleted.is_(False),
        )
        .with_for_update()
    )
    return locked, continuation


async def _lock_operation(session: AsyncSession, tenant: str, operation_id) -> ToolOperation | None:
    return await session.scalar(
        select(ToolOperation)
        .where(
            ToolOperation.id == operation_id,
            ToolOperation.tenant_id == tenant,
            ToolOperation.is_deleted.is_(False),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def _lock_source(
    session: AsyncSession, tenant: str, request: ToolResultRequest
) -> tuple[RunRecord | None, RunContinuation | None, ToolOperation]:
    """按 operation 行的 owner 分流：RUN 锁 Run/Conversation/continuation，TASK 只锁 operation。"""
    operation = await session.scalar(
        select(ToolOperation).where(
            ToolOperation.id == request.operation_id,
            ToolOperation.tenant_id == tenant,
            ToolOperation.is_deleted.is_(False),
        )
    )
    if operation is None:
        raise AppError(ErrorCode.TOOL_RESULT_BINDING_MISMATCH)
    if operation.owner_type == TASK_OWNER:
        locked = await _lock_operation(session, tenant, operation.id)
        if locked is None:
            raise AppError(ErrorCode.TOOL_RESULT_BINDING_MISMATCH)
        return None, None, locked
    run = await session.scalar(
        select(RunRecord).where(
            RunRecord.id == operation.run_id, RunRecord.tenant_id == tenant, RunRecord.is_deleted.is_(False)
        )
    )
    if run is None:
        raise AppError(ErrorCode.TOOL_RESULT_BINDING_MISMATCH)
    run, continuation = await lock_run_source(session, tenant, run)
    locked = await _lock_operation(session, tenant, operation.id)
    if locked is None:
        raise AppError(ErrorCode.TOOL_RESULT_BINDING_MISMATCH)
    return run, continuation, locked


def _verify(operation: ToolOperation, request: ToolResultRequest) -> None:
    """身份/归属绑定：owner 必须与 operation 行一致，actor/task/终态事件不得改写。

    RUN 行还比对提交时冻结的快照 hash；TASK 行没有提交快照，回执改按子 Task 的冻结快照校验
    （`_verify_task_receipt`），因此这里不比较该列。
    """
    task_owned = isinstance(request.owner, TaskToolOperationOwner)
    if task_owned:
        owner_matches = (
            operation.owner_type == TASK_OWNER
            and operation.run_id is None
            and operation.owner_task_execution_id == request.owner.task_execution_id
        )
    else:
        owner_matches = (
            operation.owner_type != TASK_OWNER
            and operation.run_id == request.owner.run_id
            and operation.task_snapshot_hash == request.task_snapshot_hash
        )
    if (
        not owner_matches
        or operation.actor_user_id != request.actor_user_id
        or (operation.task_id is not None and operation.task_id != request.task_id)
        or (operation.terminal_event_id is not None and operation.terminal_event_id != request.event_id)
    ):
        raise AppError(ErrorCode.TOOL_RESULT_BINDING_MISMATCH)


async def _verify_task_receipt(session: AsyncSession, tenant: str, request: ToolResultRequest) -> UUID:
    """跨 schema 不建 FK：回执由 `task.task_event` 校验，回执 id 由它解析（不接受调用方声明）。

    绑定两件事：`(EXECUTION, task_id, task_event_seq)` 是这条 Task 自己的终态事件；该 Task 的
    冻结快照与回执声明的 `snapshot_hash` 一致。
    """
    task_event_id = await read_task_event(
        session, tenant_id=tenant, entity_id=request.task_id, seq=request.task_event_seq
    )
    if task_event_id is None:
        raise AppError(ErrorCode.TOOL_RESULT_BINDING_MISMATCH)
    snapshot = await read_task_execution_snapshot(
        session, tenant_id=tenant, task_execution_id=request.task_id
    )
    if snapshot is None or snapshot != request.task_snapshot_hash:
        raise AppError(ErrorCode.TOOL_RESULT_BINDING_MISMATCH)
    return task_event_id


def _is_late(run: RunRecord | None, operation: ToolOperation, moment: datetime) -> bool:
    """迟到 = 接收侧已终态/已取消：只记事实，不能把等待中的执行拉回 QUEUED。"""
    return (
        operation.cancel_requested
        or operation.status in OP_TERMINAL
        or (
            run is not None
            and (
                run.status in RUN_TERMINAL
                or run.cancel_requested
                or (run.deadline_at is not None and run.deadline_at <= moment)
            )
        )
    )


async def receive_result(session: AsyncSession, tenant: str, request: ToolResultRequest) -> ToolResultReceipt:
    run, continuation, operation = await _lock_source(session, tenant, request)
    body = JSON_OBJECT.validate_python(request.model_dump(mode="json"))
    fingerprint = "sha256:" + sha256(canonical_json(body).encode()).hexdigest()
    existing = await session.scalar(
        select(ToolResultInbox).where(
            ToolResultInbox.tenant_id == tenant,
            ToolResultInbox.event_id == request.event_id,
            ToolResultInbox.is_deleted.is_(False),
        )
    )
    if existing is not None:
        if existing.payload_hash != fingerprint:
            raise AppError(ErrorCode.IDEMPOTENCY_MISMATCH)
        _verify(operation, request)
        return ToolResultReceipt(event_id=request.event_id, persisted=True, duplicate=True)
    _verify(operation, request)
    task_owned = operation.owner_type == TASK_OWNER
    task_event_id = await _verify_task_receipt(session, tenant, request) if task_owned else None
    late = _is_late(run, operation, datetime.now(UTC))
    receipt = None if run is None else await _append_receipt(session, tenant, run, operation, request, late)
    _add_inbox(session, tenant, run, operation, request, receipt, task_event_id, fingerprint, body, late)
    operation.task_id, operation.terminal_event_id = request.task_id, request.event_id
    if task_owned:
        # Task owner 没有提交快照：结果到达时以子 Task 的冻结快照为准（已在上方校验）。
        operation.task_snapshot_hash = request.task_snapshot_hash
    operation.status = OperationStatus.LATE if late else OperationStatus.RESULT_RECEIVED
    operation.completed_at, operation.update_time = request.completed_at, datetime.now(UTC)
    if continuation is not None and not late:
        continuation.ready, continuation.update_time = True, datetime.now(UTC)
    await session.flush()
    return ToolResultReceipt(event_id=request.event_id, persisted=True, duplicate=False)


async def _append_receipt(
    session: AsyncSession,
    tenant: str,
    run: RunRecord,
    operation: ToolOperation,
    request: ToolResultRequest,
    late: bool,
) -> CanonicalEvent:
    event_type = "BACKGROUND_RESULT_LATE" if late else "TOOL_RESULT_RECEIVED"
    seq = await EventWriter(session).append(
        tenant_id=tenant,
        conversation_id=run.conversation_id,
        run_id=run.id,
        event_type=event_type,
        stream_type="tool.result.late" if late else "tool.result.received",
        source_event_id=uuid5(NAMESPACE_URL, f"muad:tool-result:{request.event_id}:receipt"),
        payload={
            "event_version": 1,
            "operation_id": str(operation.id),
            "task_id": str(request.task_id),
            "terminal_status": request.terminal_status.value,
            "event_id": str(request.event_id),
        },
    )
    await session.flush()
    receipt = (
        await session.scalars(
            select(CanonicalEvent).where(
                CanonicalEvent.tenant_id == tenant,
                CanonicalEvent.run_id == run.id,
                CanonicalEvent.seq == seq,
                CanonicalEvent.is_deleted.is_(False),
            )
        )
    ).one()
    return receipt


def _add_inbox(
    session: AsyncSession,
    tenant: str,
    run: RunRecord | None,
    operation: ToolOperation,
    request: ToolResultRequest,
    receipt: CanonicalEvent | None,
    task_event_id: UUID | None,
    fingerprint: str,
    body: dict[str, JsonValue],
    late: bool,
) -> None:
    """回执 kind 分流：RUN 指 canonical event，TASK 指 `task.task_event`（禁止互相冒充）。"""
    task_owned = run is None
    if task_owned:
        assert task_event_id is not None
        receipt_event_id, receipt_seq = task_event_id, request.task_event_seq
    else:
        assert receipt is not None
        receipt_event_id, receipt_seq = receipt.id, receipt.seq
    session.add(
        ToolResultInbox(
            tenant_id=tenant,
            event_id=request.event_id,
            run_id=None if task_owned else run.id,
            task_execution_id=cast(UUID, operation.owner_task_execution_id) if task_owned else None,
            operation_id=operation.id,
            task_id=request.task_id,
            task_event_seq=request.task_event_seq,
            payload_hash=fingerprint,
            payload_json=body,
            late=late,
            receipt_event_type=INBOX_TASK_EVENT if task_owned else INBOX_RUN_EVENT,
            receipt_event_id=receipt_event_id,
            receipt_seq=receipt_seq,
            canonical_event_id=None if task_owned else receipt.id,
            materialized=False,
        )
    )
