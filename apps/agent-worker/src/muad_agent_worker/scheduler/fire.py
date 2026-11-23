"""生成 Fire 并原子提交 Execution 与调度进度（design §3.2.9 / §3.5.2 / §3.5.3 / §3.5.11）。

调用方（`SchedulerLoop`）已经完成锁外的工作：短事务选出 due Schedule、读当前 Revision、
按 `(next_fire_at, tenant_id)` 计算 occurrence、在事务外做 Admission 与设置解析。本模块只在
同一个事务里写锁内事实：

0. **DST 不是一条独立分支**：`next_fire_at` 是 UTC 指针，经 `astimezone` 还原必然是存在的本地
   时刻，gap 只可能出现在「本地计划槽」的遍历里。`recurrence` 把 gap/second-fold 作为本次计划的
   `dst_events` 与 `slots_consumed` 一起给出，本模块在同一事务里写 SCHEDULE 事件并按本地槽计数
   —— 与设计 §3.5.2 的 gap 分支等价，且绝不伪造 Fire。
1. **occurrence 身份是 `(tenant_id, schedule_id, scheduled_at)`**；`revision` 是 Fire 的属性。
   `INSERT ... ON CONFLICT (uq_schedule_fire_occurrence) DO NOTHING`，冲突=已处理，绝不建第二个
   Execution；冲突时只把扫描指针前移（该 occurrence 的计数早就结过了）。
2. **Execution 幂等键**来自 `recurrence.execution_key`（固定 UTC、微秒、`Z` 结尾）；冲突=已存在，
   Fire 关联已有的 Execution，不重复执行。
3. 进度只写 `next_fire_at / occurrence_count / last_fire_at`；`config_version` 只随配置变更，
   Scheduler 的推进绝不碰它（否则待确认 Draft 会被正常触发打失效）。
4. 跳过与失败都留台账：Fire 行 + 实体事件，没有静默丢弃。
5. 同一事务保证不会出现「Fire 有 Execution 却无关联」或「Execution 无 Fire」：非
   SKIPPED/FAILED_ADMISSION 的 Fire 一定有 Execution。

锁序：Schedule（调用方已 `FOR UPDATE` 持有）→ Fire → root Execution → 事件。Fire 创建时执行树
还不存在，因此这里不取 operation/Parent/Child 锁；`task_events` 按 SCHEDULE→FIRE→EXECUTION
顺序取实体锁，与全局锁序一致。网络（Admission/设置）一律在锁外，锁内不做任何 IO。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from muad_contracts import (
    AgentTurnPayload,
    ConcurrencyPolicy,
    DstResolution,
    FireStatus,
    RecurrenceKind,
    ReminderPayload,
    ScheduleEndReason,
    SchedulePolicy,
    ScheduleStatus,
    SkillPlanPayload,
    SkipReason,
    TaskStatus,
    TaskType,
    TriggerType,
    derive_schedule_mode,
    parse_schedule_payload,
)
from muad_contracts.platform_settings import TaskSettings
from muad_logging import get_logger
from sqlalchemy import false, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..application.schedule_admission import (
    AdmissionOutcome,
    execution_snapshot_columns,
    record_admission_failure,
)
from ..application.schedule_quota import ScheduleQuota, admit_fire
from ..application.task_events import EntityType, TaskEventSeed, TaskEventType, append_events
from ..application.task_service import INITIAL_PRIORITY
from ..infrastructure.models.task import (
    ScheduleFire,
    TaskExecution,
    TaskSchedule,
    TaskScheduleRevision,
)
from ..metrics import SCHEDULE_FIRE_DUPLICATE_METRIC, increment
from .recurrence import OccurrencePlan, PlanAction, execution_key, to_utc
from .templates import render_argv, render_input_template

logger = get_logger(__name__)

#: 阻断集（`SKIP_IF_RUNNING` 的 Fire 创建）：**全部非终态根执行，含 QUEUED**。占槽集在 claim
#: 侧不含 QUEUED（避免候选自己挡自己），两套集合不同，不能合并成一个常量。
BLOCKING_ROOT_STATUSES: Final = (
    str(TaskStatus.QUEUED),
    str(TaskStatus.RUNNING),
    str(TaskStatus.WAITING_EXTERNAL),
    str(TaskStatus.WAITING_HUMAN),
)

#: 连续多少次临时准入失败才把 CRON 升级到 `NEEDS_ATTENTION`（design §3.5.5 的「连续 N 次」）。
#: 永久错误立即升级；这里防的是网络抖动把用户任务直接判死。
TRANSIENT_FAILURES_BEFORE_ATTENTION: Final = 5

@dataclass(frozen=True, slots=True)
class FireOutcome:
    """一次 occurrence 的处理结果；`status` 为空表示没有写 Fire（DST gap）。"""

    action: PlanAction
    status: FireStatus | None = None
    fire_id: uuid.UUID | None = None
    execution_id: uuid.UUID | None = None
    misfire: bool = False
    #: 跳过原因（封闭 `SkipReason`）：指标按它分档，原始指令/参数不进 label。
    skip_reason: SkipReason | None = None
    #: 准入失败的目录错误码（`config/api-messages.yaml` 的稳定码）：同样是有界 label。
    admission_error_code: str | None = None


async def write_fire(
    session: AsyncSession,
    *,
    schedule: TaskSchedule,
    revision: TaskScheduleRevision,
    policy: SchedulePolicy,
    plan: OccurrencePlan,
    now: datetime,
    admission: AdmissionOutcome | None = None,
    quota: ScheduleQuota,
    task_defaults: TaskSettings,
    deadline_at: datetime | None = None,
) -> FireOutcome:
    """在一个事务里写入本次 occurrence 的全部事实；调用方负责提交/回滚。

    `admission` 只在 `plan.action is FIRE` 时给出，且必须来自锁外的一次性解析。
    """
    await _append_dst_events(session, schedule, plan, now)
    if plan.action is PlanAction.MISFIRE:
        return await _write_misfire(
            session, schedule=schedule, revision=revision, plan=plan, now=now
        )
    if plan.action is not PlanAction.FIRE:
        raise ValueError(f"unsupported occurrence plan: {plan.action}")

    return await _write_due(
        session,
        schedule=schedule,
        revision=revision,
        policy=policy,
        plan=plan,
        now=now,
        admission=admission,
        quota=quota,
        task_defaults=task_defaults,
        deadline_at=deadline_at,
    )


async def _write_misfire(
    session: AsyncSession,
    *,
    schedule: TaskSchedule,
    revision: TaskScheduleRevision,
    plan: OccurrencePlan,
    now: datetime,
) -> FireOutcome:
    """错过区间聚合成一条 `SKIPPED(MISFIRE)`：`missed_count` 只记**额外**次数。"""
    fire = await _insert_fire(
        session,
        schedule=schedule,
        revision_id=revision.id,
        plan=plan,
        status=FireStatus.SKIPPED,
        now=now,
        skip_reason=SkipReason.MISFIRE,
        missed_count=plan.missed_count,
    )
    if fire is None:
        _advance_conflict(schedule, plan=plan, revision=revision, now=now)
        return FireOutcome(action=plan.action)
    await _append_fire_event(
        session,
        schedule=schedule,
        fire_id=fire.id,
        event_type=TaskEventType.SKIPPED,
        payload={
            "skip_reason": str(SkipReason.MISFIRE),
            "missed_count": plan.missed_count,
            "scheduled_at": _iso(plan.scheduled_at),
        },
        now=now,
    )
    _advance(
        schedule,
        plan=plan,
        now=now,
        last_fire_at=None,
        end_reason=_end_reason(plan, revision),
    )
    logger.warning(
        "schedule_fire_misfire",
        extra={
            "tenant_id": schedule.tenant_id,
            "schedule_id": str(schedule.id),
            "scheduled_at": _iso(plan.scheduled_at),
            "missed_count": plan.missed_count,
        },
    )
    return FireOutcome(
        action=plan.action,
        status=FireStatus.SKIPPED,
        fire_id=fire.id,
        misfire=True,
        skip_reason=SkipReason.MISFIRE,
    )


async def _write_due(
    session: AsyncSession,
    *,
    schedule: TaskSchedule,
    revision: TaskScheduleRevision,
    policy: SchedulePolicy,
    plan: OccurrencePlan,
    now: datetime,
    admission: AdmissionOutcome | None,
    quota: ScheduleQuota,
    task_defaults: TaskSettings,
    deadline_at: datetime | None,
) -> FireOutcome:
    """FIRE 计划：准入失败 → 配额 → 并发策略 → Execution，顺序固定（design §3.2.9）。"""
    if admission is None:
        raise ValueError("a FIRE plan requires an admission outcome")
    if not admission.accepted:
        return await _write_admission_failure(
            session, schedule=schedule, revision=revision, plan=plan, now=now, admission=admission
        )
    decision = await admit_fire(
        session,
        schedule=schedule,
        revision=revision,
        scheduled_at=to_utc(_scheduled_at(plan)),
        quota=quota,
    )
    if not decision.allowed:
        return await _write_skip(
            session,
            schedule=schedule,
            revision=revision,
            plan=plan,
            now=now,
            reason=SkipReason.QUOTA,
            payload={"denied_by": decision.denied_by, "used": decision.used, "limit": decision.limit},
        )
    if policy.concurrency_policy is ConcurrencyPolicy.SKIP_IF_RUNNING and await _has_blocking_root(
        session, schedule
    ):
        return await _write_skip(
            session,
            schedule=schedule,
            revision=revision,
            plan=plan,
            now=now,
            reason=SkipReason.OVERLAP,
            payload={"concurrency_policy": str(policy.concurrency_policy)},
        )
    return await _write_execution(
        session,
        schedule=schedule,
        revision=revision,
        plan=plan,
        now=now,
        admission=admission,
        task_defaults=task_defaults,
        deadline_at=deadline_at,
    )


async def _write_skip(
    session: AsyncSession,
    *,
    schedule: TaskSchedule,
    revision: TaskScheduleRevision,
    plan: OccurrencePlan,
    now: datetime,
    reason: SkipReason,
    payload: dict[str, Any],
) -> FireOutcome:
    """写一条 `SKIPPED(reason)` Fire 台账；不创建 Execution。"""
    fire = await _insert_fire(
        session,
        schedule=schedule,
        revision_id=revision.id,
        plan=plan,
        status=FireStatus.SKIPPED,
        now=now,
        skip_reason=reason,
    )
    if fire is None:
        _advance_conflict(schedule, plan=plan, revision=revision, now=now)
        return FireOutcome(action=plan.action)
    await _append_fire_event(
        session,
        schedule=schedule,
        fire_id=fire.id,
        event_type=TaskEventType.SKIPPED,
        payload={
            "skip_reason": str(reason),
            "scheduled_at": _iso(plan.scheduled_at),
            **{key: value for key, value in payload.items() if value is not None},
        },
        now=now,
    )
    _advance(schedule, plan=plan, now=now, last_fire_at=None, end_reason=_end_reason(plan, revision))
    logger.warning(
        "schedule_fire_skipped",
        extra={
            "tenant_id": schedule.tenant_id,
            "schedule_id": str(schedule.id),
            "scheduled_at": _iso(plan.scheduled_at),
            "skip_reason": str(reason),
        },
    )
    return FireOutcome(
        action=plan.action, status=FireStatus.SKIPPED, fire_id=fire.id, skip_reason=reason
    )


async def _write_execution(
    session: AsyncSession,
    *,
    schedule: TaskSchedule,
    revision: TaskScheduleRevision,
    plan: OccurrencePlan,
    now: datetime,
    admission: AdmissionOutcome,
    task_defaults: TaskSettings,
    deadline_at: datetime | None,
) -> FireOutcome:
    """Fire `EXECUTION_CREATED` + 冻结 Execution + 进度推进，同一事务。"""
    fire = await _insert_fire(
        session,
        schedule=schedule,
        revision_id=revision.id,
        plan=plan,
        status=FireStatus.CREATED,
        now=now,
        dst_resolution=plan.dst_resolution,
    )
    if fire is None:
        _advance_conflict(schedule, plan=plan, revision=revision, now=now)
        return FireOutcome(action=plan.action)
    execution_id = await _insert_execution(
        session,
        schedule=schedule,
        revision=revision,
        plan=plan,
        fire_id=fire.id,
        now=now,
        admission=admission,
        task_defaults=task_defaults,
        deadline_at=deadline_at,
    )
    # 循环 FK（fire.execution_id ↔ execution.schedule_fire_id）不是 DEFERRABLE：先落 Execution
    # 再回填 Fire，两步都在同一事务里。
    fire.execution_id = execution_id
    fire.status = str(FireStatus.EXECUTION_CREATED)
    fire.update_time = now
    await append_events(
        session,
        [
            TaskEventSeed(
                tenant_id=schedule.tenant_id,
                entity_type=EntityType.FIRE,
                entity_id=fire.id,
                event_type=TaskEventType.EXECUTION_CREATED,
                payload={
                    "execution_id": str(execution_id),
                    "scheduled_at": _iso(plan.scheduled_at),
                    "schedule_revision_id": str(revision.id),
                },
                occurred_at=now,
            ),
            TaskEventSeed(
                tenant_id=schedule.tenant_id,
                entity_type=EntityType.EXECUTION,
                entity_id=execution_id,
                event_type=TaskEventType.QUEUED,
                payload={"schedule_id": str(schedule.id), "scheduled_at": _iso(plan.scheduled_at)},
                occurred_at=now,
            ),
        ],
    )
    _advance(
        schedule,
        plan=plan,
        now=now,
        last_fire_at=to_utc(_scheduled_at(plan)),
        end_reason=_end_reason(plan, revision),
    )
    return FireOutcome(
        action=plan.action,
        status=FireStatus.EXECUTION_CREATED,
        fire_id=fire.id,
        execution_id=execution_id,
    )


async def _write_admission_failure(
    session: AsyncSession,
    *,
    schedule: TaskSchedule,
    revision: TaskScheduleRevision,
    plan: OccurrencePlan,
    now: datetime,
    admission: AdmissionOutcome,
) -> FireOutcome:
    """准入失败：Fire `FAILED_ADMISSION`，绝不建「假执行」的 Execution。

    - 永久错误：CRON `NEEDS_ATTENTION`，ONCE `ENDED(ADMISSION_FAILED)`；
    - 临时错误：CRON 继续下一次 occurrence，连续失败达阈值才 `NEEDS_ATTENTION`；
      ONCE 的有界重试只发生在锁外的那一次 Admission 内，重试仍失败即
      `ENDED(ADMISSION_FAILED)`——Fire 落库后不再有第二条 Fire 或补建 Execution。
    """
    error = admission.error
    if error is None:  # pragma: no cover - accepted outcome 不走这里
        raise ValueError("an admission failure requires an error")
    is_once = revision.recurrence_kind == RecurrenceKind.ONCE.value
    fire = await _insert_fire(
        session,
        schedule=schedule,
        revision_id=revision.id,
        plan=plan,
        status=FireStatus.FAILED_ADMISSION,
        now=now,
        admission_error_code=error.code,
    )
    if fire is None:
        _advance_conflict(schedule, plan=plan, revision=revision, now=now)
        return FireOutcome(action=plan.action)
    await record_admission_failure(
        session, fire=fire, schedule=schedule, error=error, is_once=is_once, moment=now
    )
    events = [
        TaskEventSeed(
            tenant_id=schedule.tenant_id,
            entity_type=EntityType.FIRE,
            entity_id=fire.id,
            event_type=TaskEventType.FAILED_ADMISSION,
            payload={
                "admission_error_code": error.code,
                "permanent": error.permanent,
                "repair": error.repair,
                "scheduled_at": _iso(plan.scheduled_at),
            },
            occurred_at=now,
        )
    ]
    if is_once:
        events.append(
            await _end_once_on_admission_failure(session, schedule=schedule, code=error.code, now=now)
        )
    elif error.permanent:
        schedule.next_fire_at = None
        events.append(
            _schedule_event(
                schedule,
                TaskEventType.NEEDS_ATTENTION,
                {"reason": error.code, "scheduled_at": _iso(plan.scheduled_at)},
                now,
            )
        )
    elif await _transient_streak(session, schedule) >= TRANSIENT_FAILURES_BEFORE_ATTENTION:
        schedule.status = str(ScheduleStatus.NEEDS_ATTENTION)
        schedule.attention_reason = error.code[:64]
        schedule.attention_since = now
        schedule.next_fire_at = None
        events.append(
            _schedule_event(
                schedule,
                TaskEventType.NEEDS_ATTENTION,
                {"reason": error.code, "consecutive_failures": TRANSIENT_FAILURES_BEFORE_ATTENTION},
                now,
            )
        )
    elif plan.end_reason is not None:
        # 本次是策略允许的最后一次 occurrence（until/max_occurrences/ONCE）：失败也要如实终止，
        # 不能留一条 ACTIVE 但没有 next_fire_at 的僵尸 Schedule。
        schedule.status = str(ScheduleStatus.ENDED)
        schedule.end_reason = str(plan.end_reason)
        schedule.ended_at = now
        schedule.next_fire_at = None
        events.append(
            _schedule_event(
                schedule, TaskEventType.ENDED, {"reason": str(plan.end_reason)}, now
            )
        )
    await append_events(session, events)
    schedule.occurrence_count += plan.slots_consumed
    schedule.update_time = now
    if schedule.status == str(ScheduleStatus.ACTIVE):
        schedule.next_fire_at = plan.next_fire_at
    logger.warning(
        "schedule_fire_admission_failed",
        extra={
            "tenant_id": schedule.tenant_id,
            "schedule_id": str(schedule.id),
            "scheduled_at": _iso(plan.scheduled_at),
            "admission_error_code": error.code,
            "permanent": error.permanent,
            "schedule_status": schedule.status,
        },
    )
    return FireOutcome(
        action=plan.action,
        status=FireStatus.FAILED_ADMISSION,
        fire_id=fire.id,
        admission_error_code=error.code,
    )


async def _end_once_on_admission_failure(
    session: AsyncSession, *, schedule: TaskSchedule, code: str, now: datetime
) -> TaskEventSeed:
    """ONCE 准入失败即终态：`ENDED(ADMISSION_FAILED)`，不留在 ACTIVE、不进 NEEDS_ATTENTION。"""
    schedule.status = str(ScheduleStatus.ENDED)
    schedule.end_reason = str(ScheduleEndReason.ADMISSION_FAILED)
    schedule.ended_at = now
    schedule.next_fire_at = None
    schedule.attention_reason = None
    schedule.attention_since = None
    schedule.update_time = now
    return _schedule_event(
        schedule,
        TaskEventType.ENDED,
        {"reason": str(ScheduleEndReason.ADMISSION_FAILED), "admission_error_code": code},
        now,
    )


async def _transient_streak(session: AsyncSession, schedule: TaskSchedule) -> int:
    """最近连续多少次 Fire 是 `FAILED_ADMISSION`（含本事务刚写入的那一条）。"""
    statuses = (
        await session.execute(
            select(ScheduleFire.status)
            .where(
                ScheduleFire.tenant_id == schedule.tenant_id,
                ScheduleFire.schedule_id == schedule.id,
                ScheduleFire.is_deleted.is_(False),
            )
            .order_by(ScheduleFire.scheduled_at.desc())
            .limit(TRANSIENT_FAILURES_BEFORE_ATTENTION)
        )
    ).scalars().all()
    streak = 0
    for status in statuses:
        if status != str(FireStatus.FAILED_ADMISSION):
            break
        streak += 1
    return streak


async def _insert_fire(
    session: AsyncSession,
    *,
    schedule: TaskSchedule,
    revision_id: uuid.UUID,
    plan: OccurrencePlan,
    status: FireStatus,
    now: datetime,
    skip_reason: SkipReason | None = None,
    missed_count: int = 0,
    dst_resolution: DstResolution | None = None,
    admission_error_code: str | None = None,
) -> ScheduleFire | None:
    """唯一 occurrence 台账：冲突=该 occurrence 已处理，返回 None（调用方只前移指针）。"""
    fire = (
        await session.execute(
            pg_insert(ScheduleFire)
            .values(
                tenant_id=schedule.tenant_id,
                schedule_id=schedule.id,
                schedule_revision_id=revision_id,
                scheduled_at=to_utc(_scheduled_at(plan)),
                status=str(status),
                skip_reason=None if skip_reason is None else str(skip_reason),
                missed_count=missed_count,
                dst_resolution=None if dst_resolution is None else str(dst_resolution),
                admission_error_code=admission_error_code,
            )
            .on_conflict_do_nothing(constraint="uq_schedule_fire_occurrence")
            .returning(ScheduleFire)
        )
    ).scalar_one_or_none()
    if fire is None:
        increment(SCHEDULE_FIRE_DUPLICATE_METRIC)
        logger.info(
            "schedule_fire_duplicate",
            extra={
                "tenant_id": schedule.tenant_id,
                "schedule_id": str(schedule.id),
                "scheduled_at": _iso(plan.scheduled_at),
            },
        )
    return fire


async def _insert_execution(
    session: AsyncSession,
    *,
    schedule: TaskSchedule,
    revision: TaskScheduleRevision,
    plan: OccurrencePlan,
    fire_id: uuid.UUID,
    now: datetime,
    admission: AdmissionOutcome,
    task_defaults: TaskSettings,
    deadline_at: datetime | None,
) -> uuid.UUID:
    """冻结 Execution；幂等键冲突=已存在，关联它（同一 occurrence 绝不执行两次）。"""
    values = _execution_values(
        schedule=schedule,
        revision=revision,
        plan=plan,
        fire_id=fire_id,
        now=now,
        admission=admission,
        task_defaults=task_defaults,
        deadline_at=deadline_at,
    )
    inserted = (
        await session.execute(
            pg_insert(TaskExecution)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=[TaskExecution.tenant_id, TaskExecution.idempotency_key],
                index_where=TaskExecution.is_deleted == false(),
            )
            .returning(TaskExecution.id)
        )
    ).scalar_one_or_none()
    if inserted is not None:
        return inserted
    existing = await session.scalar(
        select(TaskExecution.id).where(
            TaskExecution.tenant_id == schedule.tenant_id,
            TaskExecution.idempotency_key == values["idempotency_key"],
            TaskExecution.is_deleted.is_(False),
        )
    )
    if existing is None:  # pragma: no cover - 冲突只可能来自该幂等键
        raise RuntimeError("execution conflict without an existing row")
    logger.info(
        "schedule_execution_duplicate",
        extra={
            "tenant_id": schedule.tenant_id,
            "schedule_id": str(schedule.id),
            "execution_id": str(existing),
        },
    )
    return existing


def _execution_values(
    *,
    schedule: TaskSchedule,
    revision: TaskScheduleRevision,
    plan: OccurrencePlan,
    fire_id: uuid.UUID,
    now: datetime,
    admission: AdmissionOutcome,
    task_defaults: TaskSettings,
    deadline_at: datetime | None,
) -> dict[str, Any]:
    scheduled_at = to_utc(_scheduled_at(plan))
    snapshot = execution_snapshot_columns(admission)
    skill = _skill_columns(revision.mode, snapshot["execution_snapshot_json"])
    deadline = deadline_at or now
    return {
        "id": uuid.uuid4(),
        "tenant_id": schedule.tenant_id,
        "schedule_id": schedule.id,
        "schedule_fire_id": fire_id,
        "schedule_revision_id": revision.id,
        "scheduled_at": scheduled_at,
        "agent_id": schedule.agent_id,
        "actor_user_id": schedule.owner_user_id,
        "trigger_type": str(TriggerType.SCHEDULED),
        "task_type": str(TaskType(revision.mode)),
        "skill_id": skill["skill_id"],
        "skill_artifact_id": skill["artifact_id"],
        "skill_execution_mode": skill["execution_mode"],
        "status": str(TaskStatus.QUEUED),
        "input_json": _fire_input(revision, scheduled_at),
        **snapshot,
        "idempotency_key": execution_key(schedule.id, scheduled_at),
        "priority": INITIAL_PRIORITY,
        "attempt": 0,
        "max_attempts": task_defaults.max_attempts,
        "not_before": now,
        "deadline_at": deadline,
    }


def _fire_input(revision: TaskScheduleRevision, scheduled_at: datetime) -> dict[str, Any]:
    """本次执行的冻结输入：payload 的本次渲染 + 派生 mode + 上下文。

    配置本身（payload）在 Revision 里不可变，准入时已校验过模板语法；这里渲染的是**本次
    时刻**的动态变量（`{{fire_date}}` 等），不是配置来源。TASK-016/017 直接消费该形状。
    """
    payload = parse_schedule_payload(revision.payload_json)
    context = dict(revision.context_json or {})
    base: dict[str, Any] = {
        "kind": str(payload.kind),
        "mode": str(derive_schedule_mode(payload)),
        "session_target": revision.session_target,
        "context": context,
    }
    if isinstance(payload, ReminderPayload):
        return {**base, "text": payload.text}
    if isinstance(payload, SkillPlanPayload):
        return {
            **base,
            "skill_id": str(payload.skill_id),
            "input": render_input_template(payload.input, scheduled_at, revision.timezone),
            "argv": render_argv(payload.argv, scheduled_at, revision.timezone),
            "window": payload.window.model_dump(mode="json"),
        }
    assert isinstance(payload, AgentTurnPayload)
    return {
        **base,
        "prompt": payload.prompt,
        "model_ref": payload.model_ref,
        "tools_allow": list(payload.tools_allow),
        "budget_ref": payload.budget_ref,
        "output_contract": {},
    }


def _skill_columns(mode: str, snapshot: dict[str, Any]) -> dict[str, Any]:
    """SKILL 的 Execution 绑定本次冻结的 artifact；REMINDER/AGENTIC 不填 Skill 列。"""
    skills = snapshot.get("skills") or []
    if mode != str(TaskType.SKILL) or not skills:
        return {"skill_id": None, "artifact_id": None, "execution_mode": None}
    first = skills[0]
    return {
        "skill_id": first["skill_id"],
        "artifact_id": first["artifact_id"],
        "execution_mode": first.get("execution_mode"),
    }


def _advance(
    schedule: TaskSchedule,
    *,
    plan: OccurrencePlan,
    now: datetime,
    last_fire_at: datetime | None,
    end_reason: ScheduleEndReason | None,
    count_slots: bool = True,
) -> None:
    """推进扫描指针与本地计划槽计数；`config_version` 不参与。

    `count_slots=False` 只用于「Fire 冲突=该 occurrence 已处理」：指针必须前移（否则同一指针
    每轮都被选中形成热循环），但槽位与 `last_fire_at` 早已结过账，不能再记一次。
    """
    schedule.update_time = now
    if count_slots:
        schedule.occurrence_count += plan.slots_consumed
    if last_fire_at is not None:
        schedule.last_fire_at = last_fire_at
    if end_reason is not None:
        schedule.status = str(ScheduleStatus.ENDED)
        schedule.end_reason = str(end_reason)
        schedule.ended_at = now
        schedule.next_fire_at = None
        return
    if plan.next_fire_at is None:  # pragma: no cover - 引擎保证二者同现
        raise ValueError("an occurrence plan without a next pointer must carry an end reason")
    schedule.next_fire_at = plan.next_fire_at


def _advance_conflict(
    schedule: TaskSchedule,
    *,
    plan: OccurrencePlan,
    revision: TaskScheduleRevision,
    now: datetime,
) -> None:
    """Fire 冲突=该 occurrence 已处理：只前移指针，不重复计槽、不改 `last_fire_at`。"""
    _advance(
        schedule,
        plan=plan,
        now=now,
        last_fire_at=None,
        end_reason=_end_reason(plan, revision),
        count_slots=False,
    )


def _end_reason(
    plan: OccurrencePlan, revision: TaskScheduleRevision
) -> ScheduleEndReason | None:
    """ONCE 错过触发的终态是 `MISSED`（recurrence 的 misfire 分支把「成功即完成」留给调用方定性）。"""
    if plan.action is PlanAction.MISFIRE and revision.recurrence_kind == RecurrenceKind.ONCE.value:
        return ScheduleEndReason.MISSED
    return plan.end_reason


async def _has_blocking_root(session: AsyncSession, schedule: TaskSchedule) -> bool:
    """`SKIP_IF_RUNNING` 的阻断集：同 Schedule 的全部非终态**根**执行（含 QUEUED）。"""
    total = await session.scalar(
        select(func.count())
        .select_from(TaskExecution)
        .where(
            TaskExecution.tenant_id == schedule.tenant_id,
            TaskExecution.schedule_id == schedule.id,
            TaskExecution.parent_id.is_(None),
            TaskExecution.status.in_(BLOCKING_ROOT_STATUSES),
            TaskExecution.is_deleted.is_(False),
        )
    )
    return bool(total)


async def _append_dst_events(
    session: AsyncSession, schedule: TaskSchedule, plan: OccurrencePlan, now: datetime
) -> None:
    """DST gap / second-fold 只写 SCHEDULE 事件（Fire 只承载真实 UTC occurrence）。"""
    if not plan.dst_events:
        return
    await append_events(
        session,
        [
            _schedule_event(
                schedule,
                TaskEventType(event.type.value),
                {"local_date": event.local_date, "local_time": event.local_time},
                now,
            )
            for event in plan.dst_events
        ],
    )


async def _append_fire_event(
    session: AsyncSession,
    *,
    schedule: TaskSchedule,
    fire_id: uuid.UUID,
    event_type: TaskEventType,
    payload: dict[str, Any],
    now: datetime,
) -> None:
    await append_events(
        session,
        [
            TaskEventSeed(
                tenant_id=schedule.tenant_id,
                entity_type=EntityType.FIRE,
                entity_id=fire_id,
                event_type=event_type,
                payload=payload,
                occurred_at=now,
            )
        ],
    )


def _schedule_event(
    schedule: TaskSchedule, event_type: TaskEventType, payload: dict[str, Any], now: datetime
) -> TaskEventSeed:
    return TaskEventSeed(
        tenant_id=schedule.tenant_id,
        entity_type=EntityType.SCHEDULE,
        entity_id=schedule.id,
        event_type=event_type,
        payload=payload,
        occurred_at=now,
    )


def _scheduled_at(plan: OccurrencePlan) -> datetime:
    if plan.scheduled_at is None:  # pragma: no cover - 非 DST gap 计划必有 scheduled_at
        raise ValueError("a fire plan requires scheduled_at")
    return plan.scheduled_at


def _iso(moment: datetime | None) -> str | None:
    return None if moment is None else to_utc(moment).isoformat()
