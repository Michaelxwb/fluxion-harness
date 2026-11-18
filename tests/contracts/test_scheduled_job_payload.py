"""Job/Payload 模型（run-failure-reporting §10.2 / §10.3 / §10.8 / §10.12）。

钉住四件事：

- payload 是唯一的执行选择：REMINDER / SKILL_PLAN / AGENT_TURN，`mode` 只由服务端派生；
- SKILL_PLAN 的 `input` 只走 stdin、`argv` 追加到脚本；
- session 目标决定上下文，delivery 决定结果去哪，两者不互相推导；
- 开发期旧模式迁移只映射 kind，不保留 `execution_spec` 双读。
"""

from __future__ import annotations

import uuid

import pytest
from muad_contracts import (
    PAYLOAD_MODE,
    AgentTurnPayload,
    DeliveryPlan,
    DraftKind,
    PayloadKind,
    ReminderPayload,
    ScheduleDraftRequest,
    ScheduleJob,
    ScheduleMode,
    SchedulePlan,
    ScheduleWindow,
    SessionTarget,
    SkillPlanPayload,
    ToolPolicy,
    derive_schedule_mode,
    parse_schedule_payload,
)
from muad_contracts.enums import DeliveryIntent
from pydantic import ValidationError

pytestmark = pytest.mark.contract

SESSION_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")


def _job(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "instruction": "每天 9 点发周报",
        "recurrence": {"kind": "CRON", "cron": "0 9 * * *", "timezone": "Asia/Shanghai"},
        "payload": {"kind": "REMINDER", "text": "开会"},
    }
    payload.update(overrides)
    return payload


class TestPayloadShapes:
    def test_reminder_is_text_only(self) -> None:
        assert ReminderPayload(text="开会").kind == PayloadKind.REMINDER
        with pytest.raises(ValidationError):
            ReminderPayload(text="")

    def test_skill_plan_separates_stdin_input_from_argv(self) -> None:
        payload = SkillPlanPayload(
            skill_id=uuid.uuid4(),
            input={"company_id": "22921947"},
            argv=["--company-id", "22921947", "--date", "{{previous_day}}"],
            window=ScheduleWindow(kind="DAY", offset=-1),
        )
        dumped = payload.model_dump(mode="json")
        assert dumped["input"] == {"company_id": "22921947"}
        assert dumped["argv"] == ["--company-id", "22921947", "--date", "{{previous_day}}"]
        assert dumped["window"] == {"kind": "DAY", "offset": -1}
        assert "argv" not in dumped["input"], "argv 不能混进 stdin"

    def test_skill_plan_rejects_invalid_argv(self) -> None:
        with pytest.raises(ValidationError):
            SkillPlanPayload(skill_id=uuid.uuid4(), argv=["ok", 3])
        with pytest.raises(ValidationError):
            SkillPlanPayload(skill_id=uuid.uuid4(), argv=["bad\x00arg"])

    def test_agent_turn_carries_prompt_policy_and_budget_reference(self) -> None:
        payload = AgentTurnPayload(
            prompt="汇总 {{previous_day}} 的执行",
            tools_allow=[f"SKILL:{uuid.uuid4()}:report.render", "PLATFORM:current_time"],
        )
        assert payload.budget_ref == "platform"
        assert payload.model_ref is None
        assert [entry.tool_name for entry in payload.allowlist_entries()] == ["report.render"]

    def test_agent_turn_empty_tools_follow_the_owner(self) -> None:
        plan = SchedulePlan(payload={"kind": "AGENT_TURN", "prompt": "检查一次"})
        assert plan.resolved_tools().follows_owner
        assert plan.resolved_tools().tools_allow == ["*"]

    def test_non_model_payloads_have_no_tool_surface(self) -> None:
        plan = SchedulePlan(payload={"kind": "SKILL_PLAN", "skill_id": uuid.uuid4()})
        assert plan.resolved_tools().tools_allow == []

    def test_payload_kind_is_derived_not_selected(self) -> None:
        plan = SchedulePlan(payload={"kind": "REMINDER", "text": "x"})
        assert plan.derived_mode is ScheduleMode.REMINDER
        assert derive_schedule_mode(AgentTurnPayload(prompt="x")) is ScheduleMode.AGENTIC
        assert PAYLOAD_MODE[PayloadKind.SKILL_PLAN] is ScheduleMode.SKILL

    def test_unknown_payload_kind_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            parse_schedule_payload({"kind": "SKILL", "skill_id": str(uuid.uuid4())})


