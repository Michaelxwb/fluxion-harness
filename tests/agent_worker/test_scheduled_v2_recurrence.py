"""recurrence 的纯计算口径（scheduled-task-v2 TASK-004 / RULE-time-001）。

真实边界：真实 `zoneinfo` 时区数据库；不 mock DST 规则。所有时间都由用例显式给出，
不看系统时钟，因此“春季跳变 / 秋季重复 / 跨 DST 的错过区间”都是确定性的。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from muad_agent_worker.scheduler.recurrence import (
    PlanAction,
    SlotKind,
    build_rule,
    classify_local,
    execution_key,
    initial_fire_at,
    plan_occurrence,
    slot_for_local,
)
from muad_contracts import SchedulePolicy

pytestmark = pytest.mark.unit

NEW_YORK = "America/New_York"
SHANGHAI = "Asia/Shanghai"
NY = ZoneInfo(NEW_YORK)
SH = ZoneInfo(SHANGHAI)
#: 2026-03-08 02:00 美东进入夏令时（本地 02:00→03:00，gap）。
SPRING_FORWARD = datetime(2026, 3, 8, tzinfo=UTC)
#: 2026-11-01 02:00 美东退出夏令时（本地 01:00-02:00 出现两次，fold）。
FALL_BACK = datetime(2026, 11, 1, tzinfo=UTC)


def _cron(expr: str, timezone: str = NEW_YORK):
    return build_rule(kind="CRON", timezone=timezone, cron=expr)


def _once(run_at: datetime, timezone: str = SHANGHAI):
    return build_rule(kind="ONCE", timezone=timezone, run_at=run_at)


class TestRuleValidation:
    def test_unknown_timezone_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="unknown IANA timezone"):
            _cron("0 9 * * *", timezone="Not/AZone")

    def test_invalid_cron_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="invalid cron"):
            _cron("not a cron")

    def test_cron_and_once_are_mutually_exclusive(self) -> None:
        with pytest.raises(ValueError):
            build_rule(kind="CRON", timezone=SHANGHAI, cron="0 9 * * *", run_at=SPRING_FORWARD)
        with pytest.raises(ValueError):
            build_rule(kind="ONCE", timezone=SHANGHAI, run_at=SPRING_FORWARD, cron="0 9 * * *")

    def test_naive_run_at_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            build_rule(kind="ONCE", timezone=SHANGHAI, run_at=datetime(2026, 3, 9, 9, 0))

    def test_plan_requires_aware_clock(self) -> None:
        rule = _cron("0 9 * * *")
        with pytest.raises(ValueError, match="timezone-aware"):
            plan_occurrence(
                rule=rule,
                next_fire_at=datetime(2026, 3, 9, 9, 0),
                now=datetime(2026, 3, 9, 9, 0, tzinfo=UTC),
                grace_sec=300,
                occurrence_count=0,
            )


class TestWallClockSlots:
    def test_local_time_is_interpreted_in_the_schedule_timezone(self) -> None:
        # 上海 09:00 = 01:00Z；用服务器本地时区就会错过 8 小时。
        rule = _cron("0 9 * * *", timezone=SHANGHAI)
        advance = initial_fire_at(rule, datetime(2026, 3, 8, tzinfo=UTC))
        assert advance.next_fire_at == datetime(2026, 3, 8, 1, 0, tzinfo=UTC)
        assert advance.slots_consumed == 0

    def test_gap_slot_is_classified_and_never_a_fire(self) -> None:
        local = datetime(2026, 3, 8, 2, 30)
        assert classify_local(local, NY) is SlotKind.GAP
        rule = _cron("30 2 * * *")
        advance = initial_fire_at(rule, datetime(2026, 3, 8, tzinfo=UTC))
        # 02:30 不存在：直接推进到次日，但事件与计划槽必须记下来。
        assert advance.next_fire_at == datetime(2026, 3, 9, 6, 30, tzinfo=UTC)
        assert advance.slots_consumed == 1
        event = advance.gap_events[0]
        assert event.type == "DST_GAP_SKIPPED"
        assert (event.local_date, event.local_time) == ("2026-03-08", "02:30:00")

    def test_fold_slot_fires_once_and_audits_the_second_mapping(self) -> None:
        rule = _cron("30 1 * * *")
        # 2026-11-01 01:30 美东出现两次：第一次 05:30Z（EDT），第二次 06:30Z（EST）。
        pointer = datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
        plan = plan_occurrence(
            rule=rule,
            next_fire_at=pointer,
            now=pointer,
            grace_sec=300,
            occurrence_count=0,
        )
        assert plan.action is PlanAction.FIRE
        assert plan.scheduled_at == pointer
        assert plan.dst_resolution == "FOLD_FIRST"
        assert plan.slots_consumed == 1
        assert [event.type for event in plan.dst_events] == ["DST_FOLD_SECOND_SKIPPED"]

    def test_slot_for_local_uses_first_mapping_for_fold(self) -> None:
        zone = NY
        slot = slot_for_local(datetime(2026, 11, 1, 1, 30), zone)
        assert slot.kind is SlotKind.FOLD
        assert slot.utc == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)


class TestMisfire:
    def test_misfire_aggregates_extra_misses_into_one_fire(self) -> None:
        rule = _cron("0 * * * *")  # 每小时整点
        pointer = datetime(2026, 3, 8, 0, 0, tzinfo=UTC)
        now = pointer + timedelta(hours=5, minutes=10)
        plan = plan_occurrence(
            rule=rule,
            next_fire_at=pointer,
            now=now,
            grace_sec=300,
            occurrence_count=0,
        )
        assert plan.action is PlanAction.MISFIRE
        # grace 边界是 now-5min=05:05：00:00-05:00 都算过期，处理 00:00，额外错过 5 次。
        assert plan.missed_count == 5
        assert plan.slots_consumed == 6
        assert plan.next_fire_at == datetime(2026, 3, 8, 6, 0, tzinfo=UTC)

    def test_occurrence_count_tracks_local_slots_not_fires(self) -> None:
        rule = _cron("0 * * * *")
        pointer = datetime(2026, 3, 8, 0, 0, tzinfo=UTC)
        plan = plan_occurrence(
            rule=rule,
            next_fire_at=pointer,
            now=pointer + timedelta(hours=1, minutes=5),
            grace_sec=300,
            occurrence_count=3,
        )
        # 01:00 正好落在 grace 边界上：不算错过，本次只处理 00:00 这一个槽。
        assert plan.slots_consumed == 1
        assert plan.missed_count == 0

    def test_misfire_across_dst_counts_wall_clock_slots(self) -> None:
        rule = _cron("30 2 * * *")
        pointer = datetime(2026, 3, 6, 7, 30, tzinfo=UTC)
        now = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
        plan = plan_occurrence(
            rule=rule,
            next_fire_at=pointer,
            now=now,
            grace_sec=0,
            occurrence_count=0,
        )
        assert plan.action is PlanAction.MISFIRE
        # 03-06、03-07、03-09、03-10 是真实槽，03-08 是 gap：5 个计划槽里额外错过 4 个。
        assert plan.slots_consumed == 5
        assert plan.missed_count == 4
        assert [event.local_date for event in plan.dst_events if event.type == "DST_GAP_SKIPPED"] == [
            "2026-03-08"
        ]


class TestEndConditions:
    def test_max_occurrences_ends_the_schedule(self) -> None:
        rule = _cron("0 9 * * *", timezone=SHANGHAI)
        plan = plan_occurrence(
            rule=rule,
            policy=SchedulePolicy(max_occurrences=1),
            next_fire_at=datetime(2026, 3, 8, 1, 0, tzinfo=UTC),
            now=datetime(2026, 3, 8, 1, 0, tzinfo=UTC),
            grace_sec=300,
            occurrence_count=0,
        )
        assert plan.action is PlanAction.FIRE
        assert plan.end_reason == "EXHAUSTED"
        assert plan.next_fire_at is None

    def test_until_stops_the_schedule(self) -> None:
        rule = _cron("0 9 * * *", timezone=SHANGHAI)
        plan = plan_occurrence(
            rule=rule,
            policy=SchedulePolicy(until=datetime(2026, 3, 9, 0, 0, tzinfo=UTC)),
            next_fire_at=datetime(2026, 3, 8, 1, 0, tzinfo=UTC),
            now=datetime(2026, 3, 8, 1, 0, tzinfo=UTC),
            grace_sec=300,
            occurrence_count=0,
        )
        assert plan.end_reason == "COMPLETED"
        assert plan.next_fire_at is None

    def test_once_completes_after_its_single_fire(self) -> None:
        run_at = datetime(2026, 3, 9, 9, 0, tzinfo=SH)
        plan = plan_occurrence(
            rule=_once(run_at),
            next_fire_at=run_at,
            now=run_at,
            grace_sec=300,
            occurrence_count=0,
        )
        assert plan.action is PlanAction.FIRE
        assert plan.end_reason == "COMPLETED"

    def test_once_missed_beyond_grace_does_not_backfill(self) -> None:
        run_at = datetime(2026, 3, 9, 9, 0, tzinfo=SH)
        plan = plan_occurrence(
            rule=_once(run_at),
            next_fire_at=run_at,
            now=run_at + timedelta(hours=2),
            grace_sec=300,
            occurrence_count=0,
        )
        assert plan.action is PlanAction.MISFIRE
        assert plan.missed_count == 0
        assert plan.end_reason == "COMPLETED"

    def test_resume_recomputes_from_now_without_backfill(self) -> None:
        rule = _cron("0 9 * * *", timezone=SHANGHAI)
        advance = initial_fire_at(rule, datetime(2026, 3, 10, 12, 0, tzinfo=UTC))
        assert advance.next_fire_at == datetime(2026, 3, 11, 1, 0, tzinfo=UTC)

    def test_once_in_the_past_has_no_future_pointer(self) -> None:
        rule = _once(datetime(2026, 3, 9, 9, 0, tzinfo=SH))
        assert initial_fire_at(rule, datetime(2026, 3, 10, tzinfo=UTC)).next_fire_at is None


class TestExecutionKey:
    def test_key_is_utc_microseconds_and_z(self) -> None:
        schedule_id = uuid.uuid4()
        moment = datetime(2026, 3, 8, 9, 0, 0, 123456, tzinfo=UTC)
        assert (
            execution_key(schedule_id, moment)
            == f"schedule:{schedule_id}:2026-03-08T09:00:00.123456Z"
        )

    def test_equivalent_offsets_produce_the_same_key(self) -> None:
        schedule_id = uuid.uuid4()
        shanghai = datetime(2026, 3, 8, 17, 0, tzinfo=SH)
        utc = datetime(2026, 3, 8, 9, 0, tzinfo=UTC)
        assert execution_key(schedule_id, shanghai) == execution_key(schedule_id, utc)

    def test_naive_instant_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            execution_key(uuid.uuid4(), datetime(2026, 3, 8, 9, 0))
