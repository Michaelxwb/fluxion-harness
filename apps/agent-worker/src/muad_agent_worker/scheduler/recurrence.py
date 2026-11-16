"""纯计算的 recurrence：本地计划槽、DST 决策与错过区间聚合（设计 §2.3.7 / §3.5.2 / §3.5.11）。

本模块不写库、不联网、不读系统时钟：调用方把 `now`、`next_fire_at` 与 `occurrence_count`
交给它，拿回“这一次该做什么”。这样 DST、misfire 与计数口径可以被可控时钟逐个钉住。

三条不变量：

- **按本地壁钟推进**：规则在 Revision 的 IANA 时区上解释，绝不用服务器本地时区兜底；
- **`next_fire_at` 只是扫描指针**：只有 FIRE 计划产生 Fire；gap/second-fold 只写事件；
- **`occurrence_count` 计本地计划槽**：正常 Fire 与 gap 各 +1，错过区间 +1+missed_count，
  fold 的第二次不计（同一个槽）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from croniter import CroniterBadCronError, croniter
from muad_contracts import (
    DstEventType,
    DstResolution,
    RecurrenceKind,
    ScheduleEndReason,
    SchedulePolicy,
)

#: 一次计划槽在 Python 里无法表达的极小偏移量，用于“严格在其之后”。
_TICK = timedelta(microseconds=1)


class SlotKind(StrEnum):
    """本地计划槽在时区上的形态。"""

    NORMAL = "NORMAL"
    #: 本地壁钟不存在的时刻（DST 春季跳变）。
    GAP = "GAP"
    #: 本地壁钟出现两次的时刻（DST 秋季重复）。
    FOLD = "FOLD"


class PlanAction(StrEnum):
    """这一次 tick 的处理动作。"""

    #: 没有下一次计划（ENDED/CANCELLED/未配置）。
    IDLE = "IDLE"
    #: 触发一次真实 Fire。
    FIRE = "FIRE"
    #: 本地不存在的时刻：写事件、计一个槽、不建 Fire。
    DST_GAP = "DST_GAP"
    #: 错过区间：写一条 SKIPPED(MISFIRE) Fire 并聚合额外错过数。
    MISFIRE = "MISFIRE"
    #: 计划已到终点（until / max_occurrences / ONCE 已完成）。
    ENDED = "ENDED"


@dataclass(frozen=True)
class RecurrenceRule:
    kind: RecurrenceKind
    timezone: ZoneInfo
    cron: str | None = None
    run_at: datetime | None = None

    @property
    def timezone_name(self) -> str:
        return str(self.timezone)


@dataclass(frozen=True)
class LocalSlot:
    """一个本地计划槽：本地日期/时刻 + 它在时区上的形态 + 解析出的 UTC。"""

    local: datetime
    kind: SlotKind
    #: GAP 槽没有真实 UTC；这里的值是**扫描指针**，不会成为 Fire。
    utc: datetime

    @property
    def local_date(self) -> str:
        return self.local.date().isoformat()

    @property
    def local_time(self) -> str:
        return self.local.strftime("%H:%M:%S")


@dataclass(frozen=True)
class DstEventRecord:
    """不产生 Fire 的 DST 事实（持久化为 SCHEDULE 事件）。"""

    type: DstEventType
    local_date: str
    local_time: str


@dataclass(frozen=True)
class Advance:
    """推进结果：下一个可触发指针 + 途中被跳过的计划槽。"""

    next_fire_at: datetime | None
    gap_events: tuple[DstEventRecord, ...] = ()
    slots_consumed: int = 0


@dataclass(frozen=True)
class OccurrencePlan:
    action: PlanAction
    scheduled_at: datetime | None = None
    #: 只在 action=FIRE 且该槽是 fold 的第一次时给出。
    dst_resolution: DstResolution | None = None
    #: 额外错过的 occurrence 数（MISFIRE 用）。
    missed_count: int = 0
    #: 本次消耗的本地计划槽数量。
    slots_consumed: int = 0
    next_fire_at: datetime | None = None
    end_reason: ScheduleEndReason | None = None
    #: DST 事件审计所需的本地日期/时刻（gap 与 second-fold）。
    local_date: str | None = None
    local_time: str | None = None
    #: 本次 tick 需要一并落库的 DST 事件（gap 跳过 / fold 第二次）。
    dst_events: tuple[DstEventRecord, ...] = ()


def build_rule(
    *,
    kind: str,
    timezone: str,
    cron: str | None = None,
    run_at: datetime | None = None,
) -> RecurrenceRule:
    """构造规则；非法表达式/时区直接失败，不做静默兜底。"""
    try:
        zone = ZoneInfo(timezone)
    except (KeyError, ValueError) as exc:
        raise ValueError(f"unknown IANA timezone: {timezone}") from exc
    if kind == RecurrenceKind.CRON:
        if not cron or run_at is not None:
            raise ValueError("CRON recurrence requires cron and forbids run_at")
        try:
            croniter(cron)
        except (CroniterBadCronError, ValueError) as exc:
            raise ValueError(f"invalid cron expression: {cron}") from exc
        return RecurrenceRule(kind=RecurrenceKind.CRON, timezone=zone, cron=cron)
    if kind == RecurrenceKind.ONCE:
        if run_at is None or cron is not None:
            raise ValueError("ONCE recurrence requires run_at and forbids cron")
        if run_at.tzinfo is None:
            raise ValueError("run_at must be timezone-aware")
        return RecurrenceRule(
            kind=RecurrenceKind.ONCE, timezone=zone, run_at=run_at.astimezone(UTC)
        )
    raise ValueError(f"unsupported recurrence kind: {kind}")


def classify_local(local: datetime, zone: ZoneInfo) -> SlotKind:
    """判断一个本地壁钟时刻在该时区是否存在、是否重复。"""
    first = local.replace(tzinfo=zone, fold=0)
    round_trip = first.astimezone(UTC).astimezone(zone).replace(tzinfo=None)
    if round_trip != local:
        return SlotKind.GAP
    second = local.replace(tzinfo=zone, fold=1)
    if first.utcoffset() != second.utcoffset():
        return SlotKind.FOLD
    return SlotKind.NORMAL


def slot_for_local(local: datetime, zone: ZoneInfo) -> LocalSlot:
    """本地壁钟 → 计划槽。fold 按 FIRST 取第一次映射；gap 用指针值（不是 Fire 时刻）。"""
    kind = classify_local(local, zone)
    resolved = local.replace(tzinfo=zone, fold=0).astimezone(UTC)
    return LocalSlot(local=local, kind=kind, utc=resolved)


def next_slot(rule: RecurrenceRule, after: datetime) -> LocalSlot | None:
    """严格晚于 `after` 的下一个本地计划槽；ONCE 只在未来时给一次。"""
    after_local = after.astimezone(rule.timezone).replace(tzinfo=None)
    local = _next_local(rule, after_local)
    return None if local is None else slot_for_local(local, rule.timezone)


def slot_for_pointer(rule: RecurrenceRule, pointer: datetime) -> LocalSlot:
    """把 `next_fire_at` 指针还原成它代表的本地槽。

    指针是上次推进时算出的 UTC；还原后重新判定 gap/fold，避免“按旧结论直接触发”。
    """
    local = pointer.astimezone(rule.timezone).replace(tzinfo=None)
    return slot_for_local(local, rule.timezone)


def _next_local(rule: RecurrenceRule, after_local: datetime) -> datetime | None:
    if rule.kind is RecurrenceKind.ONCE:
        assert rule.run_at is not None
        local = rule.run_at.astimezone(rule.timezone).replace(tzinfo=None)
        return local if local > after_local else None
    assert rule.cron is not None
    try:
        return croniter(rule.cron, after_local).get_next(datetime)
    except (CroniterBadCronError, ValueError):  # pragma: no cover - 构造时已校验
        return None


def initial_fire_at(rule: RecurrenceRule, now: datetime) -> Advance:
    """新建/恢复后的第一个指针：从 now 起算，不补暂停期。"""
    return _next_fireable(rule, now)


def _next_fireable(rule: RecurrenceRule, after: datetime) -> Advance:
    """严格晚于 `after` 的下一个**可触发**指针，以及途中被跳过的 gap 槽。"""
    cursor = after
    gaps: list[LocalSlot] = []
    while True:
        slot = next_slot(rule, cursor)
        if slot is None:
            return Advance(None, tuple(_gap_event(item) for item in gaps), len(gaps))
        if slot.kind is SlotKind.GAP:
            # gap 没有真实 UTC：指针按 Python 的映射前进，绝不把它当作 Fire 时刻。
            gaps.append(slot)
            cursor = slot.utc
            continue
        return Advance(slot.utc, tuple(_gap_event(item) for item in gaps), len(gaps))


def _walk(rule: RecurrenceRule, first: LocalSlot, stop_before: datetime) -> list[LocalSlot]:
    """`first` 起、UTC 早于 `stop_before` 的计划槽（含 gap），按时间顺序。"""
    slots: list[LocalSlot] = [first]
    cursor = first.utc
    while True:
        following = next_slot(rule, cursor)
        if following is None or following.utc >= stop_before:
            return slots
        slots.append(following)
        cursor = following.utc


def plan_occurrence(
    *,
    rule: RecurrenceRule,
    policy: SchedulePolicy | None = None,
    next_fire_at: datetime | None,
    now: datetime,
    grace_sec: int,
    occurrence_count: int,
) -> OccurrencePlan:
    """给定当前指针与时钟，算出这一次该做什么。

    `next_fire_at` 一定是 `_next_fireable` 产出的可触发指针；指针就是本地槽的 UTC 解析，
    因此 fold 的第一次会在这里被判成 FOLD 并带上 `dst_resolution`。
    """
    if next_fire_at is None:
        return OccurrencePlan(action=PlanAction.IDLE)
    if next_fire_at.tzinfo is None or now.tzinfo is None:
        raise ValueError("next_fire_at and now must be timezone-aware")
    policy = policy or SchedulePolicy()
    slot = slot_for_pointer(rule, next_fire_at)
    if slot.kind is SlotKind.GAP:  # pragma: no cover - 指针由 _next_fireable 保证
        raise ValueError("gap slots are never trigger pointers")

    stale_before = now - timedelta(seconds=grace_sec)
    if slot.utc < stale_before:
        skipped = _walk(rule, slot, stale_before)
        skipped_gaps = [item for item in skipped if item.kind is SlotKind.GAP]
        missed = len(skipped) - 1
        advance = _next_fireable(rule, now)
        return _consumed(
            rule,
            policy,
            plan=OccurrencePlan(
                action=PlanAction.MISFIRE,
                scheduled_at=slot.utc,
                missed_count=missed,
                next_fire_at=advance.next_fire_at,
                local_date=slot.local_date,
                local_time=slot.local_time,
                dst_events=(*_gap_events(skipped_gaps), *advance.gap_events),
            ),
            slots=len(skipped) + advance.slots_consumed,
            occurrence_count=occurrence_count,
            now=now,
        )

    following = next_slot(rule, slot.utc)
    gaps: list[LocalSlot] = []
    while following is not None and following.kind is SlotKind.GAP:
        gaps.append(following)
        following = next_slot(rule, following.utc)
    events = tuple(_gap_event(item) for item in gaps)
    if slot.kind is SlotKind.FOLD:
        events = (*events, _fold_event(slot))

    return _consumed(
        rule,
        policy,
        plan=OccurrencePlan(
            action=PlanAction.FIRE,
            scheduled_at=slot.utc,
            dst_resolution=DstResolution.FOLD_FIRST if slot.kind is SlotKind.FOLD else None,
            next_fire_at=following.utc if following is not None else None,
            local_date=slot.local_date,
            local_time=slot.local_time,
            dst_events=events,
        ),
        slots=1 + len(gaps),
        occurrence_count=occurrence_count,
        now=now,
    )


def _gap_events(slots: list[LocalSlot]) -> tuple[DstEventRecord, ...]:
    return tuple(_gap_event(item) for item in slots)


def _gap_event(slot: LocalSlot) -> DstEventRecord:
    return DstEventRecord(
        type=DstEventType.DST_GAP_SKIPPED,
        local_date=slot.local_date,
        local_time=slot.local_time,
    )


def _fold_event(slot: LocalSlot) -> DstEventRecord:
    return DstEventRecord(
        type=DstEventType.DST_FOLD_SECOND_SKIPPED,
        local_date=slot.local_date,
        local_time=slot.local_time,
    )


def _consumed(
    rule: RecurrenceRule,
    policy: SchedulePolicy,
    *,
    plan: OccurrencePlan,
    slots: int,
    occurrence_count: int,
    now: datetime,
) -> OccurrencePlan:
    """结算计划槽与结束条件（until / max_occurrences / ONCE 完成）。"""
    end_reason = _end_reason(
        rule,
        policy,
        total=occurrence_count + slots,
        next_fire_at=plan.next_fire_at,
        now=now,
    )
    return OccurrencePlan(
        action=plan.action,
        scheduled_at=plan.scheduled_at,
        dst_resolution=plan.dst_resolution,
        missed_count=plan.missed_count,
        slots_consumed=slots,
        next_fire_at=None if end_reason is not None else plan.next_fire_at,
        end_reason=end_reason,
        local_date=plan.local_date,
        local_time=plan.local_time,
        dst_events=plan.dst_events,
    )


def _end_reason(
    rule: RecurrenceRule,
    policy: SchedulePolicy,
    *,
    total: int,
    next_fire_at: datetime | None,
    now: datetime,
) -> ScheduleEndReason | None:
    if policy.max_occurrences is not None and total >= policy.max_occurrences:
        return ScheduleEndReason.EXHAUSTED
    if next_fire_at is None:
        return ScheduleEndReason.COMPLETED
    if policy.until is not None and next_fire_at > policy.until:
        return ScheduleEndReason.COMPLETED
    if rule.kind is RecurrenceKind.ONCE:
        # ONCE 只有一次；错过由 Misfire 分支承担，成功即完成。
        return ScheduleEndReason.COMPLETED
    if policy.until is not None and now > policy.until:
        return ScheduleEndReason.COMPLETED
    return None


def occurrence_key(schedule_id: uuid.UUID, scheduled_at: datetime) -> tuple[uuid.UUID, datetime]:
    """`(schedule_id, scheduled_at)` 是 occurrence 身份；revision 不进 key。"""
    return schedule_id, to_utc(scheduled_at)


def execution_key(schedule_id: uuid.UUID, scheduled_at: datetime) -> str:
    """Execution 幂等键：固定 UTC、微秒精度、Z 结尾，等价 offset 收敛到同一串。"""
    return f"schedule:{schedule_id}:{format_rfc3339_us(scheduled_at)}"


def format_rfc3339_us(moment: datetime) -> str:
    return to_utc(moment).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def to_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        raise ValueError("scheduled_at must be timezone-aware")
    return moment.astimezone(UTC)


__all__ = [
    "Advance",
    "DstEventRecord",
    "LocalSlot",
    "OccurrencePlan",
    "PlanAction",
    "RecurrenceRule",
    "SlotKind",
    "build_rule",
    "classify_local",
    "execution_key",
    "format_rfc3339_us",
    "initial_fire_at",
    "next_slot",
    "occurrence_key",
    "plan_occurrence",
    "slot_for_local",
    "slot_for_pointer",
    "to_utc",
]