class TestSessionAndDeliverySeparation:
    def test_session_target_forms(self) -> None:
        assert str(SessionTarget("isolated")) == "isolated"
        assert SessionTarget("current").kind == "current"
        assert SessionTarget("main").kind == "main"
        named = SessionTarget(f"session:{SESSION_ID}")
        assert named.kind == "session" and named.session_id == SESSION_ID
        assert SchedulePlan(payload={"kind": "REMINDER", "text": "x"}).model_dump(
            mode="json"
        )["session_target"] == "isolated"

    def test_session_target_rejects_unknown_shapes(self) -> None:
        for value in ("", "persistent", "session:", "session:not-a-uuid"):
            with pytest.raises(ValidationError):
                SessionTarget(value)

    def test_delivery_only_has_none_or_push(self) -> None:
        assert {member.value for member in DeliveryIntent} == {"NONE", "PUSH"}
        assert DeliveryPlan().intent is DeliveryIntent.NONE
        assert DeliveryPlan(intent="NONE").route is None

    def test_push_requires_a_route_and_none_forbids_one(self) -> None:
        route = {"channel": "WECOM", "bot_id": "bot-1", "external_user_id": "user-1"}
        assert DeliveryPlan(intent="PUSH", route=route).route is not None
        with pytest.raises(ValidationError):
            DeliveryPlan(intent="PUSH")
        with pytest.raises(ValidationError):
            DeliveryPlan(intent="NONE", route=route)

    def test_web_pull_is_not_a_delivery_intent(self) -> None:
        with pytest.raises(ValidationError):
            DeliveryPlan(intent="PULL")

    def test_session_does_not_follow_delivery(self) -> None:
        plan = SchedulePlan(
            payload={"kind": "REMINDER", "text": "x"},
            session_target="session:" + str(SESSION_ID),
            delivery={"intent": "NONE"},
        )
        assert plan.delivery.intent is DeliveryIntent.NONE
        assert plan.session_target.kind == "session"


class TestJobConfig:
    def test_job_is_the_authoritative_config_shape(self) -> None:
        job = ScheduleJob(**_job())
        assert job.derived_mode is ScheduleMode.REMINDER
        dumped = job.model_dump(mode="json")
        assert "mode" not in dumped
        assert "execution_spec" not in dumped
        assert dumped["payload"] == {"kind": "REMINDER", "text": "开会"}

    def test_draft_request_rejects_the_old_execution_spec(self) -> None:
        with pytest.raises(ValidationError):
            ScheduleDraftRequest(
                **_job(execution_spec={"mode": "REMINDER", "reminder_text": "开会"})
            )

    def test_draft_request_rejects_user_selected_mode(self) -> None:
        with pytest.raises(ValidationError):
            ScheduleDraftRequest(**_job(mode="REMINDER"))

    def test_update_identity_is_still_cas_scoped(self) -> None:
        with pytest.raises(ValidationError):
            ScheduleDraftRequest(
                **_job(kind=DraftKind.UPDATE, base_schedule_id=uuid.uuid4())
            )
        request = ScheduleDraftRequest(
            **_job(
                kind=DraftKind.UPDATE,
                base_schedule_id=uuid.uuid4(),
                base_config_version=2,
            )
        )
        assert request.base_config_version == 2

    def test_tool_policy_defaults_to_owner_and_rejects_wildcard_mix(self) -> None:
        assert ToolPolicy().follows_owner
        assert ToolPolicy(tools_allow=["PLATFORM:current_time"]).follows_owner is False
        with pytest.raises(ValidationError):
            ToolPolicy(tools_allow=["*", "PLATFORM:current_time"])


class TestLegacyModeMigration:
    """开发期直接迁移的映射事实：旧 mode 只决定 payload kind，不存在第二份配置。"""

    @pytest.mark.parametrize(
        ("legacy_mode", "payload", "expected_kind"),
        [
            ("REMINDER", {"kind": "REMINDER", "text": "开会"}, PayloadKind.REMINDER),
            (
                "SKILL",
                {
                    "kind": "SKILL_PLAN",
                    "skill_id": str(SESSION_ID),
                    "input": {"day": "{{previous_day}}"},
                    "argv": [],
                    "window": {"kind": "DAY", "offset": 0},
                },
                PayloadKind.SKILL_PLAN,
            ),
            (
                "AGENTIC",
                {
                    "kind": "AGENT_TURN",
                    "prompt": "检查一次",
                    "tools_allow": [],
                    "budget_ref": "platform",
                },
                PayloadKind.AGENT_TURN,
            ),
        ],
    )
    def test_legacy_mode_maps_to_one_payload_kind(
        self, legacy_mode: str, payload: dict[str, object], expected_kind: PayloadKind
    ) -> None:
        parsed = parse_schedule_payload(payload)
        assert parsed.kind == expected_kind
        assert derive_schedule_mode(parsed).value == legacy_mode
        assert "execution_spec" not in payload

    def test_old_mode_names_are_not_payload_kinds(self) -> None:
        for legacy in ("SKILL", "AGENTIC"):
            with pytest.raises(ValidationError):
                parse_schedule_payload({"kind": legacy})
