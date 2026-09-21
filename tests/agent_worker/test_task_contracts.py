"""Task/Schedule 公共契约收紧（B-103 / RULE-api-001）。

unit 层：只验证 Pydantic 公共契约与序列化，不连数据库。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from muad_contracts import (
    CancelTaskResponse,
    CreateScheduleRequest,
    CreateTaskRequest,
    ScheduleListQuery,
    ScheduleSpec,
    ScheduleStatus,
    TaskListQuery,
    TaskStatus,
    TaskType,
)
from muad_contracts.tasks import DeliveryRouteInput
from pydantic import ValidationError

NOW = datetime(2026, 9, 21, 10, 0, tzinfo=UTC)


def _task_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "tenant_id": "t1",
        "agent_id": uuid.uuid4(),
        "actor_user_id": uuid.uuid4(),
        "intent_key": "policy_check",
        "skill_id": uuid.uuid4(),
        "skill_artifact_id": uuid.uuid4(),
        "input": {},
        "execution_snapshot": {},
        "snapshot_hash": "sha256:" + "a" * 64,
        "idempotency_key": "k1",
        "delivery_route": {"channel": "WECOM", "bot_id": "b1", "external_user_id": "u1"},
    }
    payload.update(overrides)
    return payload


def _schedule_payload(spec: dict[str, object]) -> dict[str, object]:
    return {
        "name": "daily",
        "agent_id": uuid.uuid4(),
        "actor_user_id": uuid.uuid4(),
        "intent_key": "policy_check",
        "skill_id": uuid.uuid4(),
        "schedule": spec,
        "delivery_route": {"channel": "WECOM", "bot_id": "b1", "external_user_id": "u1"},
    }


class TestScheduleSpec:
    def test_cron_requires_cron_expression(self) -> None:
        with pytest.raises(ValidationError):
            ScheduleSpec(type="CRON", timezone="Asia/Shanghai")

    def test_once_requires_run_at(self) -> None:
        with pytest.raises(ValidationError):
            ScheduleSpec(type="ONCE", timezone="Asia/Shanghai")

    def test_cron_and_once_are_mutually_required(self) -> None:
        assert ScheduleSpec(type="CRON", cron="0 9 * * *", timezone="Asia/Shanghai").cron == "0 9 * * *"
        assert ScheduleSpec(type="ONCE", run_at=NOW, timezone="UTC").run_at == NOW

    def test_unknown_iana_timezone_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ScheduleSpec(type="CRON", cron="0 9 * * *", timezone="Not/AZone")


class TestStatusEnums:
    def test_schedule_status_includes_missed_terminal_state(self) -> None:
        assert ScheduleStatus.MISSED == "MISSED"
        assert {status.value for status in ScheduleStatus} == {
            "ACTIVE",
            "PAUSED",
            "COMPLETED",
            "MISSED",
        }

    def test_task_status_has_no_cancelling(self) -> None:
        assert "CANCELLING" not in {status.value for status in TaskStatus}

    def test_task_type_only_skill_and_batch(self) -> None:
        assert {task_type.value for task_type in TaskType} == {"SKILL", "BATCH"}


class TestCreateTaskRequest:
    def test_final_only_requires_delivery_route(self) -> None:
        with pytest.raises(ValidationError):
            CreateTaskRequest(**_task_payload(delivery_route=None, delivery_mode="FINAL_ONLY"))

    def test_unknown_extra_field_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CreateTaskRequest(**_task_payload(unexpected_field="x"))

    def test_external_and_agent_step_are_not_accepted(self) -> None:
        for task_type in ("EXTERNAL", "AGENT_STEP"):
            with pytest.raises(ValidationError):
                CreateTaskRequest(**_task_payload(task_type=task_type))


class TestCreateScheduleRequest:
    def test_misfire_policy_is_rejected(self) -> None:
        payload = _schedule_payload({"type": "CRON", "cron": "0 9 * * *", "timezone": "UTC"})
        payload["misfire_policy"] = "FIRE_ONCE"
        with pytest.raises(ValidationError):
            CreateScheduleRequest(**payload)

    def test_valid_schedule_is_accepted(self) -> None:
        payload = _schedule_payload({"type": "CRON", "cron": "0 9 * * *", "timezone": "Asia/Shanghai"})
        request = CreateScheduleRequest(**payload)
        assert isinstance(request.delivery_route, DeliveryRouteInput)


class TestTaskListQuery:
    def test_page_size_is_bounded_to_100(self) -> None:
        assert TaskListQuery(page_size=100).page_size == 100
        with pytest.raises(ValidationError):
            TaskListQuery(page_size=101)
        with pytest.raises(ValidationError):
            TaskListQuery(page_size=0)

    def test_page_must_be_at_least_one(self) -> None:
        assert TaskListQuery(page=1).page == 1
        with pytest.raises(ValidationError):
            TaskListQuery(page=0)

    def test_deadline_filter_is_independent_from_create_time_filter(self) -> None:
        query = TaskListQuery(start_time=NOW, end_time=NOW, deadline_from=NOW, deadline_to=NOW)
        assert query.start_time == NOW and query.end_time == NOW
        assert query.deadline_from == NOW and query.deadline_to == NOW

    def test_defaults_are_applied(self) -> None:
        query = TaskListQuery()
        assert query.page == 1 and query.page_size == 20
        assert query.deadline_from is None and query.deadline_to is None


class TestScheduleListQuery:
    def test_status_filter_accepts_missed(self) -> None:
        assert ScheduleListQuery(status=ScheduleStatus.MISSED).status is ScheduleStatus.MISSED

    def test_page_size_is_bounded_to_100(self) -> None:
        with pytest.raises(ValidationError):
            ScheduleListQuery(page_size=101)


class TestCancelTaskResponse:
    def test_non_terminal_cancel_reports_running_with_flag(self) -> None:
        response = CancelTaskResponse(
            task_id=uuid.uuid4(), status=TaskStatus.RUNNING, cancel_requested=True
        )
        assert response.status == "RUNNING" and response.cancel_requested is True

    def test_cancelling_is_not_a_valid_cancel_status(self) -> None:
        with pytest.raises(ValidationError):
            CancelTaskResponse(task_id=uuid.uuid4(), status="CANCELLING", cancel_requested=True)

    def test_direct_cancellation_has_no_request_flag(self) -> None:
        response = CancelTaskResponse(task_id=uuid.uuid4(), status=TaskStatus.CANCELLED)
        assert response.cancel_requested is False
