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
from typing import Any

import sqlalchemy as sa
from muad_contracts import (
    AgenticBudget,
    DeliveryIntent,
    DraftContext,
    DstEvent,
    ExecutionDetail,
    FireHistoryPage,
    FireItem,
    HistoryRetention,
    LastFireSummary,
    PendingAttentionItem,
    PendingHumanItem,
    PendingPage,
    ScheduleDetail,
    ScheduleListItem,
    ScheduleMode,
    SchedulePolicy,
    TaskArtifactRef,
    TaskCenterBadges,
    TaskStatus,
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
    TaskSchedule,
    TaskScheduleRevision,
)

DST_EVENT_TYPES = ("DST_GAP_SKIPPED", "DST_FOLD_SECOND_SKIPPED")


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
            end_reason=schedule.end_reason,
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
                    event_type=row.event_type,
                    local_date=str(payload.get("local_date") or ""),
                    local_time=str(payload.get("local_time") or ""),
                    timezone=timezone,
                    occurred_at=row.occurred_at,
                )
            )
        return events

    async def execution_detail(self, *, execution: TaskExecution) -> ExecutionDetail:
        """Execution 详情：等待载荷只在 `WAITING_EXTERNAL` 时可见（其余状态是内部事实）。"""
        waiting = execution.status == TaskStatus.WAITING_EXTERNAL.value
        return ExecutionDetail(
            execution_id=execution.id,
            status=TaskStatus(execution.status),
            wait_kind=WaitKind(execution.wait_kind) if waiting and execution.wait_kind else None,
            wait_payload=dict(execution.wait_payload_json or {}) if waiting else None,
            result=dict(execution.result_json) if execution.result_json is not None else None,
            result_artifacts=await self._artifacts(execution.id),
            error_code=execution.error_code,
            error_message=execution.error_message,
            failure_class=execution.failure_class,
            started_at=execution.started_at,
            finished_at=execution.finished_at,
            snapshot_schema_version=execution.execution_snapshot_schema_version,
            snapshot_bytes=execution.snapshot_bytes,
        )

    async def _artifacts(self, execution_id: uuid.UUID) -> list[TaskArtifactRef]:
        rows = (
            await self._session.execute(
                sa.text(
                    "SELECT id, media_type, size, checksum, metadata_json "
                    "FROM runtime.artifact "
                    "WHERE task_id = :execution_id AND is_deleted = false "
                    "AND artifact_type = 'TASK_OUTPUT' "
                    "ORDER BY create_time DESC, id"
                ),
                {"execution_id": execution_id},
            )
        ).all()
        artifacts: list[TaskArtifactRef] = []
        for row in rows:
            metadata = dict(row.metadata_json or {})
            artifacts.append(
                TaskArtifactRef(
                    artifact_id=row.id,
                    name=str(metadata.get("filename") or row.id),
                    media_type=row.media_type,
                    size=row.size,
                    checksum=row.checksum,
                    version=str(metadata.get("version") or "1"),
                )
            )
        return artifacts

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
        return await self._session.scalar(
            select(TaskHumanInteraction.deadline_at).where(
                TaskHumanInteraction.execution_id == execution_id,
                TaskHumanInteraction.status == "OPEN",
            )
        )

    async def delivery_status(self, *, execution_id: uuid.UUID) -> str | None:
        return await self._session.scalar(
            select(TaskDelivery.status)
            .where(TaskDelivery.execution_id == execution_id, TaskDelivery.is_deleted.is_(False))
            .order_by(TaskDelivery.create_time.desc())
            .limit(1)
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
