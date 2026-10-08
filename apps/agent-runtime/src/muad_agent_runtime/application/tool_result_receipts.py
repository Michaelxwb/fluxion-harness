"""Persist result facts and receipt metadata before acknowledging Worker delivery."""

from datetime import UTC, datetime
from hashlib import sha256
from uuid import NAMESPACE_URL, uuid5

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import OperationStatus, RunStatus, ToolResultReceipt, ToolResultRequest, canonical_json
from pydantic import JsonValue, TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.async_tools import RunContinuation, ToolOperation, ToolResultInbox
from ..infrastructure.models.runtime import CanonicalEvent, Conversation, RunRecord
from .run_events import EventWriter

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


async def _lock_source(
    session: AsyncSession, tenant: str, request: ToolResultRequest
) -> tuple[RunRecord, RunContinuation | None, ToolOperation]:
    operation = await session.scalar(
        select(ToolOperation).where(
            ToolOperation.id == request.operation_id,
            ToolOperation.tenant_id == tenant,
            ToolOperation.is_deleted.is_(False),
        )
    )
    if operation is None:
        raise AppError(ErrorCode.TOOL_RESULT_BINDING_MISMATCH)
    run = await session.scalar(
        select(RunRecord).where(
            RunRecord.id == operation.run_id, RunRecord.tenant_id == tenant, RunRecord.is_deleted.is_(False)
        )
    )
    if run is None:
        raise AppError(ErrorCode.TOOL_RESULT_BINDING_MISMATCH)
    run, continuation = await lock_run_source(session, tenant, run)
    locked = await session.scalar(
        select(ToolOperation)
        .where(
            ToolOperation.id == operation.id,
            ToolOperation.tenant_id == tenant,
            ToolOperation.is_deleted.is_(False),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if locked is None:
        raise AppError(ErrorCode.TOOL_RESULT_BINDING_MISMATCH)
    return run, continuation, locked


def _verify(operation: ToolOperation, request: ToolResultRequest) -> None:
    if (
        operation.run_id != request.source_run_id
        or operation.actor_user_id != request.actor_user_id
        or operation.task_snapshot_hash != request.task_snapshot_hash
        or (operation.task_id is not None and operation.task_id != request.task_id)
        or (operation.terminal_event_id is not None and operation.terminal_event_id != request.event_id)
    ):
        raise AppError(ErrorCode.TOOL_RESULT_BINDING_MISMATCH)


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
    late = (
        run.status in RUN_TERMINAL
        or run.cancel_requested
        or operation.cancel_requested
        or operation.status in OP_TERMINAL
    )
    receipt = await _append_receipt(session, tenant, run, operation, request, late)
    _add_inbox(session, tenant, run, operation, request, receipt, fingerprint, body, late)
    operation.task_id, operation.terminal_event_id = request.task_id, request.event_id
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
    run: RunRecord,
    operation: ToolOperation,
    request: ToolResultRequest,
    receipt: CanonicalEvent,
    fingerprint: str,
    body: dict[str, JsonValue],
    late: bool,
) -> None:
    session.add(
        ToolResultInbox(
            tenant_id=tenant,
            event_id=request.event_id,
            run_id=run.id,
            operation_id=operation.id,
            task_id=request.task_id,
            task_event_seq=request.task_event_seq,
            payload_hash=fingerprint,
            payload_json=body,
            late=late,
            receipt_event_id=receipt.id,
            receipt_seq=receipt.seq,
        )
    )
