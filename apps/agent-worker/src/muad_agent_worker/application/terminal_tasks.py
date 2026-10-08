"""One transactional terminal CAS, event and JOIN result publication boundary."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from uuid import NAMESPACE_URL, UUID, uuid5

from muad_contracts import CompletionMode, TaskStatus, TerminalStatus, ToolResultRequest, canonical_json
from muad_logging.redaction import redact_value
from pydantic import JsonValue, TypeAdapter, ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from ..infrastructure.models.runtime_operations import RuntimeOperation, RuntimeResultOutbox
from ..infrastructure.models.task import TaskEvent, TaskExecution
from ..metrics import TASKS_METRIC, record_outcome
from .task_events import TaskEventType, append_event

NON_TERMINAL = (TaskStatus.QUEUED.value, TaskStatus.RUNNING.value, TaskStatus.WAITING.value)
JSON_OBJECT = TypeAdapter(dict[str, JsonValue])


@dataclass(frozen=True, slots=True)
class TerminalChange:
    status: TerminalStatus
    result: dict[str, JsonValue] | None = None
    error_code: str | None = None
    error_message: str | None = None
    result_artifact_id: UUID | None = None
    cancel_requested: bool = False


async def lock_task_tree(session: AsyncSession, task: TaskExecution) -> None:
    root_id = task.root_id or task.parent_id or task.id
    root = await session.scalar(
        select(TaskExecution).where(
            TaskExecution.id == root_id,
            TaskExecution.tenant_id == task.tenant_id,
            TaskExecution.is_deleted.is_(False),
        )
    )
    if root is None:
        return
    if root.source_operation_id is not None:
        await session.execute(
            select(RuntimeOperation.id)
            .where(
                RuntimeOperation.tenant_id == root.tenant_id,
                RuntimeOperation.operation_id == root.source_operation_id,
                RuntimeOperation.is_deleted.is_(False),
            )
            .with_for_update()
        )
    # Ancestors are always locked before the child, including nested batches.
    await session.execute(
        select(TaskExecution.id)
        .where(
            TaskExecution.id == root_id,
            TaskExecution.tenant_id == task.tenant_id,
            TaskExecution.is_deleted.is_(False),
        )
        .with_for_update()
    )
    if task.parent_id is not None and task.parent_id != root_id:
        await session.execute(
            select(TaskExecution.id)
            .where(
                TaskExecution.id == task.parent_id,
                TaskExecution.tenant_id == task.tenant_id,
                TaskExecution.is_deleted.is_(False),
            )
            .with_for_update()
        )


def _payload(task: TaskExecution, change: TerminalChange, moment: datetime, seq: int) -> ToolResultRequest:
    if task.source_operation_id is None or task.source_run_id is None:
        raise ValueError("JOIN root requires operation and source Run identities")
    return ToolResultRequest(
        event_id=uuid5(NAMESPACE_URL, f"muad:task-result:{task.tenant_id}:{task.id}:{seq}"),
        operation_id=task.source_operation_id,
        task_id=task.id,
        task_event_seq=seq,
        source_run_id=task.source_run_id,
        actor_user_id=task.actor_user_id,
        task_snapshot_hash=task.snapshot_hash,
        terminal_status=change.status,
        completed_at=moment,
        result=change.result if change.status is TerminalStatus.COMPLETED else None,
        error_code=change.error_code
        or ("TASK_CANCELLED" if change.status is TerminalStatus.CANCELLED else None),
        error_message=change.error_message,
    )


def _validated_change(task: TaskExecution, change: TerminalChange, moment: datetime) -> TerminalChange:
    if task.completion_mode is not CompletionMode.JOIN or task.parent_id is not None:
        return change
    try:
        _payload(task, change, moment, 1)
    except ValidationError:
        return TerminalChange(
            status=TerminalStatus.FAILED,
            error_code="SKILL_RESULT_INVALID",
            error_message="Skill result violates the strict JSON result protocol",
        )
    return change


async def write_terminal(
    session: AsyncSession,
    task: TaskExecution,
    change: TerminalChange,
    moment: datetime,
    *,
    conditions: Sequence[ColumnElement[bool]] = (),
    event_type: TaskEventType | None = None,
    event_payload: dict[str, JsonValue] | None = None,
) -> bool:
    await lock_task_tree(session, task)
    change = _validated_change(task, change, moment)
    updated = await session.scalar(
        update(TaskExecution)
        .where(
            TaskExecution.id == task.id,
            TaskExecution.tenant_id == task.tenant_id,
            TaskExecution.is_deleted.is_(False),
            TaskExecution.status.in_(NON_TERMINAL),
            *conditions,
        )
        .values(
            status=change.status.value,
            result_json=change.result,
            error_code=change.error_code,
            error_message=change.error_message,
            result_artifact_id=change.result_artifact_id,
            finished_at=moment,
            cancel_requested=change.cancel_requested or TaskExecution.cancel_requested,
            lease_owner=None,
            lease_until=None,
            update_time=moment,
        )
        .returning(TaskExecution.id)
        .execution_options(synchronize_session="fetch")
    )
    if updated is None:
        return False
    event = await append_event(
        session,
        tenant_id=task.tenant_id,
        task_id=task.id,
        event_type=event_type or TaskEventType(change.status.value),
        payload=event_payload or {"attempt": task.attempt},
    )
    await session.flush()  # The outbox's composite FK must see its terminal event.
    await _enqueue_result(session, task, change, moment, event)
    await session.flush()
    record_outcome(TASKS_METRIC, change.status.value, {"type": task.task_type})
    return True


async def _enqueue_result(
    session: AsyncSession, task: TaskExecution, change: TerminalChange, moment: datetime, event: TaskEvent
) -> None:
    if task.completion_mode is not CompletionMode.JOIN or task.parent_id is not None:
        return
    payload = _payload(task, change, moment, event.seq)
    body = JSON_OBJECT.validate_python(redact_value(payload.model_dump(mode="json")))
    session.add(
        RuntimeResultOutbox(
            tenant_id=task.tenant_id,
            event_id=payload.event_id,
            task_id=task.id,
            task_event_seq=event.seq,
            operation_id=payload.operation_id,
            task_snapshot_hash=task.snapshot_hash,
            payload_json=body,
            payload_hash="sha256:" + sha256(canonical_json(body).encode()).hexdigest(),
            not_before=moment,
        )
    )
