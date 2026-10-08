"""Reserve immutable tool intentions under the Run lock before any HTTP."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING
from uuid import uuid4

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import (
    AsyncToolPolicy,
    CompletionMode,
    ControlCommand,
    CreateTaskRequest,
    DeliveryMode,
    OperationStatus,
    ResolvedSkill,
    RunStatus,
    RuntimeOperationInput,
    TaskStatus,
    ToolSubmissionReceipt,
    canonical_json,
    snapshot_hash,
)
from muad_contracts.canonical import ensure_strict_json
from pydantic import JsonValue, TypeAdapter
from sqlalchemy import false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ...infrastructure.models.async_tools import ToolControlOutbox, ToolOperation
from ...infrastructure.models.runtime import RunRecord, RuntimeSnapshot
from ..task_client import TaskSubmissionContext, build_task_snapshot
from ..tool_result_receipts import OP_TERMINAL, lock_run_source

if TYPE_CHECKING:
    from .control_dispatcher import ControlDispatcher

JSON_OBJECT = TypeAdapter(dict[str, JsonValue])
DEPENDENT_JOIN = (
    OperationStatus.SUBMITTED,
    OperationStatus.TASK_ACCEPTED,
    OperationStatus.RUNNING,
    OperationStatus.RESULT_RECEIVED,
    OperationStatus.MATERIALIZED,
)


async def _source(session: AsyncSession, context: TaskSubmissionContext) -> tuple[RunRecord, RuntimeSnapshot]:
    run = await session.scalar(
        select(RunRecord).where(
            RunRecord.id == context.source_run_id,
            RunRecord.tenant_id == context.tenant_id,
            RunRecord.is_deleted.is_(False),
        )
    )
    if run is None:
        raise AppError(ErrorCode.COMMON_NOT_FOUND)
    run, _ = await lock_run_source(session, context.tenant_id, run)
    if run.user_id != context.actor_user_id or run.agent_id != context.agent.id:
        raise AppError(ErrorCode.FORBIDDEN)
    snapshot = await session.scalar(
        select(RuntimeSnapshot).where(
            RuntimeSnapshot.id == run.snapshot_id,
            RuntimeSnapshot.run_id == run.id,
            RuntimeSnapshot.tenant_id == context.tenant_id,
            RuntimeSnapshot.is_deleted.is_(False),
        )
    )
    if snapshot is None:
        raise AppError(ErrorCode.RUN_CHECKPOINT_INVALID)
    return run, snapshot


def _authorize(snapshot: RuntimeSnapshot, skill: ResolvedSkill) -> None:
    frozen = next((row for row in snapshot.skill_catalog_json if row.get("key") == skill.key), None)
    if frozen is None or ResolvedSkill.model_validate(frozen) != skill:
        raise AppError(ErrorCode.FORBIDDEN)


async def _capacity(session: AsyncSession, run: RunRecord, policy: AsyncToolPolicy) -> None:
    count = await session.scalar(
        select(func.count())
        .select_from(ToolOperation)
        .where(
            ToolOperation.tenant_id == run.tenant_id,
            ToolOperation.run_id == run.id,
            ToolOperation.is_deleted.is_(False),
            or_(
                ToolOperation.status == OperationStatus.SUBMIT_PENDING,
                (ToolOperation.completion_mode == CompletionMode.JOIN)
                & ToolOperation.status.in_(DEPENDENT_JOIN),
            ),
        )
    )
    if (count or 0) >= policy.pending_operation_limit:
        raise AppError(ErrorCode.TOOL_OPERATION_CAPACITY_EXCEEDED)


def _submission(
    context: TaskSubmissionContext,
    skill: ResolvedSkill,
    input_data: dict[str, JsonValue],
    run: RunRecord,
    policy: AsyncToolPolicy,
    mode: CompletionMode,
    call_id: str,
) -> CreateTaskRequest:
    operation_id = uuid4()
    snapshot, _ = build_task_snapshot(context, skill)
    budget = JSON_OBJECT.validate_python(snapshot["budget"])
    budget.update({"max_attempts": 1, "async_tools": policy.model_dump(mode="json")})
    if mode is CompletionMode.JOIN:
        if run.deadline_at is None:
            raise AppError(ErrorCode.RUN_CHECKPOINT_INVALID)
        budget["run_deadline_at"] = run.deadline_at.isoformat()
    snapshot["budget"] = budget
    route = context.delivery_route if mode is CompletionMode.DETACH else None
    return CreateTaskRequest(
        tenant_id=run.tenant_id,
        agent_id=run.agent_id,
        actor_user_id=run.user_id,
        source_run_id=run.id,
        runtime_operation=RuntimeOperationInput(
            operation_id=operation_id,
            source_run_id=run.id,
            source_tool_call_id=call_id,
            completion_mode=mode,
        ),
        intent_key=skill.key,
        skill_id=skill.skill_id,
        skill_artifact_id=skill.artifact_id,
        input=input_data,
        execution_snapshot=snapshot,
        snapshot_hash=snapshot_hash(snapshot),
        idempotency_key=f"runtime-op:{operation_id}:submit",
        delivery_route=route,
        delivery_mode=DeliveryMode.FINAL_ONLY if route else DeliveryMode.NONE,
    )


async def reserve_operation(
    factory: async_sessionmaker[AsyncSession],
    context: TaskSubmissionContext,
    *,
    skill: ResolvedSkill,
    input_data: Mapping[str, object],
    call_id: str,
    completion_mode: CompletionMode,
) -> ToolOperation:
    data = JSON_OBJECT.validate_python(ensure_strict_json(dict(input_data)))
    fingerprint = _input_hash(skill, data, completion_mode)
    async with factory() as session, session.begin():
        run, snapshot = await _source(session, context)
        _authorize(snapshot, skill)
        existing = await session.scalar(
            select(ToolOperation).where(
                ToolOperation.run_id == run.id,
                ToolOperation.tenant_id == run.tenant_id,
                ToolOperation.source_tool_call_id == call_id,
                ToolOperation.is_deleted.is_(False),
            )
        )
        if existing is not None:
            if existing.input_hash != fingerprint:
                raise AppError(ErrorCode.IDEMPOTENCY_MISMATCH)
            session.expunge(existing)
            return existing
        if run.status != RunStatus.RUNNING or run.cancel_requested:
            raise AppError(ErrorCode.RUN_BUSY)
        if run.deadline_at is None or run.deadline_at <= datetime.now(UTC):
            raise AppError(ErrorCode.RUN_CHECKPOINT_INVALID)
        policy = AsyncToolPolicy.model_validate(snapshot.policy_json.get("async_tools", {}))
        await _capacity(session, run, policy)
        body = _submission(context, skill, data, run, policy, completion_mode, call_id)
        operation = await _insert_operation(session, body, fingerprint)
        session.expunge(operation)
    return operation


def _input_hash(skill: ResolvedSkill, data: dict[str, JsonValue], mode: CompletionMode) -> str:
    payload = {"skill": skill.model_dump(mode="json"), "input": data, "mode": mode.value}
    return "sha256:" + sha256(canonical_json(payload).encode()).hexdigest()


async def _insert_operation(
    session: AsyncSession, body: CreateTaskRequest, fingerprint: str
) -> ToolOperation:
    source = body.runtime_operation
    assert source is not None
    operation = ToolOperation(
        id=source.operation_id,
        tenant_id=body.tenant_id,
        run_id=source.source_run_id,
        actor_user_id=body.actor_user_id,
        source_tool_call_id=source.source_tool_call_id,
        completion_mode=source.completion_mode,
        status=OperationStatus.SUBMIT_PENDING,
        task_snapshot_hash=body.snapshot_hash,
        input_hash=fingerprint,
        submission_json=JSON_OBJECT.validate_python(body.model_dump(mode="json")),
    )
    session.add(operation)
    await session.flush()
    session.add(
        ToolControlOutbox(
            tenant_id=body.tenant_id,
            operation_id=operation.id,
            command=ControlCommand.SUBMIT,
            not_before=datetime.now(UTC),
        )
    )
    await session.flush()
    return operation


async def cancel_operations(session: AsyncSession, run: RunRecord) -> None:
    operations = list(
        await session.scalars(
            select(ToolOperation)
            .where(
                ToolOperation.run_id == run.id,
                ToolOperation.tenant_id == run.tenant_id,
                ToolOperation.is_deleted.is_(False),
                ToolOperation.status.not_in(OP_TERMINAL),
                or_(
                    ToolOperation.status == OperationStatus.SUBMIT_PENDING,
                    ToolOperation.completion_mode == CompletionMode.JOIN,
                ),
            )
            .order_by(ToolOperation.id)
            .with_for_update()
        )
    )
    now = datetime.now(UTC)
    for operation in operations:
        operation.cancel_requested, operation.status, operation.update_time = (
            True,
            OperationStatus.CANCELLED,
            now,
        )
        await add_cancel_command(session, operation, now)


async def add_cancel_command(session: AsyncSession, operation: ToolOperation, now: datetime) -> None:
    from sqlalchemy.dialects.postgresql import insert

    await session.execute(
        insert(ToolControlOutbox)
        .values(
            tenant_id=operation.tenant_id,
            operation_id=operation.id,
            command=ControlCommand.CANCEL_OPERATION,
            not_before=now,
        )
        .on_conflict_do_nothing(
            index_elements=[ToolControlOutbox.operation_id, ToolControlOutbox.command],
            index_where=ToolControlOutbox.is_deleted == false(),
        )
    )


class DurableTaskSubmitter:
    def __init__(self, factory: async_sessionmaker[AsyncSession], dispatcher: ControlDispatcher) -> None:
        self._factory, self._dispatcher = factory, dispatcher

    async def submit_task(
        self,
        context: TaskSubmissionContext,
        *,
        skill: ResolvedSkill,
        input_data: Mapping[str, object],
        call_id: str,
        completion_mode: CompletionMode,
    ) -> dict[str, JsonValue]:
        import asyncio

        operation = await reserve_operation(
            self._factory,
            context,
            skill=skill,
            input_data=input_data,
            call_id=call_id,
            completion_mode=completion_mode,
        )
        try:
            async with asyncio.timeout(5):
                await self._dispatcher.dispatch_operation(operation.id)
        except TimeoutError:
            # Attempt was committed before IO; the leased outbox remains recoverable.
            self._dispatcher.report_pending_timeout()
        async with self._factory() as session:
            row = await session.get(ToolOperation, operation.id)
            assert row is not None
            if row.status == OperationStatus.FAILED:
                raise AppError(row.error_code or ErrorCode.COMMON_INTERNAL_ERROR)
            task_status = await _admitted_status(session, row) if row.task_id else None
            receipt = ToolSubmissionReceipt(
                status="SUBMITTED" if row.task_id else "SUBMISSION_PENDING",
                operation_id=row.id,
                task_id=row.task_id,
                task_status=task_status,
                completion_mode=row.completion_mode,
            )
            result = JSON_OBJECT.validate_python(receipt.model_dump(mode="json"))
            if row.task_id is None:
                result["pending_reason"] = "DURABLE_ADMISSION_UNCONFIRMED"
        if operation.task_id is None:
            await _record_pending(self._factory, context, operation)
        return result


async def _record_pending(
    factory: async_sessionmaker[AsyncSession], context: TaskSubmissionContext, operation: ToolOperation
) -> None:
    from uuid import NAMESPACE_URL, uuid5

    from ..run_events import EventWriter

    async with factory() as session, session.begin():
        run, _ = await _source(session, context)
        current = await session.scalar(
            select(ToolOperation)
            .where(
                ToolOperation.id == operation.id,
                ToolOperation.tenant_id == run.tenant_id,
                ToolOperation.is_deleted.is_(False),
            )
            .with_for_update()
        )
        if (
            current is not None
            and current.status == OperationStatus.SUBMIT_PENDING
            and not run.cancel_requested
        ):
            await EventWriter(session).append(
                tenant_id=run.tenant_id,
                conversation_id=run.conversation_id,
                run_id=run.id,
                event_type="TOOL_SUBMISSION_PENDING",
                stream_type="tool.submission_pending",
                source_event_id=uuid5(NAMESPACE_URL, f"muad:tool-operation:{operation.id}:pending"),
                payload={
                    "event_version": 1,
                    "operation_id": str(operation.id),
                    "task_id": None,
                    "completion_mode": operation.completion_mode.value,
                    "reason": "DURABLE_ADMISSION_UNCONFIRMED",
                },
            )


async def _admitted_status(session: AsyncSession, operation: ToolOperation) -> TaskStatus:
    from ...infrastructure.models.async_tools import ToolResultInbox
    from ...infrastructure.models.runtime import CanonicalEvent

    inbox = await session.scalar(
        select(ToolResultInbox).where(
            ToolResultInbox.operation_id == operation.id,
            ToolResultInbox.tenant_id == operation.tenant_id,
            ToolResultInbox.is_deleted.is_(False),
        )
    )
    if inbox is not None:
        return TaskStatus(str(inbox.payload_json["terminal_status"]))
    event = await session.scalar(
        select(CanonicalEvent)
        .where(
            CanonicalEvent.run_id == operation.run_id,
            CanonicalEvent.tenant_id == operation.tenant_id,
            CanonicalEvent.event_type == "TOOL_TASK_ACCEPTED",
            CanonicalEvent.is_deleted.is_(False),
            CanonicalEvent.payload_json["operation_id"].astext == str(operation.id),
        )
        .order_by(CanonicalEvent.seq.desc())
        .limit(1)
    )
    if event is None:
        raise AppError(ErrorCode.RUN_CHECKPOINT_INVALID)
    return TaskStatus(str(event.payload_json["task_status"]))
