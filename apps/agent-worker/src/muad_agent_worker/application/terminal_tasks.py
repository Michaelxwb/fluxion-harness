"""One transactional terminal CAS, event, JOIN publication and PUSH delivery boundary.

锁序（设计 §3.3.12/§3.5.3）：`runtime_operation → Schedule → Fire → root Execution →
parent`。立即任务没有 operation 之外的调度行，就从 operation 开始；任何路径都不得先
锁 Execution 再回头取 operation 锁。
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from uuid import NAMESPACE_URL, UUID, uuid5

from muad_contracts import (
    CompletionMode,
    RunToolOperationOwner,
    TaskStatus,
    TerminalStatus,
    ToolResultRequest,
    canonical_json,
)
from muad_logging.redaction import redact_value
from pydantic import JsonValue, TypeAdapter, ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from ..infrastructure.models.runtime_operations import RuntimeOperation, RuntimeResultOutbox
from ..infrastructure.models.task import (
    ScheduleFire,
    TaskDelivery,
    TaskEvent,
    TaskExecution,
    TaskSchedule,
    TaskScheduleRevision,
)
from ..metrics import TASKS_METRIC, record_outcome
from .task_events import EntityType, TaskEventType, append_event

NON_TERMINAL = (
    TaskStatus.QUEUED.value,
    TaskStatus.RUNNING.value,
    TaskStatus.WAITING_EXTERNAL.value,
    TaskStatus.WAITING_HUMAN.value,
)
DELIVERY_KIND_PUSH = "PUSH"
DELIVERY_PENDING = "PENDING"
DELIVERY_INTENT_PUSH = "PUSH"
JSON_OBJECT = TypeAdapter(dict[str, JsonValue])


@dataclass(frozen=True, slots=True)
class TerminalChange:
    status: TerminalStatus
    result: dict[str, JsonValue] | None = None
    error_code: str | None = None
    error_message: str | None = None
    result_artifact_id: UUID | None = None
    cancel_requested: bool = False


async def _lock_row(
    session: AsyncSession, model: type[TaskSchedule] | type[ScheduleFire], row_id: UUID, tenant_id: str
) -> None:
    await session.execute(
        select(model.id)
        .where(model.id == row_id, model.tenant_id == tenant_id, model.is_deleted.is_(False))
        .with_for_update()
    )


async def lock_task_tree(session: AsyncSession, task: TaskExecution) -> None:
    """统一锁入口：operation → Schedule → Fire → root → parent。"""
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
    if root.schedule_id is not None:
        await _lock_row(session, TaskSchedule, root.schedule_id, root.tenant_id)
    if root.schedule_fire_id is not None:
        await _lock_row(session, ScheduleFire, root.schedule_fire_id, root.tenant_id)
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
        owner=RunToolOperationOwner(run_id=task.source_run_id),
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
    """状态 CAS + finished_at + TaskEvent + JOIN outbox + PUSH 意图，同一事务。"""
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
        entity_type=EntityType.EXECUTION,
        entity_id=task.id,
        event_type=event_type or TaskEventType(change.status.value),
        payload=event_payload or {"attempt": task.attempt},
        occurred_at=moment,
    )
    await session.flush()  # outbox 与 delivery 都要看到已落库的事件/实体行。
    await _enqueue_result(session, task, change, moment, event)
    await _enqueue_push_delivery(session, task)
    await session.flush()
    record_outcome(TASKS_METRIC, change.status.value, {"type": task.task_type})
    return True


async def _enqueue_result(
    session: AsyncSession, task: TaskExecution, change: TerminalChange, moment: datetime, event: TaskEvent
) -> None:
    """JOIN 根把结果交回 Runtime Owner；Child 从不发布根结果。"""
    if task.parent_id is not None or task.completion_mode is not CompletionMode.JOIN:
        return
    payload = _payload(task, change, moment, event.seq)
    body = JSON_OBJECT.validate_python(redact_value(payload.model_dump(mode="json")))
    session.add(
        RuntimeResultOutbox(
            tenant_id=task.tenant_id,
            event_id=payload.event_id,
            task_id=task.id,
            task_event_id=event.id,
            operation_id=payload.operation_id,
            task_snapshot_hash=task.snapshot_hash,
            payload_json=body,
            payload_hash="sha256:" + sha256(canonical_json(body).encode()).hexdigest(),
            not_before=moment,
        )
    )


async def _enqueue_push_delivery(session: AsyncSession, task: TaskExecution) -> None:
    """按来源 Revision 的 `delivery_intent` 建投递意图（§3.2.11）：PUSH 才建行，NONE 靠任务中心 pull。"""
    if task.schedule_revision_id is None:
        return
    revision = await session.scalar(
        select(TaskScheduleRevision).where(
            TaskScheduleRevision.id == task.schedule_revision_id,
            TaskScheduleRevision.is_deleted.is_(False),
        )
    )
    if (
        revision is None
        or revision.delivery_intent != DELIVERY_INTENT_PUSH
        or revision.delivery_route_id is None
    ):
        return
    session.add(
        TaskDelivery(
            tenant_id=task.tenant_id,
            execution_id=task.id,
            delivery_route_id=revision.delivery_route_id,
            delivery_kind=DELIVERY_KIND_PUSH,
            delivery_key=f"execution:{task.id}:final",
            status=DELIVERY_PENDING,
        )
    )
