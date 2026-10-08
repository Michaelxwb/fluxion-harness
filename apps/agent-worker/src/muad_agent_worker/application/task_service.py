from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from muad_api import AppError
from muad_api.audit import SENSITIVE_KEY_MARKERS
from muad_api.error_codes import ErrorCode
from muad_common import SharedSettings
from muad_contracts import (
    REQUIRED_SNAPSHOT_KEYS,
    CreateTaskRequest,
    DeliveryMode,
    DeliveryStatus,
    TaskStatus,
    TerminalStatus,
    TriggerType,
)
from muad_contracts.platform_settings import TaskSettings
from sqlalchemy import false, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.task import TaskEvent, TaskExecution
from .delivery_routes import upsert_delivery_route
from .platform_settings import resolve_platform_settings
from .ports import NullPlatformSettingsClient, PlatformSettingsClient
from .runtime_operations import prepare_submission
from .task_events import TaskEventType, append_event
from .terminal_tasks import TerminalChange, lock_task_tree, write_terminal

INITIAL_PRIORITY = 100
EXECUTION_MODE_ASYNC = "ASYNC"
TASK_TYPE_SKILL = "SKILL"
TERMINAL_STATUSES = (str(TaskStatus.COMPLETED), str(TaskStatus.FAILED), str(TaskStatus.CANCELLED))
CANCELABLE_STATUSES = (str(TaskStatus.QUEUED), str(TaskStatus.WAITING))


def _has_sensitive_key(value: Any) -> bool:
    """递归查找密钥类字段名。

    按后缀匹配而不是子串匹配：密钥字段的命名总以 marker 结尾（`api_key`、
    `bot_secret`、`access_token`），而 `max_tokens`、`prompt_template_version`
    这类合法字段不应被误判。
    """
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower().endswith(SENSITIVE_KEY_MARKERS) or _has_sensitive_key(item):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(_has_sensitive_key(item) for item in value)
    return False


def validate_execution_snapshot(snapshot: dict[str, Any]) -> None:
    """快照必须冻结必需版本键，且不得携带任何密钥（设计 §3.3.3）。"""
    if any(key not in snapshot for key in REQUIRED_SNAPSHOT_KEYS):
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
    if _has_sensitive_key(snapshot):
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)


def _source_identity(payload: CreateTaskRequest) -> dict[str, object]:
    source = payload.runtime_operation
    return {
        "source_operation_id": source.operation_id if source else None,
        "source_tool_call_id": source.source_tool_call_id if source else None,
        "completion_mode": source.completion_mode if source else None,
    }


