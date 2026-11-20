"""One transactional terminal CAS, event, JOIN publication and PUSH delivery boundary.

锁序（设计 §3.3.12/§3.5.3）：`runtime_operation → Schedule → Fire → root Execution →
parent`。立即任务没有 operation 之外的调度行，就从 operation 开始；任何路径都不得先
锁 Execution 再回头取 operation 锁。
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from muad_contracts import (
    MAX_TOOL_ERROR_MESSAGE_CHARS,
    CompletionMode,
    DeliveryIntent,
    RunToolOperationOwner,
    TaskStatus,
    TaskToolOperationOwner,
    TerminalStatus,
    ToolResultRequest,
    canonical_json,
)
from muad_logging import get_logger
from muad_logging.redaction import redact_text, redact_value
from pydantic import JsonValue, TypeAdapter
from sqlalchemy import false, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from ..delivery.records import DeliveryKind, DeliveryState, execution_delivery_key
from ..infrastructure.models.runtime_operations import RuntimeOperation, RuntimeResultOutbox
from ..infrastructure.models.task import (
    ScheduleFire,
    TaskDelivery,
    TaskEvent,
    TaskExecution,
    TaskSchedule,
    TaskScheduleRevision,
)
from ..metrics import (
    TASK_EXECUTION_DURATION_SECONDS_METRIC,
    TASKS_METRIC,
    record_gauge,
    record_outcome,
)
from .task_events import EntityType, TaskEventType, append_event
from .task_message_outbox import enqueue_task_message_outbox

logger = get_logger(__name__)

NON_TERMINAL = (
    TaskStatus.QUEUED.value,
    TaskStatus.RUNNING.value,
    TaskStatus.WAITING_EXTERNAL.value,
    TaskStatus.WAITING_HUMAN.value,
)
DELIVERY_INTENT_PUSH = str(DeliveryIntent.PUSH)
JSON_OBJECT = TypeAdapter(dict[str, JsonValue])
RESULT_STATUS_KEY = "result_status"
RESULT_TEXT_KEY = "text"
NO_CHANGE_STATUS = "no_change"
MAX_REMEMBERED_RESULT_CHARS = 4000


@dataclass(frozen=True, slots=True)
class TerminalChange:
    status: TerminalStatus
    result: dict[str, JsonValue] | None = None
    #: 结果发件的有界投影（大结果外置引用/预览）；任务行仍保存完整 `result`。
    outbox_result: dict[str, JsonValue] | None = None
    error_code: str | None = None
    error_message: str | None = None
    result_artifact_id: UUID | None = None
    cancel_requested: bool = False
    #: 失败分类（`FailureClass`，如 `HUMAN_TIMEOUT`）：终态写入的唯一出口，调用方只给事实。
    failure_class: str | None = None


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


def _bounded_error_message(task: TaskExecution, value: str | None) -> str | None:
    """原样正文的模型/发件投影：脱敏后按协议上限截断，截断事实可见。"""
    if value is None:
        return None
    redacted = redact_text(value)
    if len(redacted) <= MAX_TOOL_ERROR_MESSAGE_CHARS:
        return redacted
    logger.warning(
        "error_message_truncated",
        extra={
            "task_id": str(task.id),
            "original_chars": len(redacted),
            "truncated_chars": MAX_TOOL_ERROR_MESSAGE_CHARS,
        },
    )
    return redacted[: MAX_TOOL_ERROR_MESSAGE_CHARS - 1] + "…"


def _payload(task: TaskExecution, change: TerminalChange, moment: datetime, seq: int) -> ToolResultRequest:
    if task.source_operation_id is None or task.source_run_id is None:
        raise ValueError("JOIN root requires operation and source Run identities")
    result = change.outbox_result if change.outbox_result is not None else change.result
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
        result=result if change.status is TerminalStatus.COMPLETED else None,
        error_code=change.error_code
        or ("TASK_CANCELLED" if change.status is TerminalStatus.CANCELLED else None),
        error_message=_bounded_error_message(task, change.error_message),
    )


def _task_owned_result(change: TerminalChange) -> tuple[dict[str, Any] | None, str | None, str | None]:
    """子 Task 的信封解包：模型只该看到 Skill 的结构化结果，而不是执行器元数据。"""
    if change.status is not TerminalStatus.COMPLETED:
        return None, change.error_code, change.error_message
    if change.outbox_result is not None:
        return dict(change.outbox_result), None, None
    payload = change.result
    if isinstance(payload, Mapping) and "status" in payload:
        status = str(payload.get("status"))
        if status != "SUCCEEDED":
            return (
                None,
                str(payload.get("error_code") or status or "SKILL_EXECUTION_FAILED"),
                str(payload.get("stderr") or ""),
            )
        inner = payload.get("result")
        return (dict(inner) if isinstance(inner, Mapping) else {}), None, None
    return (dict(payload) if isinstance(payload, Mapping) else {}), None, None


def _task_owned_payload(
    task: TaskExecution, change: TerminalChange, moment: datetime, seq: int
) -> ToolResultRequest:
    """Task-owned ASYNC Skill 的子 Task：结果回给父 Execution 的操作收件箱。"""
    if task.source_operation_id is None or task.parent_id is None:
        raise ValueError("task-owned child requires operation and parent identities")
    result, error_code, error_message = _task_owned_result(change)
    return ToolResultRequest(
        event_id=uuid5(NAMESPACE_URL, f"muad:task-result:{task.tenant_id}:{task.id}:{seq}"),
        operation_id=task.source_operation_id,
        task_id=task.id,
        task_event_seq=seq,
        owner=TaskToolOperationOwner(task_execution_id=task.parent_id),
        actor_user_id=task.actor_user_id,
        task_snapshot_hash=task.snapshot_hash,
        terminal_status=change.status,
        completed_at=moment,
        result=result,
        error_code=error_code,
        error_message=_bounded_error_message(task, error_message),
    )


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
    stored_error = None if change.error_message is None else redact_text(change.error_message)
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
            error_message=stored_error,
            result_artifact_id=change.result_artifact_id,
            failure_class=change.failure_class,
            finished_at=moment,
            cancel_requested=change.cancel_requested or TaskExecution.cancel_requested,
            lease_owner=None,
            lease_until=None,
            # 终态不再"等待中"：`wait_kind` 与契约（仅 WAITING_EXTERNAL 有效）保持一致。
            # 载荷保留：取消路径在终态之后仍按 `wait_payload_json.operations[]` 发远端取消意图。
            wait_kind=None,
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
    try:
        async with session.begin_nested():
            await _enqueue_result(session, task, change, moment, event)
    except Exception as exc:  # noqa: BLE001 - 发件失败不得回滚已验证的终态
        logger.warning(
            "task_result_publish_failed",
            extra={"task_id": str(task.id), "error_type": type(exc).__name__},
        )
    outbox_written = False
    if change.status is TerminalStatus.COMPLETED:
        try:
            async with session.begin_nested():
                outbox_written = await enqueue_task_message_outbox(
                    session, task, moment, result=change.result
                )
        except Exception as exc:  # noqa: BLE001 - 发件箱失败不得回滚已验证的终态
            logger.warning(
                "task_message_outbox_write_failed",
                extra={"task_id": str(task.id), "error_type": type(exc).__name__},
            )
    if not outbox_written and _result_status(change.result) != NO_CHANGE_STATUS:
        await _enqueue_push_delivery(session, task)
    if change.status is TerminalStatus.COMPLETED:
        await _remember_meaningful_result(session, task, change)
    await session.flush()
    record_outcome(TASKS_METRIC, change.status.value, {"type": task.task_type})
    if task.started_at is not None:
        record_gauge(
            TASK_EXECUTION_DURATION_SECONDS_METRIC,
            max(0.0, (moment - task.started_at).total_seconds()),
            {"type": task.task_type},
        )
    return True


def _result_status(result: Mapping[str, JsonValue] | None) -> str | None:
    if not isinstance(result, Mapping):
        return None
    value = result.get(RESULT_STATUS_KEY)
    return value if isinstance(value, str) and value else None


async def _remember_meaningful_result(
    session: AsyncSession, task: TaskExecution, change: TerminalChange
) -> None:
    """Persist the last meaningful scheduled result; ``no_change`` never overwrites it.

    The Schedule row is already locked by ``lock_task_tree``. Older occurrences finishing
    later must not replace a newer recorded result, so ``scheduled_at`` is monotonic.
    """
    if task.schedule_id is None or change.status is not TerminalStatus.COMPLETED:
        return
    status = _result_status(change.result)
    if status is None or status == NO_CHANGE_STATUS:
        return
    result = change.result or {}
    text = result.get(RESULT_TEXT_KEY)
    if not isinstance(text, str):
        text = ""
    if len(text) > MAX_REMEMBERED_RESULT_CHARS:
        text = text[: MAX_REMEMBERED_RESULT_CHARS - 1] + "…"
    stored = {
        "status": status,
        "text": text,
        "scheduled_at": (
            task.scheduled_at.astimezone(UTC).isoformat()
            if task.scheduled_at is not None
            else None
        ),
    }
    previous = await session.scalar(
        select(TaskSchedule.last_result_json).where(
            TaskSchedule.id == task.schedule_id,
            TaskSchedule.tenant_id == task.tenant_id,
            TaskSchedule.is_deleted.is_(False),
        )
    )
    previous_at = previous.get("scheduled_at") if isinstance(previous, Mapping) else None
    if (
        isinstance(previous_at, str)
        and isinstance(stored["scheduled_at"], str)
        and previous_at >= stored["scheduled_at"]
    ):
        return
    await session.execute(
        update(TaskSchedule)
        .where(
            TaskSchedule.id == task.schedule_id,
            TaskSchedule.tenant_id == task.tenant_id,
            TaskSchedule.is_deleted.is_(False),
        )
        .values(last_result_json=stored)
    )


async def _enqueue_result(
    session: AsyncSession, task: TaskExecution, change: TerminalChange, moment: datetime, event: TaskEvent
) -> None:
    """Run-owned 根任务（JOIN/DETACH）都把结果交回 Runtime；Task-owned 子 Task 回父收件箱。"""
    if task.parent_id is not None:
        if (
            task.source_run_id is not None
            or task.source_operation_id is None
            or task.completion_mode is not CompletionMode.JOIN
        ):
            return
        payload = _task_owned_payload(task, change, moment, event.seq)
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
        return
    if task.source_run_id is None:
        # 定时任务没有 source Run；文件/文本由任务中心或既有 Schedule 投递读取。
        return
    if task.source_operation_id is None:
        logger.warning(
            "task_result_publish_not_applicable",
            extra={"task_id": str(task.id), "reason": "missing_source_operation"},
        )
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
    """按来源 Revision 的 `delivery_intent` 建投递意图（§3.2.11）：PUSH 才建行，NONE 靠任务中心 pull。

    终态事件先落库，再在这里建独立投递记录（`DELIVERY/ENQUEUED`）——发送是投递循环的事，
    业务终态不等待、也不依赖投递结果。稳定键与种类归 `delivery.records`，同键重放不新增。
    """
    if task.parent_id is not None or task.schedule_revision_id is None:
        # JOIN/BATCH 子任务不直接向用户投递；父任务终态聚合子产物（design §5.7）。
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
    delivery_id = uuid4()
    inserted = (
        await session.execute(
            pg_insert(TaskDelivery)
            .values(
                id=delivery_id,
                tenant_id=task.tenant_id,
                execution_id=task.id,
                delivery_route_id=revision.delivery_route_id,
                delivery_kind=str(DeliveryKind.PUSH),
                delivery_key=execution_delivery_key(task.id),
                status=str(DeliveryState.PENDING),
                attempt_count=0,
            )
            .on_conflict_do_nothing(
                index_elements=[TaskDelivery.tenant_id, TaskDelivery.delivery_key],
                index_where=TaskDelivery.is_deleted == false(),
            )
            .returning(TaskDelivery.id)
        )
    ).scalar_one_or_none()
    if inserted is None:
        return
    await append_event(
        session,
        tenant_id=task.tenant_id,
        entity_type=EntityType.DELIVERY,
        entity_id=inserted,
        event_type=TaskEventType.ENQUEUED,
        payload={"kind": str(DeliveryKind.PUSH), "execution_id": str(task.id)},
    )
