"""Authoring compiler: validate one model proposal before any Draft exists.

The model may describe the business intent only; this compiler derives the payload kind,
checks it against the already-resolved Run materials and returns either a
`SchedulePlan`, one business question, or structured field errors. It never resolves
materials itself (Worker admission owns that) and never silently downgrades a plan.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from muad_agent_runtime.application.schedule_authoring import (
    ScheduleAuthoringContext,
    ScheduleAuthoringError,
    compile_schedule_plan,
)
from muad_contracts import (
    AgentTurnPayload,
    DeliveryRouteInput,
    ReminderPayload,
    ResolvedMcpServer,
    ResolvedSkill,
    SkillExecutionMode,
    SkillPlanPayload,
    SkillSource,
)

pytestmark = pytest.mark.unit

SKILL_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
TZ = "Asia/Shanghai"


def _skill(*, key: str = "daily-report", frontmatter: dict[str, Any] | None = None) -> ResolvedSkill:
    return ResolvedSkill(
        skill_id=SKILL_ID,
        artifact_id=uuid.uuid4(),
        key=key,
        name="值守日报",
        description="Render the daily duty report",
        version="1.0.0",
        checksum="sha256:" + "c" * 64,
        storage_key="skills/daily-report.tar.gz",
        execution_mode=SkillExecutionMode.SYNC,
        frontmatter=frontmatter or {},
        source=SkillSource.ADMIN,
    )


def _mcp() -> ResolvedMcpServer:
    return ResolvedMcpServer(
        mcp_server_id=uuid.uuid4(),
        key="crm",
        endpoint="https://crm.internal/mcp",
        catalog_revision=3,
        definitions=[{"name": "customer.report", "input_schema": {}}],
    )


def _context(
    *, skills: tuple[ResolvedSkill, ...] = (), entry_channel: str | None = "WEBCHAT", **extra: Any
) -> ScheduleAuthoringContext:
    return ScheduleAuthoringContext(
        skills={skill.key: skill for skill in skills},
        mcp_servers=(_mcp(),),
        entry_channel=entry_channel,
        **extra,
    )


def _args(payload: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "instruction": "每天 9 点给客户 22921947 出昨天的值守日报",
        "timezone": TZ,
        "recurrence": {"kind": "CRON", "cron": "0 9 * * *"},
        "payload": payload,
    }
    args.update(overrides)
    return args


def _fields(exc: ScheduleAuthoringError) -> list[dict[str, Any]]:
    return list(exc.data["fields"])


class TestKindDerivation:
    def test_reminder_is_a_fixed_notification_without_tools(self) -> None:
        result = compile_schedule_plan(
            _args({"text": "9 点开周会"}), context=_context()
        )
        assert isinstance(result.plan.payload, ReminderPayload)
        assert result.plan.derived_mode.value == "REMINDER"
        assert result.budget_source == "NONE"
        assert result.tools_allow == []

    def test_daily_report_is_a_fixed_skill_plan(self) -> None:
        result = compile_schedule_plan(
            _args(
                {
                    "skill_key": "daily-report",
                    "input": {"company_id": "22921947"},
                    "argv": ["--company-id", "22921947", "--date", "{{previous_day}}"],
                    "window": {"kind": "DAY", "offset": -1},
                }
            ),
            context=_context(skills=(_skill(),)),
        )
        payload = result.plan.payload
        assert isinstance(payload, SkillPlanPayload)
        assert payload.skill_id == SKILL_ID
        assert payload.input == {"company_id": "22921947"}
        assert payload.argv[-1] == "{{previous_day}}"
        assert result.budget_source == "NONE"

    def test_open_ended_summary_is_an_agent_turn(self) -> None:
        result = compile_schedule_plan(
            _args(
                {
                    "prompt": "汇总 {{previous_day}} 的定时任务执行并指出阻塞项",
                    "tools_allow": ["PLATFORM:list_tasks", f"SKILL:{SKILL_ID}:report.render"],
                }
            ),
            context=_context(skills=(_skill(),)),
        )
        payload = result.plan.payload
        assert isinstance(payload, AgentTurnPayload)
        assert payload.tools_allow == [
            "PLATFORM:list_tasks",
            f"SKILL:{SKILL_ID}:report.render",
        ]
        assert result.budget_source == "PLATFORM"

    def test_kind_is_derived_from_the_fields_not_a_mode_choice(self) -> None:
        result = compile_schedule_plan(
            _args({"skill_key": "daily-report"}), context=_context(skills=(_skill(),))
        )
        assert isinstance(result.plan.payload, SkillPlanPayload)
        assert result.plan.payload.kind == "SKILL_PLAN"

    def test_unknown_kind_is_rejected(self) -> None:
        with pytest.raises(ScheduleAuthoringError) as caught:
            compile_schedule_plan(
                _args({"kind": "SKILL", "text": "x"}), context=_context()
            )
        assert caught.value.code == "SCHEDULE_DRAFT_INVALID"
        assert _fields(caught.value)[0]["field"] == "payload.kind"
        assert "REMINDER" in _fields(caught.value)[0]["allowed_values"]

    def test_conflicting_payload_families_are_rejected(self) -> None:
        with pytest.raises(ScheduleAuthoringError) as caught:
            compile_schedule_plan(
                _args({"prompt": "汇总", "text": "提醒"}), context=_context()
            )
        error = _fields(caught.value)[0]
        assert error["field"] == "payload.kind"
        assert error["reason"] == "KIND_CONFLICT"


class TestObviousPayloadErrors:
    @pytest.mark.parametrize(
        ("payload", "field", "reason", "code"),
        [
            ({"kind": "AGENT_TURN"}, "payload.prompt", "MISSING", "SCHEDULE_PARAMETER_INCOMPLETE"),
            ({"kind": "SKILL_PLAN"}, "payload.skill_key", "MISSING", "SCHEDULE_PARAMETER_INCOMPLETE"),
            ({"kind": "REMINDER"}, "payload.text", "MISSING", "SCHEDULE_PARAMETER_INCOMPLETE"),
            (
                {"kind": "REMINDER", "prompt": "x"},
                "payload.prompt",
                "FIELD_NOT_ALLOWED",
                "SCHEDULE_PARAMETER_INCOMPLETE",
            ),
        ],
    )
    def test_wrong_payload_returns_a_structured_field_error(
        self, payload: dict[str, Any], field: str, reason: str, code: str
    ) -> None:
        with pytest.raises(ScheduleAuthoringError) as caught:
            compile_schedule_plan(_args(payload), context=_context(skills=(_skill(),)))
        assert caught.value.code == code
        assert _fields(caught.value)[0]["field"] == field
        assert _fields(caught.value)[0]["reason"] == reason
        assert _fields(caught.value)[0]["repair"]


class TestSkillContract:
    def test_unknown_skill_lists_effective_skills(self) -> None:
        with pytest.raises(ScheduleAuthoringError) as caught:
            compile_schedule_plan(
                _args({"skill_key": "missing"}), context=_context(skills=(_skill(),))
            )
        error = _fields(caught.value)[0]
        assert error["field"] == "payload.skill_key"
        assert error["reason"] == "SKILL_NOT_EFFECTIVE"
        assert error["allowed_values"] == ["daily-report"]

    def test_declared_entrypoint_must_be_inside_the_package(self) -> None:
        skill = _skill(frontmatter={"entrypoint": "../escape.py"})
        with pytest.raises(ScheduleAuthoringError) as caught:
            compile_schedule_plan(
                _args({"skill_key": "daily-report"}), context=_context(skills=(skill,))
            )
        error = _fields(caught.value)[0]
        assert error["reason"] == "ENTRYPOINT_INVALID"
        assert error["repair"] == "REIMPORT_SKILL"

    def test_missing_entrypoint_falls_back_to_the_package_default(self) -> None:
        result = compile_schedule_plan(
            _args({"skill_key": "daily-report"}), context=_context(skills=(_skill(),))
        )
        assert isinstance(result.plan.payload, SkillPlanPayload)

    def test_input_cannot_carry_envelope_control_fields(self) -> None:
        with pytest.raises(ScheduleAuthoringError) as caught:
            compile_schedule_plan(
                _args({"skill_key": "daily-report", "input": {"company_id": "1", "argv": []}}),
                context=_context(skills=(_skill(),)),
            )
        error = _fields(caught.value)[0]
        assert error["field"] == "payload.input.argv"
        assert error["reason"] == "CONTROL_FIELD_IN_INPUT"

    def test_unknown_window_variable_is_rejected_with_the_allowlist(self) -> None:
        with pytest.raises(ScheduleAuthoringError) as caught:
            compile_schedule_plan(
                _args(
                    {
                        "skill_key": "daily-report",
                        "input": {"day": "{{yesterday}}"},
                        "argv": ["--date", "{{previous_day}}"],
                    }
                ),
                context=_context(skills=(_skill(),)),
            )
        error = _fields(caught.value)[0]
        assert error["field"] == "payload.input"
        assert error["reason"] == "UNKNOWN_WINDOW_VARIABLE"
        assert error["allowed_values"] == ["fire_date", "fire_time", "previous_day"]

    def test_argv_must_be_an_array_of_strings(self) -> None:
        with pytest.raises(ScheduleAuthoringError) as caught:
            compile_schedule_plan(
                _args({"skill_key": "daily-report", "argv": ["ok", 3]}),
                context=_context(skills=(_skill(),)),
            )
        assert _fields(caught.value)[0]["field"] == "payload.argv"


class TestScheduleShape:
    def test_missing_timezone_asks_the_user(self) -> None:
        arguments = _args({"text": "开会"})
        arguments.pop("timezone")
        with pytest.raises(ScheduleAuthoringError) as caught:
            compile_schedule_plan(arguments, context=_context())
        assert caught.value.code == "SCHEDULE_TIMEZONE_REQUIRED"
        assert _fields(caught.value)[0]["field"] == "timezone"
        assert _fields(caught.value)[0]["repair"] == "ASK_USER_TIMEZONE"
        assert "ask the user" in caught.value.message

    def test_invalid_timezone_is_explicit(self) -> None:
        with pytest.raises(ScheduleAuthoringError) as caught:
            compile_schedule_plan(
                _args({"text": "开会"}, timezone="Mars/Olympus"), context=_context()
            )
        assert caught.value.code == "SCHEDULE_DRAFT_INVALID"
        assert _fields(caught.value)[0]["reason"] == "INVALID_TIMEZONE"

    def test_cron_must_have_five_fields(self) -> None:
        with pytest.raises(ScheduleAuthoringError) as caught:
            compile_schedule_plan(
                _args({"text": "开会"}, recurrence={"kind": "CRON", "cron": "0 9 * *"}),
                context=_context(),
            )
        assert _fields(caught.value)[0]["field"] == "recurrence.cron"
        assert _fields(caught.value)[0]["reason"] == "INVALID_CRON"

    def test_once_requires_an_iso_timestamp_with_offset(self) -> None:
        with pytest.raises(ScheduleAuthoringError) as caught:
            compile_schedule_plan(
                _args(
                    {"text": "开会"},
                    recurrence={"kind": "ONCE", "run_at": "2026-11-20T09:00:00"},
                ),
                context=_context(),
            )
        error = _fields(caught.value)[0]
        assert error["field"] == "recurrence.run_at"
        assert error["reason"] == "INVALID"

    def test_once_accepts_an_aware_timestamp(self) -> None:
        run_at = datetime(2026, 11, 20, 9, 0, tzinfo=UTC).isoformat()
        result = compile_schedule_plan(
            _args({"text": "开会"}, recurrence={"kind": "ONCE", "run_at": run_at}),
            context=_context(),
        )
        assert result.recurrence is not None
        assert result.recurrence["kind"] == "ONCE"

    def test_current_session_requires_a_conversation(self) -> None:
        with pytest.raises(ScheduleAuthoringError) as caught:
            compile_schedule_plan(
                _args({"text": "开会"}, session_target="current"), context=_context()
            )
        error = _fields(caught.value)[0]
        assert error["field"] == "session_target"
        assert error["reason"] == "SESSION_CONTEXT_UNAVAILABLE"


class TestDelivery:
    PUSH_ROUTE = DeliveryRouteInput(
        channel="WECOM",
        bot_id="bot-1",
        external_user_id="user-1",
        external_conversation_id="chat-1",
    )

    def test_webchat_defaults_to_no_delivery_intent(self) -> None:
        result = compile_schedule_plan(_args({"text": "开会"}), context=_context())
        assert str(result.plan.delivery.intent) == "NONE"

    def test_push_requires_the_source_chat_route(self) -> None:
        with pytest.raises(ScheduleAuthoringError) as caught:
            compile_schedule_plan(
                _args({"text": "开会"}, delivery={"intent": "PUSH"}), context=_context()
            )
        error = _fields(caught.value)[0]
        assert error["field"] == "delivery.route"
        assert error["reason"] == "DELIVERY_ROUTE_UNAVAILABLE"
        assert caught.value.code == "SCHEDULE_DELIVERY_UNSUPPORTED"

    def test_push_uses_the_authenticated_route(self) -> None:
        result = compile_schedule_plan(
            _args({"text": "开会"}, delivery={"intent": "PUSH"}),
            context=_context(entry_channel="WECOM", delivery_route=self.PUSH_ROUTE),
        )
        assert str(result.plan.delivery.intent) == "PUSH"
        assert result.plan.delivery.route == self.PUSH_ROUTE

    def test_model_supplied_route_must_match_the_source_chat(self) -> None:
        other = {
            "channel": "WECOM",
            "bot_id": "bot-2",
            "external_user_id": "user-2",
        }
        with pytest.raises(ScheduleAuthoringError) as caught:
            compile_schedule_plan(
                _args({"text": "开会"}, delivery={"intent": "PUSH", "route": other}),
                context=_context(entry_channel="WECOM", delivery_route=self.PUSH_ROUTE),
            )
        assert _fields(caught.value)[0]["reason"] == "ROUTE_NOT_OWNED"

    def test_none_forbids_a_route(self) -> None:
        with pytest.raises(ScheduleAuthoringError) as caught:
            compile_schedule_plan(
                _args(
                    {"text": "开会"},
                    delivery={"intent": "NONE", "route": self.PUSH_ROUTE.model_dump()},
                ),
                context=_context(),
            )
        assert _fields(caught.value)[0]["reason"] == "ROUTE_NOT_ALLOWED"

    def test_push_channel_must_be_outbound(self) -> None:
        local = DeliveryRouteInput(
            channel="WEBCHAT", bot_id="bot-1", external_user_id="user-1"
        )
        with pytest.raises(ScheduleAuthoringError) as caught:
            compile_schedule_plan(
                _args({"text": "开会"}, delivery={"intent": "PUSH", "route": local.model_dump()}),
                context=_context(entry_channel="WECOM", delivery_route=local),
            )
        error = _fields(caught.value)[0]
        assert error["reason"] == "DELIVERY_CHANNEL_UNSUPPORTED"
        assert error["allowed_values"] == ["WECOM"]


class TestToolsPolicy:
    def test_skill_tool_must_belong_to_an_effective_skill(self) -> None:
        with pytest.raises(ScheduleAuthoringError) as caught:
            compile_schedule_plan(
                _args(
                    {
                        "prompt": "汇总",
                        "tools_allow": [f"SKILL:{uuid.uuid4()}:report.render"],
                    }
                ),
                context=_context(skills=(_skill(),)),
            )
        error = _fields(caught.value)[0]
        assert error["reason"] == "SKILL_NOT_OWNED"
        assert error["allowed_values"] == ["daily-report"]

    def test_mcp_tool_must_come_from_an_owned_server(self) -> None:
        with pytest.raises(ScheduleAuthoringError) as caught:
            compile_schedule_plan(
                _args({"prompt": "汇总", "tools_allow": ["MCP:erp:customer.report"]}),
                context=_context(),
            )
        error = _fields(caught.value)[0]
        assert error["reason"] == "MCP_SERVER_NOT_OWNED"
        assert error["allowed_values"] == ["crm"]

    def test_mcp_tool_must_exist_in_the_owned_catalog(self) -> None:
        with pytest.raises(ScheduleAuthoringError) as caught:
            compile_schedule_plan(
                _args({"prompt": "汇总", "tools_allow": ["MCP:crm:customer.delete"]}),
                context=_context(),
            )
        error = _fields(caught.value)[0]
        assert error["reason"] == "TOOL_NOT_OWNED"
        assert error["allowed_values"] == ["customer.report"]

    def test_unknown_platform_tool_is_rejected(self) -> None:
        with pytest.raises(ScheduleAuthoringError) as caught:
            compile_schedule_plan(
                _args({"prompt": "汇总", "tools_allow": ["PLATFORM:rm_rf"]}),
                context=_context(),
            )
        assert _fields(caught.value)[0]["reason"] == "PLATFORM_TOOL_UNKNOWN"

    def test_unattended_turn_cannot_allow_asking_the_user(self) -> None:
        with pytest.raises(ScheduleAuthoringError) as caught:
            compile_schedule_plan(
                _args({"prompt": "汇总", "tools_allow": ["PLATFORM:ask_user"]}),
                context=_context(),
            )
        assert _fields(caught.value)[0]["reason"] == "PLATFORM_TOOL_UNKNOWN"

    def test_skill_key_is_compiled_to_a_uuid(self) -> None:
        result = compile_schedule_plan(
            _args({"prompt": "汇总", "tools_allow": ["SKILL:daily-report:report.render"]}),
            context=_context(skills=(_skill(),)),
        )
        assert isinstance(result.plan.payload, AgentTurnPayload)
        assert result.plan.payload.tools_allow == [f"SKILL:{SKILL_ID}:report.render"]

    def test_wildcard_cannot_be_mixed_with_explicit_tools(self) -> None:
        with pytest.raises(ScheduleAuthoringError) as caught:
            compile_schedule_plan(
                _args({"prompt": "汇总", "tools_allow": ["*", "PLATFORM:current_time"]}),
                context=_context(),
            )
        assert _fields(caught.value)[0]["reason"] == "WILDCARD_MIXED"


class TestConfidenceAndClarification:
    def test_low_confidence_returns_one_business_question(self) -> None:
        result = compile_schedule_plan(
            _args({"prompt": "汇总客户情况"}, confidence=0.2), context=_context()
        )
        assert result.clarification is not None
        assert result.clarification.question
        for internal in ("SKILL_PLAN", "AGENT_TURN", "REMINDER", "mode", "模式"):
            assert internal not in result.clarification.question

    def test_empty_payload_asks_instead_of_guessing(self) -> None:
        result = compile_schedule_plan(
            {
                "instruction": "每天给我看看客户情况",
                "timezone": TZ,
                "recurrence": {"kind": "CRON", "cron": "0 9 * * *"},
            },
            context=_context(),
        )
        assert result.plan is None
        assert result.clarification is not None
        assert "固定的" in result.clarification.question

    def test_confidence_must_be_between_zero_and_one(self) -> None:
        with pytest.raises(ScheduleAuthoringError) as caught:
            compile_schedule_plan(
                _args({"text": "开会"}, confidence=1.5), context=_context()
            )
        assert _fields(caught.value)[0]["field"] == "confidence"
        assert _fields(caught.value)[0]["reason"] == "OUT_OF_RANGE"

    def test_reason_is_a_card_fact_not_a_job_field(self) -> None:
        result = compile_schedule_plan(
            _args({"text": "开会"}, reason="用户要求每天固定提醒"),
            context=_context(),
        )
        assert result.reason == "用户要求每天固定提醒"
        assert "reason" not in result.plan.model_dump(mode="json")
