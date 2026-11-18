"""`SchedulePlan` and the authoring card facts (run-failure-reporting §10.4 / §10.11).

The plan is the only executable shape. Authoring metadata (confidence/reason) belongs to
the preview/card, never to `ScheduleJob`/`ScheduleDraftRequest`: a confirmed Job must not
carry model self-assessment into its frozen configuration.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from muad_contracts import (
    AgentTurnPayload,
    DeliveryPlan,
    ReminderPayload,
    ScheduleDraftRequest,
    ScheduleJob,
    ScheduleMode,
    SchedulePlan,
    SchedulePreview,
    SessionTarget,
    SkillPlanPayload,
)
from pydantic import ValidationError

pytestmark = pytest.mark.contract

SKILL_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
SESSION_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")


def _job(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "instruction": "每天 9 点给客户出日报",
        "recurrence": {"kind": "CRON", "cron": "0 9 * * *", "timezone": "Asia/Shanghai"},
        "payload": {"kind": "REMINDER", "text": "开会"},
    }
    payload.update(overrides)
    return payload


class TestPlanShape:
    def test_reminder_plan_has_no_tools(self) -> None:
        plan = SchedulePlan(payload={"kind": "REMINDER", "text": "开会"})
        assert isinstance(plan.payload, ReminderPayload)
        assert plan.derived_mode is ScheduleMode.REMINDER
        assert plan.resolved_tools().tools_allow == []

    def test_skill_plan_keeps_stdin_and_argv_separate(self) -> None:
        plan = SchedulePlan(
            payload={
                "kind": "SKILL_PLAN",
                "skill_id": str(SKILL_ID),
                "input": {"company_id": "22921947"},
                "argv": ["--date", "{{previous_day}}"],
                "window": {"kind": "DAY", "offset": -1},
            }
        )
        assert isinstance(plan.payload, SkillPlanPayload)
        assert plan.derived_mode is ScheduleMode.SKILL
        assert plan.payload.input == {"company_id": "22921947"}
        assert plan.payload.argv == ["--date", "{{previous_day}}"]

    def test_agent_turn_uses_the_payload_tool_surface(self) -> None:
        plan = SchedulePlan(
            payload={
                "kind": "AGENT_TURN",
                "prompt": "汇总昨天的任务",
                "tools_allow": [f"SKILL:{SKILL_ID}:report.render", "PLATFORM:list_tasks"],
            }
        )
        assert isinstance(plan.payload, AgentTurnPayload)
        assert plan.derived_mode is ScheduleMode.AGENTIC
        assert plan.resolved_tools().tools_allow == [
            f"SKILL:{SKILL_ID}:report.render",
            "PLATFORM:list_tasks",
        ]

    def test_empty_agent_turn_tools_follow_the_owner(self) -> None:
        plan = SchedulePlan(payload={"kind": "AGENT_TURN", "prompt": "检查一次"})
        assert plan.resolved_tools().follows_owner

    def test_session_and_delivery_are_independent(self) -> None:
        plan = SchedulePlan(
            payload={"kind": "REMINDER", "text": "开会"},
            session_target=f"session:{SESSION_ID}",
            delivery={"intent": "NONE"},
        )
        assert plan.session_target.session_id == SESSION_ID
        assert plan.delivery.intent.value == "NONE"
        assert plan.delivery.route is None

    def test_push_needs_a_route(self) -> None:
        route = {"channel": "WECOM", "bot_id": "bot-1", "external_user_id": "u-1"}
        assert DeliveryPlan(intent="PUSH", route=route).route is not None
        with pytest.raises(ValidationError):
            SchedulePlan(
                payload={"kind": "REMINDER", "text": "开会"},
                delivery={"intent": "PUSH"},
            )

    def test_session_target_rejects_unknown_shapes(self) -> None:
        for value in ("persistent", "session:", "session:not-a-uuid"):
            with pytest.raises(ValidationError):
                SessionTarget(value)


class TestPreviewAuthoringFacts:
    def _preview(self, **overrides: object) -> SchedulePreview:
        data: dict[str, object] = {
            "mode": "REMINDER",
            "payload": {"kind": "REMINDER", "text": "开会"},
            "session_target": "isolated",
            "what": "开会",
            "when": "CRON 0 9 * * * (Asia/Shanghai)",
            "first_fire_at": datetime.now(UTC) + timedelta(hours=1),
            "delivery": "NONE",
            "tools_allow": [],
            "budget_source": "NONE",
            "dependencies": [],
            "budget": {},
        }
        data.update(overrides)
        return SchedulePreview.model_validate(data)

    def test_preview_defaults_are_compatible_with_existing_drafts(self) -> None:
        preview = self._preview()
        assert preview.confidence == 1.0
        assert preview.reason == ""

    def test_preview_carries_confidence_and_reason(self) -> None:
        preview = self._preview(confidence=0.82, reason="固定客户与固定参数")
        assert preview.confidence == 0.82
        assert preview.reason == "固定客户与固定参数"

    def test_confidence_is_bounded(self) -> None:
        for value in (-0.1, 1.1):
            with pytest.raises(ValidationError):
                SchedulePreview.model_validate(
                    {
                        "mode": "REMINDER",
                        "payload": {"kind": "REMINDER", "text": "开会"},
                        "session_target": "isolated",
                        "what": "开会",
                        "when": "CRON",
                        "first_fire_at": datetime.now(UTC),
                        "delivery": "NONE",
                        "confidence": value,
                    }
                )

    def test_authoring_metadata_is_not_job_config(self) -> None:
        job = ScheduleJob(**_job())
        assert "confidence" not in job.model_dump(mode="json")
        assert "reason" not in job.model_dump(mode="json")
        with pytest.raises(ValidationError):
            ScheduleJob(**_job(confidence=0.8))
        with pytest.raises(ValidationError):
            ScheduleDraftRequest(**_job(reason="why"))

    def test_mode_is_derived_from_payload_not_accepted_as_input(self) -> None:
        with pytest.raises(ValidationError):
            ScheduleDraftRequest(**_job(mode="REMINDER"))
        with pytest.raises(ValidationError):
            ScheduleDraftRequest(
                **_job(payload={"kind": "REMINDER", "text": "开会", "mode": "REMINDER"})
            )