class TaskService:
    def __init__(
        self,
        session: AsyncSession,
        settings: SharedSettings | None = None,
        settings_client: PlatformSettingsClient | None = None,
    ) -> None:
        self._session = session
        self._settings = settings or SharedSettings()
        self._settings_client: PlatformSettingsClient = settings_client or NullPlatformSettingsClient()

    async def create(self, payload: CreateTaskRequest) -> TaskExecution:
        validate_execution_snapshot(payload.execution_snapshot)
        operation = await prepare_submission(self._session, payload)
        existing = await self._find_by_idempotency_key(payload.tenant_id, payload.idempotency_key)
        if existing is not None:
            return existing
        # 任务创建边界取一次平台设置快照：新 Task 用它冻结默认，存量行一律不动。
        task_settings = await self._task_settings(payload.tenant_id)
        route_id = await self._resolve_route(payload)
        values = self._creation_values(payload, task_settings, route_id)
        statement = (
            pg_insert(TaskExecution)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=[TaskExecution.tenant_id, TaskExecution.idempotency_key],
                index_where=TaskExecution.is_deleted == false(),
            )
            .returning(TaskExecution.id)
        )
        inserted_id = (await self._session.execute(statement)).scalar_one_or_none()
        if inserted_id is None:
            deduped = await self._find_by_idempotency_key(payload.tenant_id, payload.idempotency_key)
            if deduped is None:
                raise AppError(ErrorCode.COMMON_CONFLICT)
            return deduped
        await append_event(
            self._session,
            tenant_id=payload.tenant_id,
            task_id=inserted_id,
            event_type=TaskEventType.CREATED,
            payload={"idempotency_key": payload.idempotency_key},
        )
        await self._session.flush()
        task = await self._session.get(TaskExecution, inserted_id)
        if task is None:
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
        if operation is not None:
            operation.task_id = task.id
            await self._session.flush()
        return task

    def _creation_values(
        self, payload: CreateTaskRequest, task_settings: TaskSettings, route_id: uuid.UUID | None
    ) -> dict[str, object]:
        task_id = uuid.uuid4()
        now = datetime.now(UTC)
        return {
            "id": task_id,
            "tenant_id": payload.tenant_id,
            "source_run_id": payload.source_run_id,
            **_source_identity(payload),
            "agent_id": payload.agent_id,
            "actor_user_id": payload.actor_user_id,
            "intent_key": payload.intent_key,
            "skill_id": payload.skill_id,
            "skill_artifact_id": payload.skill_artifact_id,
            "trigger_type": str(TriggerType.IMMEDIATE),
            "execution_mode": EXECUTION_MODE_ASYNC,
            "task_type": TASK_TYPE_SKILL,
            "status": str(TaskStatus.QUEUED),
            "input_json": payload.input,
            "execution_snapshot_schema_version": payload.execution_snapshot_schema_version,
            "execution_snapshot_json": payload.execution_snapshot,
            "snapshot_hash": payload.snapshot_hash,
            "idempotency_key": payload.idempotency_key,
            "priority": INITIAL_PRIORITY,
            "attempt": 0,
            "max_attempts": 1 if payload.runtime_operation else task_settings.max_attempts,
            "not_before": now,
            "deadline_at": _task_deadline(payload, task_settings, now),
            "delivery_route_id": route_id,
            "delivery_mode": str(payload.delivery_mode),
            "delivery_status": str(self._initial_delivery_status(payload.delivery_mode)),
            "delivery_key": f"task:{task_id}:final",
            "delivery_attempts": 0,
        }

    async def get(
        self, tenant_id: str, task_id: uuid.UUID, *, actor_user_id: uuid.UUID | None = None
    ) -> TaskExecution:
        """跨租户或（声明 actor 时）非本人的 Task 一律视为不存在，不泄露存在性。"""
        conditions: list[Any] = [
            TaskExecution.id == task_id,
            TaskExecution.tenant_id == tenant_id,
            TaskExecution.is_deleted.is_(False),
        ]
        if actor_user_id is not None:
            conditions.append(TaskExecution.actor_user_id == actor_user_id)
        task = (await self._session.execute(select(TaskExecution).where(*conditions))).scalar_one_or_none()
        if task is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        return task

    async def detail(
        self, tenant_id: str, task_id: uuid.UUID, *, actor_user_id: uuid.UUID | None = None
    ) -> tuple[TaskExecution, list[TaskEvent], list[TaskExecution]]:
        """任务详情：本体 + Timeline（seq 升序）+ 直接子任务。

        三条有界查询而不是按行展开，避免列表/详情出现 N+1。
        """
        task = await self.get(tenant_id, task_id, actor_user_id=actor_user_id)
        events = (
            (
                await self._session.execute(
                    select(TaskEvent)
                    .where(TaskEvent.task_id == task.id, TaskEvent.is_deleted.is_(False))
                    .order_by(TaskEvent.seq.asc())
                )
            )
            .scalars()
            .all()
        )
        children = (
            (
                await self._session.execute(
                    select(TaskExecution)
                    .where(
                        TaskExecution.parent_id == task.id,
                        TaskExecution.tenant_id == tenant_id,
                        TaskExecution.is_deleted.is_(False),
                    )
                    .order_by(TaskExecution.create_time.asc())
                )
            )
            .scalars()
            .all()
        )
        return task, list(events), list(children)

    async def list(
        self,
        tenant_id: str,
        *,
        status: TaskStatus | None = None,
        trigger_type: TriggerType | None = None,
        agent_id: uuid.UUID | None = None,
        actor_user_id: uuid.UUID | None = None,
        skill_id: uuid.UUID | None = None,
        schedule_id: uuid.UUID | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        deadline_from: datetime | None = None,
        deadline_to: datetime | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[TaskExecution], int]:
        conditions: list[Any] = [
            TaskExecution.tenant_id == tenant_id,
            TaskExecution.is_deleted.is_(False),
        ]
        if status is not None:
            conditions.append(TaskExecution.status == str(status))
        if trigger_type is not None:
            conditions.append(TaskExecution.trigger_type == str(trigger_type))
        if agent_id is not None:
            conditions.append(TaskExecution.agent_id == agent_id)
        if actor_user_id is not None:
            conditions.append(TaskExecution.actor_user_id == actor_user_id)
        if skill_id is not None:
            conditions.append(TaskExecution.skill_id == skill_id)
        if schedule_id is not None:
            conditions.append(TaskExecution.schedule_id == schedule_id)
        if start_time is not None:
            conditions.append(TaskExecution.create_time >= start_time)
        if end_time is not None:
            conditions.append(TaskExecution.create_time <= end_time)
        if deadline_from is not None:
            conditions.append(TaskExecution.deadline_at >= deadline_from)
        if deadline_to is not None:
            conditions.append(TaskExecution.deadline_at <= deadline_to)
        items = (
            (
                await self._session.execute(
                    select(TaskExecution)
                    .where(*conditions)
                    .order_by(TaskExecution.create_time.desc())
                    .limit(page_size)
                    .offset((page - 1) * page_size)
                )
            )
            .scalars()
            .all()
        )
        total = (
            await self._session.execute(select(func.count()).select_from(TaskExecution).where(*conditions))
        ).scalar_one()
        return list(items), int(total)

    async def child_progress(
        self, tenant_id: str, parent_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, tuple[int, int]]:
        """本页父任务的子任务进度 `{parent_id: (总数, 已终态数)}`。

        列表页要展示「子任务进度」，逐行查询即 N+1；这里用一次 `GROUP BY parent_id`
        取回整页（`parent_ids` 最多一页 100 个）。没有子任务的行不出现在结果里。
        """
        if not parent_ids:
            return {}
        finished = func.count().filter(TaskExecution.status.in_(TERMINAL_STATUSES))
        rows = (
            await self._session.execute(
                select(TaskExecution.parent_id, func.count(), finished)
                .where(
                    TaskExecution.tenant_id == tenant_id,
                    TaskExecution.parent_id.in_(parent_ids),
                    TaskExecution.is_deleted.is_(False),
                )
                .group_by(TaskExecution.parent_id)
            )
        ).all()
        return {row[0]: (int(row[1]), int(row[2])) for row in rows}

    async def cancel(
        self, tenant_id: str, task_id: uuid.UUID, *, actor_user_id: uuid.UUID | None = None
    ) -> tuple[str, bool]:
        """Lock operation/ancestors before the Task and persist cooperative cancellation.

        Idle Tasks settle atomically with the terminal event/outbox; RUNNING Tasks
        retain their state and get cancel_requested. The row lock covers the status
        decision so a concurrent claim cannot make a cancel intent disappear.
        """
        task = await self.get(tenant_id, task_id, actor_user_id=actor_user_id)
        await lock_task_tree(self._session, task)
        await self._session.refresh(task, with_for_update=True)
        if task.status in TERMINAL_STATUSES:
            if task.status == str(TaskStatus.CANCELLED):
                return str(TaskStatus.CANCELLED), bool(task.cancel_requested)
            raise AppError(ErrorCode.REVISION_CONFLICT)
        now = datetime.now(UTC)
        settled = await self._apply_cancel_intent(task, now)
        if settled is None:
            # 唯一会落空的情况：并发把它推进到终态了（语句只匹配非终态行）。
            await self._session.refresh(task)
            if task.status == str(TaskStatus.CANCELLED):
                return str(TaskStatus.CANCELLED), True
            raise AppError(ErrorCode.REVISION_CONFLICT)
        await self._session.refresh(task)
        await self._record_cancel(task, settled=settled, now=now)
        return (str(TaskStatus.CANCELLED) if settled else str(TaskStatus.RUNNING)), True

    async def _apply_cancel_intent(self, task: TaskExecution, now: datetime) -> bool | None:
        """原子落下取消意图，返回「是否已就地为终态」；行已不在非终态时返回 None。"""
        if task.status in CANCELABLE_STATUSES:
            written = await write_terminal(
                self._session,
                task,
                TerminalChange(status=TerminalStatus.CANCELLED, cancel_requested=True),
                now,
                conditions=(TaskExecution.status.in_(CANCELABLE_STATUSES),),
            )
            return True if written else None
        changed = await self._session.scalar(
            update(TaskExecution)
            .where(
                TaskExecution.id == task.id,
                TaskExecution.tenant_id == task.tenant_id,
                TaskExecution.status == str(TaskStatus.RUNNING),
                TaskExecution.is_deleted.is_(False),
            )
            .values(cancel_requested=True, update_time=now)
            .returning(TaskExecution.id)
        )
        return False if changed is not None else None

    async def _record_cancel(self, task: TaskExecution, *, settled: bool, now: datetime) -> None:
        """写事件并按需级联——状态本身已由 `_apply_cancel_intent` 落库，这里只记随附事实。"""
        # 局部导入：batch_fanout/batch_fanin 依赖本模块的常量，顶层导入会形成环。
        from .batch_fanin import settle_child
        from .task_cancel import cancel_children

        if not settled:
            await append_event(
                self._session,
                tenant_id=task.tenant_id,
                task_id=task.id,
                event_type=TaskEventType.CANCEL_REQUESTED,
            )
        await cancel_children(self._session, task, now)
        if settled:
            # 取消掉的是一个 Parent：它自己终态了，扇入要看一眼 Child 是否都已终态。
            await settle_child(self._session, task, now)

    async def _find_by_idempotency_key(self, tenant_id: str, key: str) -> TaskExecution | None:
        return (
            await self._session.execute(
                select(TaskExecution).where(
                    TaskExecution.tenant_id == tenant_id,
                    TaskExecution.idempotency_key == key,
                    TaskExecution.is_deleted.is_(False),
                )
            )
        ).scalar_one_or_none()

    async def _task_settings(self, tenant_id: str) -> TaskSettings:
        """任务默认的唯一来源：该租户当前平台设置（源不可读即明确失败，RULE-06）。"""
        snapshot = await self._settings_client.fetch_snapshot(tenant_id=tenant_id)
        return resolve_platform_settings(
            snapshot, batch_platform_limit=self._settings.batch_platform_limit
        ).task

    async def _resolve_route(self, payload: CreateTaskRequest) -> uuid.UUID | None:
        if payload.delivery_route is None:
            return None
        return await upsert_delivery_route(
            self._session,
            tenant_id=payload.tenant_id,
            platform_user_id=payload.actor_user_id,
            route=payload.delivery_route,
        )

    def _initial_delivery_status(self, mode: DeliveryMode) -> DeliveryStatus:
        if mode is DeliveryMode.FINAL_ONLY:
            return DeliveryStatus.PENDING
        return DeliveryStatus.NONE


def _task_deadline(payload: CreateTaskRequest, settings: TaskSettings, now: datetime) -> datetime:
    deadline = now + timedelta(hours=settings.default_deadline_hours)
    source = payload.runtime_operation
    if source is None or source.completion_mode.value == "DETACH":
        return deadline
    budget = payload.execution_snapshot.get("budget")
    value = budget.get("run_deadline_at") if isinstance(budget, dict) else None
    if not isinstance(value, str):
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
    try:
        run_deadline = datetime.fromisoformat(value)
    except ValueError as exc:
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR) from exc
    if run_deadline.tzinfo is None:
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
    return min(deadline, run_deadline)
