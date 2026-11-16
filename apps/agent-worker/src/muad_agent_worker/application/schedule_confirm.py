"""API-27：用户确认 Draft 并原子创建/更新 Schedule（design §3.2.8 / §3.4.4 / §3.5.1 / §3.5.4）。

三条硬约束：

1. **唯一生效入口**：确认请求本身就是创建/更新（API-02/03 不存在）。模型的
   `draft→create` 工具上下文没有用户动作来源（surface + ref），任何这类调用都在落任何
   业务行之前以 `SCHEDULE_CONFIRMATION_REQUIRED` 拒绝。
2. **一个事务**：`FOR UPDATE` 锁 Draft → owner/hash/PENDING/时效/确认引用复核 → 确认事实 →
   Schedule + 不可变 Revision + next_fire_at → 首次幂等响应快照。任一步失败整体回滚：
   Draft 保持 PENDING（确认未被消耗），不留下半个 Schedule/Revision/事件。
3. **重放优先**：调用方先经 `task_submission` 的 advisory 锁与指纹重放首次结果，再进入本
   服务；因此「Draft 已 CONFIRMED」只在**不同**幂等键时才意味着「一次确认只生效一次」。

UPDATE 的 CAS 基线只取 Draft 的 `base_config_version`，绝不接受请求体覆盖；Scheduler 只推进
`next_fire_at/occurrence_count`，不改 `config_version`，所以正常调度进度不会让待确认 Draft 失效。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import (
    ConfirmationSurface,
    DeliveryIntent,
    DraftKind,
    DraftStatus,
    RecurrenceKind,
    ScheduleDraftConfirmRequest,
    ScheduleDraftRequest,
    SchedulePolicy,
    ScheduleStatus,
)
from muad_contracts.enums import OUTBOUND_CHANNELS, FireStatus, SkipReason, TaskStatus
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.task import (
    ScheduleFire,
    TaskExecution,
    TaskSchedule,
    TaskScheduleDraft,
    TaskScheduleRevision,
)
from ..scheduler.recurrence import Advance, RecurrenceRule, build_rule, initial_fire_at
from .delivery_routes import upsert_delivery_route
from .schedule_drafts import draft_what, update_diff
from .submissions import SubmissionEndpoint, TaskSubmissionService
from .task_events import EntityType, TaskEventSeed, TaskEventType, append_events

DRAFT_STATUS_PENDING = str(DraftStatus.PENDING)
DRAFT_STATUS_CONFIRMED = str(DraftStatus.CONFIRMED)
SCHEDULE_STATUS_ACTIVE = str(ScheduleStatus.ACTIVE)
#: 终态 Schedule 不可再修改（ONCE 准入失败/已完成只能新建）。
TERMINAL_SCHEDULE_STATUSES = (str(ScheduleStatus.ENDED), str(ScheduleStatus.CANCELLED))


@dataclass(frozen=True, slots=True)
class ConfirmIdentity:
    """确认动作的可信来源：actor 来自 `X-Actor-User-Id`，surface/ref 只在用户真实动作时存在。"""

    tenant_id: str
    actor_user_id: uuid.UUID
    surface: ConfirmationSurface | None = None
    ref: str | None = None


def payload_request(draft: TaskScheduleDraft) -> ScheduleDraftRequest:
    """Draft 载荷 → 强类型请求；损坏的载荷显式失败，不猜。"""
    try:
        return ScheduleDraftRequest.model_validate(dict(draft.payload_json))
    except ValueError as exc:
        raise AppError(
            ErrorCode.SCHEDULE_DRAFT_INVALID, data={"reason": "DRAFT_PAYLOAD_INVALID"}
        ) from exc


def recurrence_rule(body: ScheduleDraftRequest) -> RecurrenceRule:
    recurrence = body.recurrence
    try:
        return build_rule(
            kind=str(recurrence.kind),
            timezone=recurrence.timezone,
            cron=recurrence.cron,
            run_at=recurrence.run_at,
        )
    except ValueError as exc:
        raise AppError(
            ErrorCode.SCHEDULE_DRAFT_INVALID, data={"reason": "RECURRENCE_INVALID"}
        ) from exc


def future_fire_at(rule: RecurrenceRule, now: datetime) -> Advance:
    """首个可触发指针**及其途中被跳过的 gap 槽**（绝不建永不触发的 Schedule）。

    指针本身不是全部事实：从 now 到首个可触发槽之间若跨过 DST gap，那些槽同样消耗
    计划槽并要留 SCHEDULE 事件（§3.5.11），所以这里返回整份 Advance，而不是只给时间。
    """
    advance = initial_fire_at(rule, now)
    if advance.next_fire_at is None:
        raise AppError(
            ErrorCode.SCHEDULE_DRAFT_INVALID, data={"reason": "NO_FUTURE_OCCURRENCE"}
        )
    return advance


def ensure_delivery_supported(body: ScheduleDraftRequest) -> None:
    """PUSH 必须带着创建时冻结的目标；没有目标的投递承诺绝不落成 Revision。"""
    if body.delivery_intent is not DeliveryIntent.PUSH:
        return
    route = body.delivery_route
    if route is None:
        raise AppError(
            ErrorCode.SCHEDULE_DELIVERY_UNSUPPORTED,
            data={"reason": "DELIVERY_ROUTE_UNAVAILABLE"},
        )
    if route.channel not in OUTBOUND_CHANNELS:
        raise AppError(
            ErrorCode.SCHEDULE_DELIVERY_UNSUPPORTED,
            data={"reason": "ENTRY_CHANNEL_UNSUPPORTED"},
        )


def execution_affecting(
    draft: TaskScheduleDraft,
    *,
    schedule: TaskSchedule,
    revision: TaskScheduleRevision | None,
) -> bool:
    """关键字段变化 = 除展示名之外的配置差异（模式/规则/参数/投递意图）。

    只改 name（展示字段）时保留已有 Fire/Execution，未来 Fire 用新 Revision。
    """
    if revision is None:
        return True
    diff = update_diff(dict(draft.payload_json), schedule=schedule, revision=revision)
    return any(item["field"] != "name" for item in diff)


def revision_values(
    draft: TaskScheduleDraft,
    body: ScheduleDraftRequest,
    *,
    schedule_id: uuid.UUID,
    revision_no: int,
    created_by: uuid.UUID,
    delivery_route_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """不可变 Revision 的字段：配置全部来自 Draft 载荷，不复制到 Schedule。"""
    recurrence = body.recurrence
    once = recurrence.kind is RecurrenceKind.ONCE
    return {
        "tenant_id": draft.tenant_id,
        "schedule_id": schedule_id,
        "revision_no": revision_no,
        "mode": str(body.mode),
        "instruction_raw": body.instruction,
        "normalized_goal": draft_what(body),
        "recurrence_kind": str(recurrence.kind),
        "cron_expr": None if once else recurrence.cron,
        "run_at": recurrence.run_at.astimezone(UTC) if once and recurrence.run_at else None,
        "timezone": recurrence.timezone,
        "schedule_policy": SchedulePolicy().model_dump(mode="json"),
        "context_json": body.context.model_dump(mode="json"),
        "execution_spec": body.execution_spec.model_dump(mode="json"),
        "delivery_intent": str(body.delivery_intent),
        "delivery_route_id": delivery_route_id,
        "preflight_json": dict(draft.preview_json),
        "created_by": created_by,
    }


def create_name(
    draft: TaskScheduleDraft,
    request: ScheduleDraftConfirmRequest,
    body: ScheduleDraftRequest,
) -> str:
    """CREATE 的展示名：确认时给的名 > Draft 载荷里的名 > instruction 派生。"""
    candidate = request.name or draft.payload_json.get("name")
    if isinstance(candidate, str) and candidate.strip():
        return candidate
    return (body.instruction.strip() or body.instruction)[:160]


def update_name(draft: TaskScheduleDraft, request: ScheduleDraftConfirmRequest) -> str | None:
    """UPDATE 的展示名；缺省沿用原名（Draft 创建时已把原名写进载荷）。"""
    candidate = request.name or draft.payload_json.get("name")
    return candidate if isinstance(candidate, str) and candidate.strip() else None


def confirm_response(schedule: TaskSchedule, revision: TaskScheduleRevision) -> dict[str, Any]:
    """API-27 响应体（`ScheduleDraftConfirmResponse` 同形）。"""
    return {
        "schedule_id": str(schedule.id),
        "status": schedule.status,
        "revision_no": revision.revision_no,
        "config_version": schedule.config_version,
        "next_fire_at": schedule.next_fire_at.isoformat() if schedule.next_fire_at else None,
    }


class ScheduleConfirmService:
    """确认事务的应用服务：调用方负责提交或回滚，本服务不 commit。"""

    def __init__(
        self,
        session: AsyncSession,
        *,
        submissions: TaskSubmissionService,
        now: datetime,
    ) -> None:
        self._session = session
        self._submissions = submissions
        self._now = now

    async def confirm(
        self,
        draft_id: uuid.UUID,
        *,
        request: ScheduleDraftConfirmRequest,
        identity: ConfirmIdentity,
        idempotency_key: str,
        fingerprint: str,
    ) -> dict[str, Any]:
        surface, ref = self._confirmation_scope(identity)
        draft = await self._locked_draft(draft_id, identity)
        self._check_hash(draft, request)
        self._check_state(draft)
        await self._check_reference(draft, identity.tenant_id, surface, ref)
        body = payload_request(draft)
        ensure_delivery_supported(body)
        delivery_route_id = await self._delivery_route_id(draft, body)
        await self._mark_confirmed(draft, identity.actor_user_id, surface, ref)
        if draft.kind == str(DraftKind.CREATE):
            schedule, revision = await self._create(
                draft, body, request, identity, delivery_route_id=delivery_route_id
            )
        else:
            schedule, revision = await self._update(
                draft, body, request, identity, delivery_route_id=delivery_route_id
            )
        draft.schedule_id = schedule.id
        response = confirm_response(schedule, revision)
        await self._record(draft, schedule, response, identity, idempotency_key, fingerprint)
        return response

    async def _delivery_route_id(
        self, draft: TaskScheduleDraft, body: ScheduleDraftRequest
    ) -> uuid.UUID | None:
        """冻结创建时的投递目标（owner 就是 Draft owner）：同一 route 复用同一行。"""
        route = body.delivery_route
        if route is None:
            return None
        return await upsert_delivery_route(
            self._session,
            tenant_id=draft.tenant_id,
            platform_user_id=draft.owner_user_id,
            route=route,
        )

    async def _locked_draft(
        self, draft_id: uuid.UUID, identity: ConfirmIdentity
    ) -> TaskScheduleDraft:
        """Draft 行锁 + owner 复核：跨用户/跨租户一律 404（不暴露存在性）。"""
        draft = await self._session.scalar(
            select(TaskScheduleDraft)
            .where(
                TaskScheduleDraft.id == draft_id,
                TaskScheduleDraft.tenant_id == identity.tenant_id,
                TaskScheduleDraft.is_deleted.is_(False),
            )
            .with_for_update()
        )
        if draft is None or draft.owner_user_id != identity.actor_user_id:
            raise AppError(ErrorCode.SCHEDULE_NOT_FOUND)
        return draft

    def _confirmation_scope(self, identity: ConfirmIdentity) -> tuple[ConfirmationSurface, str]:
        """没有 surface/ref 就没有用户动作（模型同轮自确认走不到这里）。"""
        if identity.surface is None or not identity.ref:
            raise AppError(
                ErrorCode.SCHEDULE_CONFIRMATION_REQUIRED, data={"reason": "NO_USER_ACTION"}
            )
        return identity.surface, identity.ref

    def _check_hash(
        self, draft: TaskScheduleDraft, request: ScheduleDraftConfirmRequest
    ) -> None:
        if draft.draft_hash != request.draft_hash:
            raise AppError(
                ErrorCode.SCHEDULE_CONFIRMATION_REQUIRED,
                data={"reason": "DRAFT_HASH_MISMATCH"},
            )

    def _check_state(self, draft: TaskScheduleDraft) -> None:
        """重放已在入口按幂等键处理；到这里仍非 PENDING/已过期就是「一次确认只生效一次」。"""
        if draft.status != DRAFT_STATUS_PENDING:
            raise AppError(
                ErrorCode.SCHEDULE_DRAFT_EXPIRED, data={"reason": "NOT_PENDING"}
            )
        if draft.expires_at <= self._now:
            raise AppError(
                ErrorCode.SCHEDULE_DRAFT_EXPIRED, data={"reason": "EXPIRED"}
            )

    async def _check_reference(
        self,
        draft: TaskScheduleDraft,
        tenant_id: str,
        surface: ConfirmationSurface,
        ref: str,
    ) -> None:
        """一次用户动作只能确认一个 Draft（partial unique 的显式前置检查）。"""
        other = await self._session.scalar(
            select(TaskScheduleDraft.id).where(
                TaskScheduleDraft.tenant_id == tenant_id,
                TaskScheduleDraft.confirmation_surface == str(surface),
                TaskScheduleDraft.confirmation_ref == ref,
                TaskScheduleDraft.status == DRAFT_STATUS_CONFIRMED,
                TaskScheduleDraft.is_deleted.is_(False),
                TaskScheduleDraft.id != draft.id,
            )
        )
        if other is not None:
            raise AppError(
                ErrorCode.SCHEDULE_CONFIRMATION_REPLAY,
                data={"confirmation_surface": str(surface)},
            )

    async def _mark_confirmed(
        self,
        draft: TaskScheduleDraft,
        actor_user_id: uuid.UUID,
        surface: ConfirmationSurface,
        ref: str,
    ) -> None:
        """确认事实与业务行同事务；并发抢同一确认引用由 partial unique 兜底。"""
        try:
            async with self._session.begin_nested():
                draft.status = DRAFT_STATUS_CONFIRMED
                draft.confirmation_surface = str(surface)
                draft.confirmation_ref = ref
                draft.confirmed_by_user_id = actor_user_id
                draft.confirmed_at = self._now
                await self._session.flush()
        except IntegrityError as exc:
            raise AppError(
                ErrorCode.SCHEDULE_CONFIRMATION_REPLAY,
                data={"confirmation_surface": str(surface)},
            ) from exc

    async def _create(
        self,
        draft: TaskScheduleDraft,
        body: ScheduleDraftRequest,
        request: ScheduleDraftConfirmRequest,
        identity: ConfirmIdentity,
        *,
        delivery_route_id: uuid.UUID | None,
    ) -> tuple[TaskSchedule, TaskScheduleRevision]:
        """CREATE：Schedule + Revision 1 + 首个 next_fire_at，全部在本事务内。"""
        advance = future_fire_at(recurrence_rule(body), self._now)
        schedule = TaskSchedule(
            id=uuid.uuid4(),
            tenant_id=draft.tenant_id,
            owner_user_id=draft.owner_user_id,
            agent_id=draft.agent_id,
            name=create_name(draft, request, body),
            status=SCHEDULE_STATUS_ACTIVE,
            next_fire_at=advance.next_fire_at,
            # gap 槽也是计划 occurrence：建成时就按本地槽计数，不留“凭空少一次”的账。
            occurrence_count=advance.slots_consumed,
            config_version=1,
            created_by=identity.actor_user_id,
        )
        self._session.add(schedule)
        await self._session.flush()
        revision = TaskScheduleRevision(
            id=uuid.uuid4(),
            **revision_values(
                draft,
                body,
                schedule_id=schedule.id,
                revision_no=1,
                created_by=identity.actor_user_id,
                delivery_route_id=delivery_route_id,
            ),
        )
        self._session.add(revision)
        await self._session.flush()
        schedule.current_revision_id = revision.id
        await self._append_schedule_event(
            schedule,
            TaskEventType.CREATED,
            {"revision_no": 1, "config_version": 1},
        )
        await self._append_skipped_gaps(schedule, advance)
        return schedule, revision

    async def _update(
        self,
        draft: TaskScheduleDraft,
        body: ScheduleDraftRequest,
        request: ScheduleDraftConfirmRequest,
        identity: ConfirmIdentity,
        *,
        delivery_route_id: uuid.UUID | None,
    ) -> tuple[TaskSchedule, TaskScheduleRevision]:
        """UPDATE：只认 Draft 冻结的 base_config_version 做 CAS，冲突整体回滚。"""
        schedule = await self._locked_schedule(draft)
        self._check_cas(schedule, draft)
        previous = await self._current_revision(schedule)
        affecting = execution_affecting(draft, schedule=schedule, revision=previous)
        cancelled = (
            await self._cancel_old_revision_work(schedule, previous)
            if affecting
            else ([], [])
        )
        revision = TaskScheduleRevision(
            id=uuid.uuid4(),
            **revision_values(
                draft,
                body,
                schedule_id=schedule.id,
                revision_no=await self._next_revision_no(schedule),
                created_by=identity.actor_user_id,
                delivery_route_id=delivery_route_id,
            ),
        )
        self._session.add(revision)
        await self._session.flush()
        schedule.current_revision_id = revision.id
        schedule.config_version += 1
        name = update_name(draft, request)
        if name is not None:
            schedule.name = name
        if affecting and schedule.status == SCHEDULE_STATUS_ACTIVE:
            advance = future_fire_at(recurrence_rule(body), self._now)
            schedule.next_fire_at = advance.next_fire_at
            schedule.occurrence_count += advance.slots_consumed
            await self._append_skipped_gaps(schedule, advance)
        schedule.update_time = self._now
        await self._append_update_events(schedule, revision, *cancelled)
        return schedule, revision

    async def _locked_schedule(self, draft: TaskScheduleDraft) -> TaskSchedule:
        schedule = await self._session.scalar(
            select(TaskSchedule)
            .where(
                TaskSchedule.id == draft.base_schedule_id,
                TaskSchedule.tenant_id == draft.tenant_id,
                TaskSchedule.is_deleted.is_(False),
            )
            .with_for_update()
        )
        if schedule is None or schedule.owner_user_id != draft.owner_user_id:
            raise AppError(ErrorCode.SCHEDULE_NOT_FOUND)
        if schedule.status in TERMINAL_SCHEDULE_STATUSES:
            raise AppError(
                ErrorCode.SCHEDULE_STATE_INVALID, data={"status": schedule.status}
            )
        return schedule

    def _check_cas(self, schedule: TaskSchedule, draft: TaskScheduleDraft) -> None:
        if (
            draft.base_config_version is None
            or schedule.config_version != draft.base_config_version
        ):
            raise AppError(
                ErrorCode.SCHEDULE_VERSION_CONFLICT,
                data={
                    "expected": draft.base_config_version,
                    "actual": schedule.config_version,
                },
            )

    async def _current_revision(self, schedule: TaskSchedule) -> TaskScheduleRevision | None:
        if schedule.current_revision_id is None:
            return None
        return await self._session.get(TaskScheduleRevision, schedule.current_revision_id)

    async def _next_revision_no(self, schedule: TaskSchedule) -> int:
        current = await self._session.scalar(
            select(func.max(TaskScheduleRevision.revision_no)).where(
                TaskScheduleRevision.tenant_id == schedule.tenant_id,
                TaskScheduleRevision.schedule_id == schedule.id,
            )
        )
        return int(current or 0) + 1

    async def _cancel_old_revision_work(
        self, schedule: TaskSchedule, previous: TaskScheduleRevision | None
    ) -> tuple[list[uuid.UUID], list[uuid.UUID]]:
        """取消旧 Revision 未开始的 Execution 及其 Fire。

        `started_at IS NULL` 是「从未开始」的唯一判据：reclaim 回 QUEUED 的执行保留
        `started_at`，继续用旧 Snapshot 跑完；尚未生成 Fire 的 occurrence 只重算指针，
        不伪造一条 Fire。
        """
        conditions = [
            TaskExecution.tenant_id == schedule.tenant_id,
            TaskExecution.schedule_id == schedule.id,
            TaskExecution.started_at.is_(None),
            TaskExecution.status == str(TaskStatus.QUEUED),
            TaskExecution.is_deleted.is_(False),
        ]
        if previous is not None:
            conditions.append(TaskExecution.schedule_revision_id == previous.id)
        rows = await self._session.execute(
            update(TaskExecution)
            .where(*conditions)
            .values(
                status=str(TaskStatus.CANCELLED),
                cancel_requested=True,
                finished_at=self._now,
                lease_owner=None,
                lease_until=None,
                update_time=self._now,
            )
            .returning(TaskExecution.id)
            .execution_options(synchronize_session=False)
        )
        execution_ids = [row[0] for row in rows.all()]
        if not execution_ids:
            return [], []
        fire_rows = await self._session.execute(
            update(ScheduleFire)
            .where(
                ScheduleFire.tenant_id == schedule.tenant_id,
                ScheduleFire.schedule_id == schedule.id,
                ScheduleFire.execution_id.in_(execution_ids),
            )
            .values(
                status=str(FireStatus.CANCELLED),
                skip_reason=str(SkipReason.PAUSED_BY_UPDATE),
                update_time=self._now,
            )
            .returning(ScheduleFire.id)
            .execution_options(synchronize_session=False)
        )
        return execution_ids, [row[0] for row in fire_rows.all()]

    async def _append_skipped_gaps(self, schedule: TaskSchedule, advance: Advance) -> None:
        """把推进途中被跳过的 DST 槽写成 SCHEDULE 事件（与 Scheduler 同一口径）。"""
        if not advance.gap_events:
            return
        await append_events(
            self._session,
            [
                TaskEventSeed(
                    tenant_id=schedule.tenant_id,
                    entity_type=EntityType.SCHEDULE,
                    entity_id=schedule.id,
                    event_type=TaskEventType(event.type.value),
                    payload={"local_date": event.local_date, "local_time": event.local_time},
                    occurred_at=self._now,
                )
                for event in advance.gap_events
            ],
        )

    async def _append_schedule_event(
        self, schedule: TaskSchedule, event_type: TaskEventType, payload: dict[str, Any]
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

    async def _append_update_events(
        self,
        schedule: TaskSchedule,
        revision: TaskScheduleRevision,
        execution_ids: list[uuid.UUID],
        fire_ids: list[uuid.UUID],
    ) -> None:
        """Schedule UPDATED + 被更新取消的 Execution/Fire 各自的 CANCELLED（同一事务）。"""
        seeds = [
            TaskEventSeed(
                tenant_id=schedule.tenant_id,
                entity_type=EntityType.SCHEDULE,
                entity_id=schedule.id,
                event_type=TaskEventType.UPDATED,
                payload={
                    "revision_no": revision.revision_no,
                    "config_version": schedule.config_version,
                    "cancelled_executions": len(execution_ids),
                },
                occurred_at=self._now,
            )
        ]
        seeds.extend(
            TaskEventSeed(
                tenant_id=schedule.tenant_id,
                entity_type=EntityType.EXECUTION,
                entity_id=execution_id,
                event_type=TaskEventType.CANCELLED,
                payload={"reason": "SCHEDULE_UPDATED"},
                occurred_at=self._now,
            )
            for execution_id in execution_ids
        )
        seeds.extend(
            TaskEventSeed(
                tenant_id=schedule.tenant_id,
                entity_type=EntityType.FIRE,
                entity_id=fire_id,
                event_type=TaskEventType.CANCELLED,
                payload={"reason": str(SkipReason.PAUSED_BY_UPDATE)},
                occurred_at=self._now,
            )
            for fire_id in fire_ids
        )
        await append_events(self._session, seeds)

    async def _record(
        self,
        draft: TaskScheduleDraft,
        schedule: TaskSchedule,
        response: dict[str, Any],
        identity: ConfirmIdentity,
        idempotency_key: str,
        fingerprint: str,
    ) -> None:
        """首次响应快照与业务行同事务：同键重放拿到的是这次提交的事实。"""
        await self._submissions.record_in(
            tenant_id=identity.tenant_id,
            idempotency_key=idempotency_key,
            endpoint=SubmissionEndpoint.SCHEDULE_CONFIRM,
            actor_user_id=identity.actor_user_id,
            request_fingerprint=fingerprint,
            response=response,
            schedule_id=schedule.id,
            schedule_draft_id=draft.id,
        )


__all__ = [
    "ConfirmIdentity",
    "ScheduleConfirmService",
    "confirm_response",
    "create_name",
    "ensure_delivery_supported",
    "execution_affecting",
    "future_fire_at",
    "payload_request",
    "recurrence_rule",
    "revision_values",
    "update_name",
]
