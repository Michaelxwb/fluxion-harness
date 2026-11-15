"""API-04/05/06/11：Schedule 暂停/恢复/取消与 Execution 取消（design §3.2.5 / §3.2.10 / §3.4.5）。

所有入口（内部 API，后续 surface API）共用本模块：同一事务内按固定锁序
`operation → Schedule → Fire → root → parent → child → 事件` 判定状态并落动作；只有
**恢复时的 Admission** 与**远端 operation 取消**在锁外（网络不持行锁）。

状态矩阵（§3.4.5）：

| 动作 | 未来 Fire | QUEUED 未开始 | QUEUED 已开始 | RUNNING | WAITING_EXTERNAL | WAITING_HUMAN | OPEN Human |
|---|---|---|---|---|---|---|---|
| Pause / `FUTURE_ONLY` | 停 | 取消 | 保留 | 保留 | 保留 | 保留 | 保留 |
| `ALL_ACTIVE` | 停 | 取消 | 取消 | 协作取消 | 终止 + 取消 operation | 终止 | 原子关闭 |

三条硬约束：

1. **状态在行锁内决定**：Schedule 行锁把同一 Schedule 的生命周期动作串行化；执行行再逐条
   `FOR UPDATE` 复核，`status='QUEUED' AND started_at IS NULL` 是「从未开始」的唯一判据 ——
   claim 赢了就按新状态处理，不会被一刀切成「取消」。
2. **`FUTURE_ONLY` 不关闭 interaction**：执行还在等人工时关掉问题会出现「Execution 等、
   Interaction 已关」的卡死；只有 `ALL_ACTIVE` 才把 Execution 与 interaction 一起收尾。
3. **取消不是删除**：Fire/Execution/事件全部保留，Fire 只从创建时的事实改为取消时的状态；
   `ENDED`/`CANCELLED` 不可恢复，ONCE 准入失败只能重新创建。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import (
    CancelScope,
    FireStatus,
    HumanInteractionStatus,
    RecurrenceKind,
    ScheduleEndReason,
    SchedulePolicy,
    ScheduleStatus,
    TaskStatus,
    TerminalStatus,
)
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.task import (
    ScheduleFire,
    TaskExecution,
    TaskHumanInteraction,
    TaskSchedule,
    TaskScheduleRevision,
)
from ..scheduler.recurrence import build_rule, initial_fire_at
from .batch_fanin import settle_child
from .schedule_admission import (
    AdmissionError,
    AdmissionOutcome,
    AdmissionRejected,
    AdmissionRequest,
    admission_request_from_revision,
)
from .task_events import EntityType, TaskEventSeed, TaskEventType, append_events
from .terminal_tasks import TerminalChange, lock_task_tree, write_terminal

logger = logging.getLogger(__name__)

ACTIVE = ScheduleStatus.ACTIVE.value
PAUSED = ScheduleStatus.PAUSED.value
NEEDS_ATTENTION = ScheduleStatus.NEEDS_ATTENTION.value
ENDED = ScheduleStatus.ENDED.value
CANCELLED = ScheduleStatus.CANCELLED.value
TERMINAL_SCHEDULE_STATUSES = (ENDED, CANCELLED)
RESUMABLE_SCHEDULE_STATUSES = (PAUSED, NEEDS_ATTENTION)

QUEUED = TaskStatus.QUEUED.value
RUNNING = TaskStatus.RUNNING.value
WAITING_EXTERNAL = TaskStatus.WAITING_EXTERNAL.value
WAITING_HUMAN = TaskStatus.WAITING_HUMAN.value
NON_TERMINAL = (QUEUED, RUNNING, WAITING_EXTERNAL, WAITING_HUMAN)

#: 嵌套批量最多下探层数（与 `task_cancel` 同一安全阀，不是业务规则）。
MAX_CASCADE_DEPTH = 8
DEFAULT_REASON = "USER_REQUEST"


class AdmissionPort(Protocol):
    """恢复时的准入端口：`ScheduleAdmissionService` 结构兼容，测试可注入假实现。"""

    async def admit(
        self, request: AdmissionRequest, *, retry_transient_once: bool = False
    ) -> AdmissionOutcome: ...


@dataclass(frozen=True, slots=True)
class RemoteCancel:
    """一个待取消的远端 ASYNC operation（网络调用在事务提交之后）。"""

    execution_id: uuid.UUID
    operation_id: uuid.UUID
    source_tool_call_id: str
    actor_user_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class LifecycleOutcome:
    schedule: TaskSchedule | None = None
    execution: TaskExecution | None = None
    remote_cancels: tuple[RemoteCancel, ...] = ()
    admission_error: AdmissionError | None = None


@dataclass(slots=True)
class _Book:
    """一次取消动作在事务内落下的账：被取消的执行、被取消的 Fire、待远端取消的 operation。"""

    cancelled: list[uuid.UUID] = field(default_factory=list)
    fires: list[uuid.UUID] = field(default_factory=list)
    remote: list[RemoteCancel] = field(default_factory=list)


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment is not None else None


def operation_cancels(execution: TaskExecution) -> list[RemoteCancel]:
    """从 `wait_payload_json.operations[]` 取待取消的调用身份；坏载荷显式告警不猜。"""
    payload = execution.wait_payload_json or {}
    operations = payload.get("operations")
    if not isinstance(operations, list):
        return []
    items: list[RemoteCancel] = []
    for entry in operations:
        call_id = entry.get("call_id") if isinstance(entry, dict) else None
        if not isinstance(call_id, str) or not call_id:
            logger.warning(
                "schedule_cancel_wait_payload_invalid",
                extra={"execution_id": str(execution.id), "reason": "CALL_ID_MISSING"},
            )
            continue
        try:
            operation_id = uuid.UUID(str(entry.get("operation_id")))
        except (TypeError, ValueError):
            operation_id = uuid5(NAMESPACE_URL, f"muad:async:{execution.id}:{call_id}")
        items.append(
            RemoteCancel(
                execution_id=execution.id,
                operation_id=operation_id,
                source_tool_call_id=call_id,
                actor_user_id=execution.actor_user_id,
            )
        )
    return items


async def _lock_schedule(
    session: AsyncSession,
    *,
    tenant_id: str,
    schedule_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    include_deleted: bool = False,
) -> TaskSchedule:
    """Schedule 行锁 + owner 复核：跨租户/跨用户一律 404（不暴露存在性）。"""
    conditions = [TaskSchedule.id == schedule_id, TaskSchedule.tenant_id == tenant_id]
    if not include_deleted:
        conditions.append(TaskSchedule.is_deleted.is_(False))
    schedule = await session.scalar(
        select(TaskSchedule)
        .where(*conditions)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if schedule is None or (
        actor_user_id is not None and schedule.owner_user_id != actor_user_id
    ):
        raise AppError(ErrorCode.SCHEDULE_NOT_FOUND)
    return schedule


async def _load_schedule(
    session: AsyncSession,
    *,
    tenant_id: str,
    schedule_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
) -> TaskSchedule:
    schedule = await session.scalar(
        select(TaskSchedule).where(
            TaskSchedule.id == schedule_id,
            TaskSchedule.tenant_id == tenant_id,
            TaskSchedule.is_deleted.is_(False),
        )
    )
    if schedule is None or (
        actor_user_id is not None and schedule.owner_user_id != actor_user_id
    ):
        raise AppError(ErrorCode.SCHEDULE_NOT_FOUND)
    return schedule


def _state_invalid(status: str) -> AppError:
    return AppError(ErrorCode.SCHEDULE_STATE_INVALID, data={"status": status})


async def _root_executions(
    session: AsyncSession, schedule: TaskSchedule
) -> list[TaskExecution]:
    rows = await session.scalars(
        select(TaskExecution)
        .where(
            TaskExecution.tenant_id == schedule.tenant_id,
            TaskExecution.schedule_id == schedule.id,
            TaskExecution.parent_id.is_(None),
            TaskExecution.status.in_(NON_TERMINAL),
            TaskExecution.is_deleted.is_(False),
        )
        .order_by(TaskExecution.id)
    )
    return list(rows)


async def _children(session: AsyncSession, parent: TaskExecution) -> list[TaskExecution]:
    rows = await session.scalars(
        select(TaskExecution)
        .where(
            TaskExecution.tenant_id == parent.tenant_id,
            TaskExecution.parent_id == parent.id,
            TaskExecution.status.in_(NON_TERMINAL),
            TaskExecution.is_deleted.is_(False),
        )
        .order_by(TaskExecution.id)
    )
    return list(rows)


async def _async_events(
    session: AsyncSession,
    execution: TaskExecution,
    items: list[RemoteCancel],
    event_type: TaskEventType,
    payload: dict[str, object],
    now: datetime,
) -> None:
    await append_events(
        session,
        [
            TaskEventSeed(
                tenant_id=execution.tenant_id,
                entity_type=EntityType.ASYNC,
                entity_id=item.operation_id,
                event_type=event_type,
                payload={
                    **payload,
                    "execution_id": str(execution.id),
                    "source_tool_call_id": item.source_tool_call_id,
                },
                occurred_at=now,
            )
            for item in items
        ],
    )


async def _close_interactions(
    session: AsyncSession, execution: TaskExecution, reason: str, now: datetime
) -> None:
    rows = await session.execute(
        update(TaskHumanInteraction)
        .where(
            TaskHumanInteraction.tenant_id == execution.tenant_id,
            TaskHumanInteraction.execution_id == execution.id,
            TaskHumanInteraction.status == HumanInteractionStatus.OPEN.value,
            TaskHumanInteraction.is_deleted.is_(False),
        )
        .values(
            status=HumanInteractionStatus.CANCELLED.value,
            resolved_at=now,
            update_time=now,
        )
        .returning(TaskHumanInteraction.id)
        .execution_options(synchronize_session=False)
    )
    interaction_ids = [row[0] for row in rows.all()]
    if not interaction_ids:
        return
    await append_events(
        session,
        [
            TaskEventSeed(
                tenant_id=execution.tenant_id,
                entity_type=EntityType.HUMAN,
                entity_id=interaction_id,
                event_type=TaskEventType.CANCELLED,
                payload={"reason": reason, "execution_id": str(execution.id)},
                occurred_at=now,
            )
            for interaction_id in interaction_ids
        ],
    )


async def _cancel_fire(
    session: AsyncSession, execution: TaskExecution, book: _Book, now: datetime
) -> None:
    """Fire 保留创建事实，只把状态改为取消时的结果，并写 FIRE 事件。"""
    if execution.schedule_fire_id is None:
        return
    fire = await session.scalar(
        select(ScheduleFire)
        .where(
            ScheduleFire.id == execution.schedule_fire_id,
            ScheduleFire.tenant_id == execution.tenant_id,
            ScheduleFire.is_deleted.is_(False),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if fire is None or fire.status == FireStatus.CANCELLED.value:
        return
    fire.status = FireStatus.CANCELLED.value
    fire.update_time = now
    book.fires.append(fire.id)
    await append_events(
        session,
        [
            TaskEventSeed(
                tenant_id=fire.tenant_id,
                entity_type=EntityType.FIRE,
                entity_id=fire.id,
                event_type=TaskEventType.CANCELLED,
                payload={"execution_id": str(execution.id)},
                occurred_at=now,
            )
        ],
    )


async def _flag_running(session: AsyncSession, execution: TaskExecution, now: datetime) -> None:
    """协作停止：只落标记，真正的 CANCELLED 由持有 lease 的 Worker 在检查点写。"""
    await session.execute(
        update(TaskExecution)
        .where(
            TaskExecution.id == execution.id,
            TaskExecution.tenant_id == execution.tenant_id,
            TaskExecution.status == RUNNING,
            TaskExecution.is_deleted.is_(False),
        )
        .values(cancel_requested=True, update_time=now)
        .execution_options(synchronize_session=False)
    )


async def _terminate(
    session: AsyncSession, execution: TaskExecution, reason: str, book: _Book, now: datetime
) -> bool:
    written = await write_terminal(
        session,
        execution,
        TerminalChange(status=TerminalStatus.CANCELLED, cancel_requested=True),
        now,
        event_payload={"reason": reason},
    )
    if not written:
        return False
    book.cancelled.append(execution.id)
    await _cancel_fire(session, execution, book, now)
    await settle_child(session, execution, now)
    return True


async def apply_execution_state(
    session: AsyncSession,
    execution: TaskExecution,
    *,
    now: datetime,
    scope: CancelScope,
    reason: str,
    book: _Book,
    depth: int = 0,
) -> None:
    """按状态矩阵对一个执行落动作：行锁下复核，claim 赢了就按新状态处理。"""
    await lock_task_tree(session, execution)
    await session.refresh(execution, with_for_update=True)
    status = execution.status
    if status not in NON_TERMINAL:
        return
    if status == QUEUED:
        if execution.started_at is not None and scope is CancelScope.FUTURE_ONLY:
            return
        await _terminate(session, execution, reason, book, now)
    elif status == RUNNING:
        if scope is CancelScope.FUTURE_ONLY:
            return
        await _flag_running(session, execution, now)
    else:
        if scope is CancelScope.FUTURE_ONLY:
            return
        # 先落 Execution 终态（锁序 EXECUTION → HUMAN/ASYNC），再收尾 interaction 与远端意图；
        # 终态 CAS 落败说明别人已经收尾，这里不再重复关问题/发取消。
        if not await _terminate(session, execution, reason, book, now):
            return
        if status == WAITING_HUMAN:
            await _close_interactions(session, execution, reason, now)
        if status == WAITING_EXTERNAL:
            items = operation_cancels(execution)
            book.remote.extend(items)
            if items:
                await _async_events(
                    session,
                    execution,
                    items,
                    TaskEventType.CANCELLED,
                    {"state": "REQUESTED", "reason": "ALL_ACTIVE"},
                    now,
                )
    await _cascade(
        session, execution, now=now, scope=scope, reason=reason, book=book, depth=depth
    )


async def _cascade(
    session: AsyncSession,
    parent: TaskExecution,
    *,
    now: datetime,
    scope: CancelScope,
    reason: str,
    book: _Book,
    depth: int,
) -> None:
    if depth >= MAX_CASCADE_DEPTH:
        logger.error(
            "schedule_cancel_cascade_depth_exceeded",
            extra={"execution_id": str(parent.id), "depth": depth},
        )
        return
    for child in await _children(session, parent):
        await apply_execution_state(
            session, child, now=now, scope=scope, reason=reason, book=book, depth=depth + 1
        )


class ScheduleService:
    """Schedule 生命周期动作；调用方负责提交或回滚，本服务不 commit。"""

    def __init__(self, session: AsyncSession, *, now: datetime) -> None:
        self._session = session
        self._now = now

    async def pause(
        self,
        schedule_id: uuid.UUID,
        *,
        tenant_id: str,
        actor_user_id: uuid.UUID | None,
        reason: str | None = None,
    ) -> LifecycleOutcome:
        """ACTIVE → PAUSED：停未来、取消从未开始的执行，已开始与等待保持原样。"""
        schedule = await _lock_schedule(
            self._session,
            tenant_id=tenant_id,
            schedule_id=schedule_id,
            actor_user_id=actor_user_id,
        )
        if schedule.status == PAUSED:
            return LifecycleOutcome(schedule=schedule)
        if schedule.status != ACTIVE:
            raise _state_invalid(schedule.status)
        book = await self._settle_scope(
            schedule, scope=CancelScope.FUTURE_ONLY, reason=reason or "PAUSED"
        )
        schedule.status = PAUSED
        schedule.update_time = self._now
        await self._schedule_event(
            schedule, TaskEventType.PAUSED, {"reason": reason or DEFAULT_REASON}
        )
        await self._session.flush()
        return LifecycleOutcome(schedule=schedule, remote_cancels=tuple(book.remote))

    async def resume(
        self,
        schedule_id: uuid.UUID,
        *,
        tenant_id: str,
        actor_user_id: uuid.UUID | None,
        admission: AdmissionPort | None,
        reason: str | None = None,
    ) -> LifecycleOutcome:
        """PAUSED/NEEDS_ATTENTION → 重新 Admission → ACTIVE，`next_fire_at` 从 now 重算。

        准入在行锁外（网络不持锁），随后在锁内复核「配置版本 + 当前 Revision + 状态」：
        期间被更新/取消/并发恢复过就按新事实处理，绝不把旧准入结果盖回去。
        """
        baseline = await _load_schedule(
            self._session,
            tenant_id=tenant_id,
            schedule_id=schedule_id,
            actor_user_id=actor_user_id,
        )
        expected_status = baseline.status
        expected_version = baseline.config_version
        expected_revision_id = baseline.current_revision_id
        if expected_status == ACTIVE:
            return LifecycleOutcome(schedule=baseline)
        if expected_status not in RESUMABLE_SCHEDULE_STATUSES:
            raise _state_invalid(expected_status)
        revision = await self._current_revision(baseline)
        if revision is None:
            raise _state_invalid(expected_status)
        outcome = await self._admit(baseline, revision, admission)

        schedule = await _lock_schedule(
            self._session,
            tenant_id=tenant_id,
            schedule_id=schedule_id,
            actor_user_id=actor_user_id,
        )
        self._check_resume_baseline(
            schedule,
            status=expected_status,
            config_version=expected_version,
            revision_id=expected_revision_id,
        )
        if not outcome.accepted:
            return await self._refuse_resume(schedule, revision, outcome.error)
        await self._activate(schedule, revision, reason)
        await self._session.flush()
        return LifecycleOutcome(schedule=schedule)

    async def cancel(
        self,
        schedule_id: uuid.UUID,
        *,
        tenant_id: str,
        actor_user_id: uuid.UUID | None,
        scope: CancelScope = CancelScope.FUTURE_ONLY,
        reason: str | None = None,
    ) -> LifecycleOutcome:
        """两档取消：`FUTURE_ONLY` 只停未来；`ALL_ACTIVE` 才终止进行中/等待中。"""
        schedule = await _lock_schedule(
            self._session,
            tenant_id=tenant_id,
            schedule_id=schedule_id,
            actor_user_id=actor_user_id,
        )
        if schedule.status == CANCELLED:
            return LifecycleOutcome(schedule=schedule)
        if schedule.status in TERMINAL_SCHEDULE_STATUSES:
            raise _state_invalid(schedule.status)
        book = await self._settle_scope(
            schedule, scope=scope, reason=reason or "USER_CANCELLED"
        )
        schedule.status = CANCELLED
        schedule.end_reason = ScheduleEndReason.USER_CANCELLED.value
        schedule.ended_at = self._now
        schedule.next_fire_at = None
        schedule.update_time = self._now
        await self._schedule_event(
            schedule,
            TaskEventType.CANCELLED,
            {"scope": str(scope), "reason": reason or DEFAULT_REASON},
        )
        await self._session.flush()
        return LifecycleOutcome(schedule=schedule, remote_cancels=tuple(book.remote))

    async def soft_delete(
        self,
        schedule_id: uuid.UUID,
        *,
        tenant_id: str,
        actor_user_id: uuid.UUID | None,
    ) -> TaskSchedule:
        """管理员清理：只打软删标记，绝不删除 Fire/Execution/事件历史。"""
        schedule = await _lock_schedule(
            self._session,
            tenant_id=tenant_id,
            schedule_id=schedule_id,
            actor_user_id=actor_user_id,
            include_deleted=True,
        )
        if schedule.is_deleted:
            return schedule
        schedule.is_deleted = True
        schedule.update_time = self._now
        await self._session.flush()
        return schedule

    async def _current_revision(
        self, schedule: TaskSchedule
    ) -> TaskScheduleRevision | None:
        if schedule.current_revision_id is None:
            return None
        return await self._session.get(TaskScheduleRevision, schedule.current_revision_id)

    async def _admit(
        self,
        schedule: TaskSchedule,
        revision: TaskScheduleRevision,
        admission: AdmissionPort | None,
    ) -> AdmissionOutcome:
        if admission is None:
            return AdmissionOutcome(
                error=AdmissionError("RESOLVE_UNAVAILABLE", False, "RETRY_LATER")
            )
        try:
            request = admission_request_from_revision(
                revision,
                tenant_id=schedule.tenant_id,
                agent_id=schedule.agent_id,
                actor_user_id=schedule.owner_user_id,
            )
        except AdmissionRejected as exc:
            return AdmissionOutcome(error=exc.error)
        return await admission.admit(
            request,
            retry_transient_once=revision.recurrence_kind == RecurrenceKind.ONCE.value,
        )

    def _check_resume_baseline(
        self,
        schedule: TaskSchedule,
        *,
        status: str,
        config_version: int,
        revision_id: uuid.UUID | None,
    ) -> None:
        if (
            schedule.config_version != config_version
            or schedule.current_revision_id != revision_id
        ):
            raise AppError(ErrorCode.SCHEDULE_VERSION_CONFLICT)
        if schedule.status == ACTIVE:
            return
        if schedule.status != status:
            raise _state_invalid(schedule.status)

    async def _refuse_resume(
        self,
        schedule: TaskSchedule,
        revision: TaskScheduleRevision,
        error: AdmissionError | None,
    ) -> LifecycleOutcome:
        """准入失败：ONCE 进入 `ENDED(ADMISSION_FAILED)`；CRON 保持原状态并报错。"""
        assert error is not None
        if revision.recurrence_kind != RecurrenceKind.ONCE.value:
            raise AppError(
                ErrorCode.SCHEDULE_ADMISSION_FAILED,
                data={
                    "admission_code": error.code,
                    "permanent": error.permanent,
                    "repair": error.repair,
                },
            )
        schedule.status = ENDED
        schedule.end_reason = ScheduleEndReason.ADMISSION_FAILED.value
        schedule.ended_at = self._now
        schedule.next_fire_at = None
        schedule.attention_reason = None
        schedule.attention_since = None
        schedule.update_time = self._now
        await self._schedule_event(
            schedule,
            TaskEventType.ENDED,
            {"reason": ScheduleEndReason.ADMISSION_FAILED.value, "admission_code": error.code},
        )
        await self._session.flush()
        return LifecycleOutcome(schedule=schedule, admission_error=error)

    async def _activate(
        self,
        schedule: TaskSchedule,
        revision: TaskScheduleRevision,
        reason: str | None,
    ) -> None:
        end_reason = await self._resume_end_reason(schedule, revision)
        if end_reason is not None:
            schedule.status = ENDED
            schedule.end_reason = end_reason.value
            schedule.ended_at = self._now
            schedule.next_fire_at = None
            schedule.update_time = self._now
            await self._schedule_event(
                schedule, TaskEventType.ENDED, {"reason": end_reason.value}
            )
            return
        schedule.status = ACTIVE
        schedule.end_reason = None
        schedule.ended_at = None
        schedule.attention_reason = None
        schedule.attention_since = None
        schedule.update_time = self._now
        await self._schedule_event(
            schedule,
            TaskEventType.RESUMED,
            {"reason": reason or DEFAULT_REASON, "next_fire_at": _iso(schedule.next_fire_at)},
        )

    async def _resume_end_reason(
        self, schedule: TaskSchedule, revision: TaskScheduleRevision
    ) -> ScheduleEndReason | None:
        """从 now 重算指针；没有未来槽或策略已到终点即终态，不补发暂停期。"""
        try:
            rule = build_rule(
                kind=revision.recurrence_kind,
                timezone=revision.timezone,
                cron=revision.cron_expr,
                run_at=revision.run_at,
            )
        except ValueError as exc:
            raise AppError(
                ErrorCode.SCHEDULE_STATE_INVALID, data={"reason": "RECURRENCE_INVALID"}
            ) from exc
        try:
            policy = SchedulePolicy.model_validate(revision.schedule_policy or {})
        except ValueError as exc:
            raise AppError(
                ErrorCode.SCHEDULE_STATE_INVALID, data={"reason": "SCHEMA_INCOMPATIBLE"}
            ) from exc
        if policy.max_occurrences is not None and (
            schedule.occurrence_count >= policy.max_occurrences
        ):
            return ScheduleEndReason.EXHAUSTED
        advance = initial_fire_at(rule, self._now)
        if advance.next_fire_at is None:
            if revision.recurrence_kind == RecurrenceKind.ONCE.value:
                return ScheduleEndReason.MISSED
            return ScheduleEndReason.COMPLETED
        if policy.until is not None and advance.next_fire_at > policy.until:
            return ScheduleEndReason.COMPLETED
        schedule.next_fire_at = advance.next_fire_at
        # gap 槽是计划 occurrence：恢复后的首个可触发槽之前被跳过的槽同样计数并留事件。
        schedule.occurrence_count += advance.slots_consumed
        for event in advance.gap_events:
            await self._schedule_event(
                schedule,
                TaskEventType(event.type.value),
                {"local_date": event.local_date, "local_time": event.local_time},
            )
        return None

    async def _schedule_event(
        self, schedule: TaskSchedule, event_type: TaskEventType, payload: dict[str, object]
    ) -> None:
        await append_events(
            self._session,
            [
                TaskEventSeed(
                    tenant_id=schedule.tenant_id,
                    entity_type=EntityType.SCHEDULE,
                    entity_id=schedule.id,
                    event_type=event_type,
                    payload=payload,
                    occurred_at=self._now,
                )
            ],
        )

    async def _settle_scope(
        self, schedule: TaskSchedule, *, scope: CancelScope, reason: str
    ) -> _Book:
        book = _Book()
        for execution in await _root_executions(self._session, schedule):
            await apply_execution_state(
                self._session, execution, now=self._now, scope=scope, reason=reason, book=book
            )
        return book


class ExecutionService:
    """单条 Execution 的取消（API-11）：与 Schedule 路径共用同一套矩阵与锁序。"""

    def __init__(self, session: AsyncSession, *, now: datetime) -> None:
        self._session = session
        self._now = now

    async def cancel(
        self,
        execution_id: uuid.UUID,
        *,
        tenant_id: str,
        actor_user_id: uuid.UUID | None,
        reason: str | None = None,
    ) -> LifecycleOutcome:
        execution = await self._load(tenant_id, execution_id, actor_user_id)
        # 锁序：operation → Schedule → Fire → root → parent，然后才是本行。
        await lock_task_tree(self._session, execution)
        await self._session.refresh(execution, with_for_update=True)
        if execution.status not in NON_TERMINAL:
            if execution.status == CANCELLED:
                return LifecycleOutcome(execution=execution)
            raise _state_invalid(execution.status)
        book = _Book()
        await apply_execution_state(
            self._session,
            execution,
            now=self._now,
            scope=CancelScope.ALL_ACTIVE,
            reason=reason or DEFAULT_REASON,
            book=book,
        )
        await self._session.flush()
        return LifecycleOutcome(execution=execution, remote_cancels=tuple(book.remote))

    async def _load(
        self, tenant_id: str, execution_id: uuid.UUID, actor_user_id: uuid.UUID | None
    ) -> TaskExecution:
        execution = await self._session.scalar(
            select(TaskExecution).where(
                TaskExecution.id == execution_id,
                TaskExecution.tenant_id == tenant_id,
                TaskExecution.is_deleted.is_(False),
            )
        )
        if execution is None or (
            actor_user_id is not None and execution.actor_user_id != actor_user_id
        ):
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        return execution


__all__ = [
    "AdmissionPort",
    "ExecutionService",
    "LifecycleOutcome",
    "RemoteCancel",
    "ScheduleService",
    "apply_execution_state",
    "operation_cancels",
]
