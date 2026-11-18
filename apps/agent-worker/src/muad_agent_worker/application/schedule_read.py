"""定时任务的有界读取面（scheduled-task-v2 §3.3.12 / API-07/08/09）。

只做投影，不做业务：所有查询都按 `tenant + owner（+ 授权 Agent 集合）` 过滤，跨租户/跨用户/
撤销授权一律不可见（返回 404 的判定在 API 层）。列表页用**固定条数的批量查询**拼装名称、
最近 Fire 与待处理标记，不做 N+1；分页排序稳定（`update_time DESC, id`），不会重复或丢页。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, cast

import sqlalchemy as sa
from muad_contracts import (
    AgenticBudget,
    DeliveryIntent,
    DraftContext,
    DstEvent,
    DstEventType,
    ExecutionDetail,
    FailureClass,
    FireHistoryPage,
    FireItem,
    HistoryRetention,
    LastFireSummary,
    PendingAttentionItem,
    PendingHumanItem,
    PendingPage,
    ScheduleDetail,
    ScheduleEndReason,
    ScheduleListItem,
    ScheduleMode,
    SchedulePolicy,
    TaskArtifactDeliveryRef,
    TaskArtifactRef,
    TaskCenterBadges,
    TaskChildItem,
    TaskChildPage,
    TaskResultCounts,
    TaskStatus,
    TaskType,
    WaitKind,
)
from muad_contracts.enums import ScheduleStatus
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.db_clock import database_now
from ..infrastructure.models.task import (
    DeliveryRoute,
    ScheduleFire,
    TaskDelivery,
    TaskEvent,
    TaskExecution,
    TaskHumanInteraction,
    TaskMessageOutbox,
    TaskSchedule,
    TaskScheduleRevision,
)

DST_EVENT_TYPES = ("DST_GAP_SKIPPED", "DST_FOLD_SECOND_SKIPPED")

OUTBOX_PENDING = "PENDING"
OUTBOX_FAILED = "FAILED"
#: `message_delivery.status` → 任务中心词表；DELIVERING 是“正在发”，对用户仍是 PENDING。
DELIVERY_STATUS_MAP = {
    "PENDING": "PENDING",
    "DELIVERING": "PENDING",
    "DELIVERED": "DELIVERED",
    "DEGRADED": "DEGRADED",
    "UNKNOWN": "UNKNOWN",
    "FAILED": "FAILED",
}
#: 安全标签：只显示通道名，不拼接 bot/user/conversation 等内部 ID。
TARGET_LABELS = {"WECOM": "企微", "WEBCHAT": "Web"}
UNKNOWN_TARGET_LABEL = "未知目标"


def _task_type(value: str) -> TaskType:
    try:
        return TaskType(value)
    except ValueError:
        return TaskType.SKILL


def _skill_name(execution: TaskExecution) -> str:
    snapshot = execution.execution_snapshot_json or {}
    skills = snapshot.get("skills") if isinstance(snapshot, dict) else None
    if isinstance(skills, list) and skills:
        first = skills[0]
        if isinstance(first, dict):
            name = first.get("name") or first.get("key")
            if isinstance(name, str) and name.strip():
                return name.strip()
    return f"{execution.task_type} {str(execution.id)[:8]}"


def _result_counts(result: Any) -> TaskResultCounts:
    row = result if isinstance(result, dict) else {}
    values = [row.get(key) for key in ("total", "succeeded", "failed", "cancelled")]
    return TaskResultCounts(
        total=values[0] if isinstance(values[0], int) and values[0] >= 0 else 0,
        succeeded=values[1] if isinstance(values[1], int) and values[1] >= 0 else 0,
        failed=values[2] if isinstance(values[2], int) and values[2] >= 0 else 0,
        cancelled=values[3] if isinstance(values[3], int) and values[3] >= 0 else 0,
    )


def _partial_failure(execution: TaskExecution, counts: TaskResultCounts) -> bool:
    row = execution.result_json if isinstance(execution.result_json, dict) else {}
    return (
        execution.status == TaskStatus.COMPLETED.value
        and execution.task_type == TaskType.BATCH.value
        and row.get("mode") == "BEST_EFFORT"
        and counts.failed > 0
    )


def _error_summary(execution: TaskExecution) -> str | None:
    value = execution.error_message or ""
    first = value.splitlines()[0].strip() if value else ""
    if not first:
        return execution.error_code
    return first[:200]


def _target_label(route_json: Any) -> str:
    """投递目标的安全标签（不暴露 route 里的内部 ID）。"""
    channel = route_json.get("channel") if isinstance(route_json, dict) else None
    if not isinstance(channel, str) or not channel:
        return UNKNOWN_TARGET_LABEL
    return TARGET_LABELS.get(channel, channel)


def _payload_artifact_ids(payload_json: Any) -> list[uuid.UUID]:
    """消息快照里的附件身份；缺 artifact_id 的历史/坏载荷跳过而不是伪造目标。"""
    payload = payload_json if isinstance(payload_json, dict) else {}
    raw = payload.get("attachments")
    if not isinstance(raw, list):
        return []
    artifact_ids: list[uuid.UUID] = []
    for item in raw:
        value = item.get("artifact_id") if isinstance(item, dict) else None
        if value is None:
            continue
        try:
            artifact_ids.append(uuid.UUID(str(value)))
        except ValueError:
            continue
    return artifact_ids


def _delivery_ref(
    *,
    target_key: str,
    target_label: str,
    status: str,
    error_code: str | None,
    retryable: bool,
    fallback_url: str | None = None,
) -> TaskArtifactDeliveryRef:
    return TaskArtifactDeliveryRef(
        target_key=target_key,
        target_label=target_label,
        status=status,  # type: ignore[arg-type]
        error_code=error_code,
        retryable=retryable,
        fallback_url=fallback_url,
    )


def _child_label(execution: TaskExecution) -> str:
    payload = execution.input_json if isinstance(execution.input_json, dict) else {}
    for key in ("label", "name", "title", "customer", "target", "item_key"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:120]
    if execution.item_key:
        return execution.item_key
    return f"子任务 {str(execution.id)[:8]}"


@dataclass(frozen=True, slots=True)
class DeliveryRouteScope:
    """一条入站消息的精确来源：只匹配**推到这个会话**的问题（design §3.2.6）。"""

    channel: str
    bot_id: str
    external_user_id: str | None = None
    external_conversation_id: str | None = None


def _dependencies(mode: str, execution_spec: dict[str, Any]) -> list[str]:
    if mode == ScheduleMode.REMINDER.value:
        return []
    if mode == ScheduleMode.SKILL.value:
        skill_id = execution_spec.get("skill_id")
        return [f"SKILL:{skill_id}"] if skill_id else []
    allowlist: Sequence[Any] = execution_spec.get("tool_allowlist") or []
    return [str(entry) for entry in allowlist]


def _budget(mode: str, execution_spec: dict[str, Any]) -> AgenticBudget | None:
    if mode != ScheduleMode.AGENTIC.value:
        return None
    raw = execution_spec.get("budget")
    if not isinstance(raw, dict):
        return None
    try:
        return AgenticBudget.model_validate(raw)
    except ValueError:
        return None


def _context(context_json: dict[str, Any]) -> DraftContext:
    return DraftContext(
        parameters=dict(context_json.get("parameters") or {}),
        output=dict(context_json.get("output") or {}),
    )


@dataclass(frozen=True, slots=True)
class HistoryWindow:
    """读面用于保留提示的清理边界（来源是平台设置的 schedule 分组，不在此重述默认值）。"""

    fire_history_days: int
    execution_history_days: int


class ScheduleReadService:
    """读面查询集合；每个方法自我限定在调用方给出的可见范围内。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _visible_ids(
        self, *, tenant_id: str, owner_user_id: uuid.UUID | None, agent_ids: Sequence[uuid.UUID] | None
    ) -> list[uuid.UUID]:
        conditions: list[Any] = [
            TaskSchedule.tenant_id == tenant_id,
            TaskSchedule.is_deleted.is_(False),
        ]
        if owner_user_id is not None:
            conditions.append(TaskSchedule.owner_user_id == owner_user_id)
        if agent_ids is not None:
            if not agent_ids:
                return []
            conditions.append(TaskSchedule.agent_id.in_(list(agent_ids)))
        rows = await self._session.scalars(select(TaskSchedule.id).where(*conditions))
        return list(rows.all())

    async def list_page(
        self,
        *,
        tenant_id: str,
        owner_user_id: uuid.UUID | None = None,
        agent_ids: Sequence[uuid.UUID] | None = None,
        agent_id: uuid.UUID | None = None,
        status: ScheduleStatus | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[ScheduleListItem], int]:
        conditions: list[Any] = [
            TaskSchedule.tenant_id == tenant_id,
            TaskSchedule.is_deleted.is_(False),
        ]
        if owner_user_id is not None:
            conditions.append(TaskSchedule.owner_user_id == owner_user_id)
        if agent_ids is not None:
            if not agent_ids:
                return [], 0
            conditions.append(TaskSchedule.agent_id.in_(list(agent_ids)))
        if agent_id is not None:
            conditions.append(TaskSchedule.agent_id == agent_id)
        if status is not None:
            conditions.append(TaskSchedule.status == str(status))

        total = int(
            await self._session.scalar(
                select(func.count()).select_from(TaskSchedule).where(*conditions)
            )
            or 0
        )
        rows = (
            await self._session.execute(
                select(TaskSchedule, TaskScheduleRevision)
                .outerjoin(
                    TaskScheduleRevision,
                    TaskScheduleRevision.id == TaskSchedule.current_revision_id,
                )
                .where(*conditions)
                .order_by(TaskSchedule.update_time.desc(), TaskSchedule.id)
                .limit(page_size)
                .offset((page - 1) * page_size)
            )
        ).all()
        schedule_ids = [schedule.id for schedule, _ in rows]
        last_fires = await self._last_fires(schedule_ids)
        waiting = await self._pending_human_schedule_ids(schedule_ids)
        items: list[ScheduleListItem] = []
        for schedule, revision in rows:
            # 没有当前 Revision 的 Schedule 是中间态（确认事务提交前后），不伪造配置。
            if revision is None:
                continue
            items.append(
                ScheduleListItem(
                    schedule_id=schedule.id,
                    agent_id=schedule.agent_id,
                    actor_user_id=schedule.owner_user_id,
                    name=schedule.name,
                    mode=ScheduleMode(revision.mode),
                    status=ScheduleStatus(schedule.status),
                    timezone=revision.timezone,
                    config_version=schedule.config_version,
                    next_fire_at=schedule.next_fire_at,
                    last_fire=last_fires.get(schedule.id),
                    needs_attention=schedule.status == ScheduleStatus.NEEDS_ATTENTION.value,
                    pending_human=schedule.id in waiting,
                )
            )
        return items, total

    async def _last_fires(
        self, schedule_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, LastFireSummary]:
        if not schedule_ids:
            return {}
        rows = (
            await self._session.execute(
                select(ScheduleFire, TaskExecution.status)
                .outerjoin(TaskExecution, TaskExecution.id == ScheduleFire.execution_id)
                .where(ScheduleFire.schedule_id.in_(list(schedule_ids)))
                .order_by(ScheduleFire.schedule_id, ScheduleFire.scheduled_at.desc())
            )
        ).all()
        latest: dict[uuid.UUID, LastFireSummary] = {}
        for fire, execution_status in rows:
            if fire.schedule_id in latest:
                continue
            latest[fire.schedule_id] = LastFireSummary(
                scheduled_at=fire.scheduled_at,
                status=fire.status,
                execution_status=TaskStatus(execution_status) if execution_status else None,
                missed_count=fire.missed_count,
                dst_resolution=fire.dst_resolution,
            )
        return latest

    async def _pending_human_schedule_ids(
        self, schedule_ids: Sequence[uuid.UUID]
    ) -> set[uuid.UUID]:
        if not schedule_ids:
            return set()
        rows = await self._session.scalars(
            select(TaskExecution.schedule_id)
            .join(TaskHumanInteraction, TaskHumanInteraction.execution_id == TaskExecution.id)
            .where(
                TaskExecution.schedule_id.in_(list(schedule_ids)),
                TaskHumanInteraction.status == "OPEN",
                TaskHumanInteraction.is_deleted.is_(False),
            )
        )
        return {row for row in rows.all() if row is not None}

    async def detail(self, *, schedule: TaskSchedule, revision: TaskScheduleRevision) -> ScheduleDetail:
        last = (await self._last_fires([schedule.id])).get(schedule.id)
        waiting = await self._pending_human_schedule_ids([schedule.id])
        mode = ScheduleMode(revision.mode)
        spec = dict(revision.execution_spec or {})
        return ScheduleDetail(
            schedule_id=schedule.id,
            agent_id=schedule.agent_id,
            actor_user_id=schedule.owner_user_id,
            name=schedule.name,
            mode=mode,
            status=ScheduleStatus(schedule.status),
            timezone=revision.timezone,
            config_version=schedule.config_version,
            next_fire_at=schedule.next_fire_at,
            last_fire=last,
            needs_attention=schedule.status == ScheduleStatus.NEEDS_ATTENTION.value,
            pending_human=schedule.id in waiting,
            revision_no=revision.revision_no,
            instruction=revision.instruction_raw,
            normalized_goal=revision.normalized_goal,
            context=_context(revision.context_json or {}),
            dependencies=_dependencies(mode.value, spec),
            delivery_intent=DeliveryIntent(revision.delivery_intent),
            policy=SchedulePolicy.model_validate(revision.schedule_policy or {}),
            budget=_budget(mode.value, spec),
            memory_mode=spec.get("memory_mode"),
            end_reason=ScheduleEndReason(schedule.end_reason) if schedule.end_reason else None,
            attention_reason=schedule.attention_reason,
        )

    async def fire_history(
        self,
        *,
        schedule: TaskSchedule,
        page: int = 1,
        page_size: int = 20,
        window: HistoryWindow | None = None,
    ) -> FireHistoryPage:
        conditions = [
            ScheduleFire.tenant_id == schedule.tenant_id,
            ScheduleFire.schedule_id == schedule.id,
            ScheduleFire.is_deleted.is_(False),
        ]
        total = int(
            await self._session.scalar(
                select(func.count()).select_from(ScheduleFire).where(*conditions)
            )
            or 0
        )
        rows = (
            await self._session.execute(
                select(ScheduleFire, TaskExecution.status)
                .outerjoin(TaskExecution, TaskExecution.id == ScheduleFire.execution_id)
                .where(*conditions)
                .order_by(ScheduleFire.scheduled_at.desc(), ScheduleFire.id)
                .limit(page_size)
                .offset((page - 1) * page_size)
            )
        ).all()
        items = [
            FireItem(
                fire_id=fire.id,
                scheduled_at=fire.scheduled_at,
                status=fire.status,
                skip_reason=fire.skip_reason,
                missed_count=fire.missed_count,
                dst_resolution=fire.dst_resolution,
                admission_error_code=fire.admission_error_code,
                execution_id=fire.execution_id,
                execution_status=TaskStatus(execution_status) if execution_status else None,
            )
            for fire, execution_status in rows
        ]
        # DST 事件不是 Fire：它们在同一张事件表里，按页的时间窗对齐后一并返回。
        timezone = await self._schedule_timezone(schedule)
        dst_events = await self._dst_events(
            schedule=schedule, page_items=items, timezone=timezone
        )
        return FireHistoryPage(
            items=items,
            dst_events=dst_events,
            page=page,
            page_size=page_size,
            total=total,
            retention=await self._retention(schedule=schedule, window=window),
        )

    async def _retention(
        self, *, schedule: TaskSchedule, window: HistoryWindow | None
    ) -> HistoryRetention | None:
        """保留提示：`oldest_retained_at` 说明"还能查到多早"，`cleaned_before` 说明清理边界。

        没有窗口（调用方不取设置）就不编造提示；有窗口时两次读都是按索引的单行/有界查询。
        """
        if window is None:
            return None
        now = await database_now(self._session)
        oldest = await self._session.scalar(
            select(func.min(ScheduleFire.scheduled_at)).where(
                ScheduleFire.tenant_id == schedule.tenant_id,
                ScheduleFire.schedule_id == schedule.id,
                ScheduleFire.is_deleted.is_(False),
            )
        )
        return HistoryRetention(
            fire_history_days=window.fire_history_days,
            execution_history_days=window.execution_history_days,
            cleaned_before=now - timedelta(days=window.fire_history_days),
            oldest_retained_at=oldest,
        )

    async def _schedule_timezone(self, schedule: TaskSchedule) -> str:
        """DST 事件本身只记本地日期/时刻，时区来自它所属的 Revision。"""
        timezone = await self._session.scalar(
            select(TaskScheduleRevision.timezone).where(
                TaskScheduleRevision.id == schedule.current_revision_id
            )
        )
        return str(timezone or "UTC")

    async def _dst_events(
        self,
        *,
        schedule: TaskSchedule,
        page_items: Sequence[FireItem],
        timezone: str,
    ) -> list[DstEvent]:
        if not page_items:
            return []
        newest = max(item.scheduled_at for item in page_items)
        oldest = min(item.scheduled_at for item in page_items)
        rows = (
            await self._session.scalars(
                select(TaskEvent)
                .where(
                    TaskEvent.tenant_id == schedule.tenant_id,
                    TaskEvent.entity_type == "SCHEDULE",
                    TaskEvent.entity_id == schedule.id,
                    TaskEvent.event_type.in_(DST_EVENT_TYPES),
                    TaskEvent.occurred_at >= oldest,
                    TaskEvent.occurred_at <= newest,
                    TaskEvent.is_deleted.is_(False),
                )
                .order_by(TaskEvent.occurred_at.desc(), TaskEvent.seq.desc())
            )
        ).all()
        events: list[DstEvent] = []
        for row in rows:
            payload = dict(row.payload_json or {})
            events.append(
                DstEvent(
                    event_id=row.id,
                    event_type=DstEventType(row.event_type),
                    local_date=str(payload.get("local_date") or ""),
                    local_time=str(payload.get("local_time") or ""),
                    timezone=timezone,
                    occurred_at=row.occurred_at,
                )
            )
        return events

    async def execution_detail(
        self,
        *,
        execution: TaskExecution,
        children_page: int = 1,
        children_page_size: int = 20,
    ) -> ExecutionDetail:
        """Execution 详情：等待载荷只在 `WAITING_EXTERNAL` 时可见（其余状态是内部事实）。"""
        waiting = execution.status == TaskStatus.WAITING_EXTERNAL.value
        counts = _result_counts(execution.result_json)
        result_artifacts, artifact_total = await self._artifacts_with_children(execution)
        return ExecutionDetail(
            execution_id=execution.id,
            agent_id=execution.agent_id,
            task_type=_task_type(execution.task_type),
            skill_name=_skill_name(execution),
            parent_id=execution.parent_id,
            item_key=execution.item_key,
            status=TaskStatus(execution.status),
            wait_kind=WaitKind(execution.wait_kind) if waiting and execution.wait_kind else None,
            wait_payload=dict(execution.wait_payload_json or {}) if waiting else None,
            result=dict(execution.result_json) if execution.result_json is not None else None,
            result_artifacts=result_artifacts,
            artifact_total=artifact_total,
            counts=counts,
            partial_failure=_partial_failure(execution, counts),
            children=await self._children_page(
                execution, page=children_page, page_size=children_page_size
            ),
            error_code=execution.error_code,
            error_message=execution.error_message,
            failure_class=(
                FailureClass(execution.failure_class) if execution.failure_class else None
            ),
            started_at=execution.started_at,
            finished_at=execution.finished_at,
            snapshot_schema_version=execution.execution_snapshot_schema_version,
            snapshot_bytes=execution.snapshot_bytes,
        )

    async def _children_page(
        self, execution: TaskExecution, *, page: int, page_size: int
    ) -> TaskChildPage:
        conditions = (
            TaskExecution.parent_id == execution.id,
            TaskExecution.tenant_id == execution.tenant_id,
            TaskExecution.is_deleted.is_(False),
        )
        total = int(
            await self._session.scalar(
                select(func.count()).select_from(TaskExecution).where(*conditions)
            )
            or 0
        )
        rows = (
            await self._session.scalars(
                select(TaskExecution)
                .where(*conditions)
                .order_by(TaskExecution.create_time.asc(), TaskExecution.id)
                .limit(page_size)
                .offset((page - 1) * page_size)
            )
        ).all()
        counts = await self._child_artifact_counts(execution, [row.id for row in rows])
        return TaskChildPage(
            items=[
                TaskChildItem(
                    task_id=row.id,
                    item_key=row.item_key,
                    label=_child_label(row),
                    status=TaskStatus(row.status),
                    error_code=row.error_code,
                    error_message_summary=_error_summary(row),
                    artifact_count=counts.get(row.id, 0),
                    started_at=row.started_at,
                    finished_at=row.finished_at,
                )
                for row in rows
            ],
            page=page,
            page_size=page_size,
            total=total,
        )

    async def _child_artifact_counts(
        self, execution: TaskExecution, child_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, int]:
        if not child_ids:
            return {}
        rows = (
            await self._session.execute(
                sa.text(
                    "SELECT a.task_id, count(DISTINCT a.logical_file_id) AS n "
                    "FROM runtime.artifact a "
                    "JOIN task.task_execution e ON e.id = a.task_id "
                    "WHERE e.parent_id = :parent_id AND e.tenant_id = :tenant_id "
                    "AND e.is_deleted = false AND a.is_deleted = false "
                    "AND a.artifact_type = 'OUTPUT' "
                    "AND a.task_id IN :child_ids "
                    "GROUP BY a.task_id"
                ).bindparams(sa.bindparam("child_ids", expanding=True)),
                {
                    "parent_id": execution.id,
                    "tenant_id": execution.tenant_id,
                    "child_ids": tuple(child_ids),
                },
            )
        ).all()
        return {row.task_id: int(row.n) for row in rows}

    async def _artifact_deliveries(
        self, execution: TaskExecution, artifact_rows: Sequence[Any]
    ) -> dict[uuid.UUID, list[TaskArtifactDeliveryRef]]:
        """按产物聚合 push 状态：outbox（PENDING/FAILED）+ `message_delivery`（消息级权威）。

        `deliveries=[]` 只在没有 push 路由时出现；outbox 已请求但尚未受理时不能显示成
        “仅 Web”。`target_label` 只放安全通道标签，`target_key` 留给后端定位。
        """
        # 父任务可能没有自己的产物、只聚合子产物；outbox/message_delivery 仍记在父任务上。
        task_ids = {execution.id} | {
            row.task_id for row in artifact_rows if row.task_id is not None
        }
        facts: dict[tuple[uuid.UUID, str], TaskArtifactDeliveryRef] = {}
        outbox_rows = (
            await self._session.scalars(
                select(TaskMessageOutbox).where(
                    TaskMessageOutbox.tenant_id == execution.tenant_id,
                    TaskMessageOutbox.task_id.in_(task_ids),
                    TaskMessageOutbox.status.in_((OUTBOX_PENDING, OUTBOX_FAILED)),
                    TaskMessageOutbox.is_deleted.is_(False),
                )
            )
        ).all()
        for row in outbox_rows:
            outbox_status = "PENDING" if row.status == OUTBOX_PENDING else "FAILED"
            label = _target_label(row.route_json)
            for artifact_id in _payload_artifact_ids(row.payload_json):
                facts[(artifact_id, row.target_key)] = _delivery_ref(
                    target_key=row.target_key,
                    target_label=label,
                    status=outbox_status,
                    error_code=row.last_error_code,
                    retryable=row.status == OUTBOX_PENDING,
                )
        delivered_rows = (
            await self._session.execute(
                sa.text(
                    "SELECT target_key, route_json, payload_json, status, last_error_code, "
                    " fallback_url FROM runtime.message_delivery "
                    "WHERE tenant_id = :tenant_id AND owner_type = 'TASK' "
                    "AND owner_id IN :task_ids AND is_deleted = false"
                ).bindparams(sa.bindparam("task_ids", expanding=True)),
                {"tenant_id": execution.tenant_id, "task_ids": tuple(task_ids)},
            )
        ).all()
        for delivered in delivered_rows:
            status = DELIVERY_STATUS_MAP.get(str(delivered.status))
            if status is None:
                continue
            label = _target_label(delivered.route_json)
            for artifact_id in _payload_artifact_ids(delivered.payload_json):
                facts[(artifact_id, delivered.target_key)] = _delivery_ref(
                    target_key=delivered.target_key,
                    target_label=label,
                    status=status,
                    error_code=delivered.last_error_code,
                    retryable=status == "PENDING",
                    fallback_url=delivered.fallback_url,
                )
        grouped: dict[uuid.UUID, list[TaskArtifactDeliveryRef]] = {}
        for (artifact_id, _), ref in sorted(
            facts.items(), key=lambda item: (str(item[0][0]), item[0][1])
        ):
            grouped.setdefault(artifact_id, []).append(ref)
        return grouped

    async def _artifacts_with_children(
        self, execution: TaskExecution
    ) -> tuple[list[TaskArtifactRef], int]:
        rows = (
            await self._session.execute(
                sa.text(
                    "SELECT * FROM ("
                    "  SELECT DISTINCT ON (a.logical_file_id) "
                    "    a.id, a.media_type, a.size, a.checksum, a.metadata_json, a.task_id,"
                    "    a.logical_file_id, a.version, a.create_time "
                    "  FROM runtime.artifact a "
                    "  JOIN task.task_execution e ON e.id = a.task_id "
                    "  WHERE e.tenant_id = :tenant_id AND e.is_deleted = false "
                    "  AND (a.task_id = :execution_id OR e.parent_id = :execution_id) "
                    "  AND a.is_deleted = false AND a.artifact_type = 'OUTPUT' "
                    "  ORDER BY a.logical_file_id, a.version DESC"
                    ") latest ORDER BY latest.create_time DESC, latest.id"
                ),
                {"execution_id": execution.id, "tenant_id": execution.tenant_id},
            )
        ).all()
        deliveries = await self._artifact_deliveries(execution, rows)
        seen: set[uuid.UUID] = set()
        artifacts: list[TaskArtifactRef] = []
        for row in rows:
            if row.id in seen:
                continue
            seen.add(row.id)
            metadata = dict(row.metadata_json or {})
            artifacts.append(
                TaskArtifactRef(
                    artifact_id=row.id,
                    name=str(metadata.get("filename") or row.id),
                    media_type=row.media_type,
                    size=row.size,
                    checksum=row.checksum,
                    version=str(row.version),
                    deliveries=deliveries.get(row.id, []),
                )
            )
        # `artifact_total` is the number of logical files, not versions.
        total = int(
            await self._session.scalar(
                sa.text(
                    "SELECT count(DISTINCT a.logical_file_id) FROM runtime.artifact a "
                    "JOIN task.task_execution e ON e.id = a.task_id "
                    "WHERE e.tenant_id = :tenant_id AND e.is_deleted = false "
                    "AND (a.task_id = :execution_id OR e.parent_id = :execution_id) "
                    "AND a.is_deleted = false AND a.artifact_type = 'OUTPUT'"
                ),
                {"execution_id": execution.id, "tenant_id": execution.tenant_id},
            )
            or 0
        )
        return artifacts[:100], total

    async def pending_page(
        self,
        *,
        tenant_id: str,
        owner_user_id: uuid.UUID | None = None,
        agent_ids: Sequence[uuid.UUID] | None = None,
        kind: str | None = None,
        route: DeliveryRouteScope | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> PendingPage:
        """HUMAN 与 NEEDS_ATTENTION 混在同一次分页里，游标由 (kind 排序, id) 稳定给出。

        `route` 只收窄 HUMAN 条目到"推到这个会话"的那些（聊天入口回答用）；不传 = 全部。
        """
        humans = await self._pending_humans(
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            agent_ids=agent_ids,
            route=route,
        )
        attention = await self._pending_attention(
            tenant_id=tenant_id, owner_user_id=owner_user_id, agent_ids=agent_ids
        )
        items: list[PendingHumanItem | PendingAttentionItem] = []
        if kind in (None, "HUMAN"):
            items.extend(humans)
        if kind in (None, "NEEDS_ATTENTION"):
            items.extend(attention)
        items.sort(key=lambda item: (item.kind, str(item.schedule_id)))
        total = len(items)
        start = (page - 1) * page_size
        return PendingPage(
            items=items[start : start + page_size], page=page, page_size=page_size, total=total
        )

    async def _pending_humans(
        self,
        *,
        tenant_id: str,
        owner_user_id: uuid.UUID | None,
        agent_ids: Sequence[uuid.UUID] | None,
        route: DeliveryRouteScope | None = None,
    ) -> list[PendingHumanItem]:
        conditions: list[Any] = [
            TaskHumanInteraction.tenant_id == tenant_id,
            TaskHumanInteraction.status == "OPEN",
            TaskHumanInteraction.is_deleted.is_(False),
        ]
        if owner_user_id is not None:
            conditions.append(TaskHumanInteraction.owner_user_id == owner_user_id)
        if agent_ids is not None:
            if not agent_ids:
                return []
            conditions.append(TaskHumanInteraction.agent_id.in_(list(agent_ids)))
        if route is not None:
            conditions.append(
                TaskHumanInteraction.execution_id.in_(_executions_on_route(tenant_id, route))
            )
        rows = (
            await self._session.execute(
                select(TaskHumanInteraction, TaskExecution.schedule_id, TaskSchedule.name)
                .join(TaskExecution, TaskExecution.id == TaskHumanInteraction.execution_id)
                .join(TaskSchedule, TaskSchedule.id == TaskExecution.schedule_id)
                .where(*conditions)
                .order_by(TaskHumanInteraction.deadline_at)
            )
        ).all()
        return [
            PendingHumanItem(
                interaction_id=interaction.id,
                schedule_id=schedule_id,
                execution_id=interaction.execution_id,
                schedule_name=name,
                question=interaction.question,
                deadline_at=interaction.deadline_at,
                input_schema=dict(interaction.input_schema) if interaction.input_schema else None,
            )
            for interaction, schedule_id, name in rows
            if schedule_id is not None
        ]

    async def _pending_attention(
        self,
        *,
        tenant_id: str,
        owner_user_id: uuid.UUID | None,
        agent_ids: Sequence[uuid.UUID] | None,
    ) -> list[PendingAttentionItem]:
        conditions: list[Any] = [
            TaskSchedule.tenant_id == tenant_id,
            TaskSchedule.status == ScheduleStatus.NEEDS_ATTENTION.value,
            TaskSchedule.is_deleted.is_(False),
        ]
        if owner_user_id is not None:
            conditions.append(TaskSchedule.owner_user_id == owner_user_id)
        if agent_ids is not None:
            if not agent_ids:
                return []
            conditions.append(TaskSchedule.agent_id.in_(list(agent_ids)))
        rows = (
            await self._session.scalars(
                select(TaskSchedule).where(*conditions).order_by(TaskSchedule.attention_since)
            )
        ).all()
        return [
            PendingAttentionItem(
                schedule_id=schedule.id,
                schedule_name=schedule.name,
                reason=schedule.attention_reason or "ATTENTION_REQUIRED",
                action="修复依赖后恢复任务",
            )
            for schedule in rows
        ]

    async def badges(
        self,
        *,
        tenant_id: str,
        owner_user_id: uuid.UUID | None = None,
        agent_ids: Sequence[uuid.UUID] | None = None,
    ) -> TaskCenterBadges:
        humans = await self._pending_humans(
            tenant_id=tenant_id, owner_user_id=owner_user_id, agent_ids=agent_ids
        )
        attention = await self._pending_attention(
            tenant_id=tenant_id, owner_user_id=owner_user_id, agent_ids=agent_ids
        )
        return TaskCenterBadges(pending_human=len(humans), needs_attention=len(attention))

    async def timeout_warning(self, *, execution_id: uuid.UUID) -> datetime | None:
        """派发侧用的最小事实：该执行当前 OPEN 交互的截止时间（不存在即 None）。"""
        return cast(
            datetime | None,
            await self._session.scalar(
                select(TaskHumanInteraction.deadline_at).where(
                    TaskHumanInteraction.execution_id == execution_id,
                    TaskHumanInteraction.status == "OPEN",
                )
            ),
        )

    async def delivery_status(self, *, execution_id: uuid.UUID) -> str | None:
        return cast(
            str | None,
            await self._session.scalar(
                select(TaskDelivery.status)
                .where(
                    TaskDelivery.execution_id == execution_id,
                    TaskDelivery.is_deleted.is_(False),
                )
                .order_by(TaskDelivery.create_time.desc())
                .limit(1)
            ),
        )


def _executions_on_route(tenant_id: str, route: DeliveryRouteScope) -> Any:
    """执行所属 Revision 的投递目标 = 这条入站消息的来源（同租户、未删除）。"""
    conditions: list[Any] = [
        DeliveryRoute.tenant_id == tenant_id,
        DeliveryRoute.is_deleted.is_(False),
        DeliveryRoute.channel == route.channel,
        DeliveryRoute.bot_id == route.bot_id,
    ]
    if route.external_user_id is not None:
        conditions.append(DeliveryRoute.external_user_id == route.external_user_id)
    if route.external_conversation_id is None:
        conditions.append(DeliveryRoute.external_conversation_id.is_(None))
    else:
        conditions.append(
            DeliveryRoute.external_conversation_id == route.external_conversation_id
        )
    return (
        select(TaskExecution.id)
        .join(
            TaskScheduleRevision,
            TaskScheduleRevision.id == TaskExecution.schedule_revision_id,
        )
        .join(DeliveryRoute, DeliveryRoute.id == TaskScheduleRevision.delivery_route_id)
        .where(
            TaskExecution.tenant_id == tenant_id,
            TaskExecution.is_deleted.is_(False),
            *conditions,
        )
    )
