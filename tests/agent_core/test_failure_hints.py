"""Plain-language failure hints for tool/platform codes."""

from __future__ import annotations

import pytest
from muad_agent_core.failures import failure_hint
from muad_api.error_codes import ErrorCode


def test_platform_auth_is_actionable_in_both_locales() -> None:
    zh = failure_hint("PLATFORM_AUTH_REJECTED", locale="zh-CN", tool="execute")
    en = failure_hint("PLATFORM_AUTH_REJECTED", locale="en-US", tool="execute")

    assert zh.known and en.known
    assert "平台登录失败" in zh.title
    assert "凭据" in zh.next_step
    assert "Platform sign-in failed" in en.title
    assert "credential" in en.next_step


def test_platform_configuration_codes_are_distinct() -> None:
    session = failure_hint("PLATFORM_SESSION_REQUIRED", locale="zh-CN")
    credential = failure_hint("CREDENTIAL_MISSING", locale="zh-CN")
    settings = failure_hint("PLATFORM_SETTINGS_UNAVAILABLE", locale="zh-CN")
    adapter = failure_hint("PLATFORM_ADAPTER_NOT_FOUND", locale="zh-CN")

    assert "登录态" in session.title
    assert "凭据未配置" in credential.title
    assert "设置" in settings.title
    assert "适配器" in adapter.title
    assert all(item.known for item in (session, credential, settings, adapter))


def test_platform_business_and_control_codes() -> None:
    business = failure_hint("PLATFORM_BUSINESS_ERROR", locale="en-US")
    control = failure_hint("CONTROL_REJECTED", locale="zh-CN")
    credential = failure_hint("CREDENTIAL_REVOKED", locale="zh-CN")

    assert "business error" in business.title
    assert "控制面" in control.title
    assert "凭据" in credential.title


def test_execution_environment_and_tool_codes() -> None:
    model = failure_hint("MODEL_UNAVAILABLE", locale="zh-CN")
    sandbox = failure_hint("SANDBOX_TIMEOUT", locale="en-US")
    workspace = failure_hint("WORKSPACE_QUOTA_EXCEEDED", locale="zh-CN")
    tool = failure_hint("TOOL_FAILED", locale="zh-CN", tool="execute")
    skill = failure_hint("SKILL_RESULT_INVALID", locale="en-US")
    mcp = failure_hint("MCP_DISCOVERY_FAILED", locale="zh-CN")

    assert "模型" in model.title
    assert "execution environment" in sandbox.title
    assert "执行环境" in workspace.title
    assert "execute" in tool.title
    assert "tool" in skill.title.lower()
    assert "工具" in mcp.title


def test_run_and_task_codes_have_next_steps() -> None:
    budget = failure_hint("RUN_BUDGET_EXCEEDED", locale="zh-CN")
    deadline = failure_hint("RUN_DEADLINE_EXCEEDED", locale="en-US")
    no_progress = failure_hint("RUN_NO_PROGRESS", locale="zh-CN")
    capacity = failure_hint("RUN_CAPACITY_EXCEEDED", locale="zh-CN")
    task = failure_hint("TASK_DEADLINE_EXCEEDED", locale="en-US")

    assert "轮次" in budget.title and budget.next_step
    assert "deadline" in deadline.title and deadline.next_step
    assert "无法继续" in no_progress.title and no_progress.next_step
    assert "容量" in capacity.title and capacity.next_step
    assert "background task" in task.title.lower() and task.next_step


def test_unknown_code_gets_generic_hint_and_is_marked_unknown() -> None:
    hint = failure_hint("SOME_NEW_TOOL_ERROR", locale="zh-CN", tool="execute")

    assert hint.known is False
    assert "execute" in hint.title
    assert "SOME_NEW_TOOL_ERROR" not in hint.title
    assert hint.next_step


def test_missing_or_unknown_inputs_fall_back_without_raising() -> None:
    for code in (None, "", "   "):
        hint = failure_hint(code, locale="zh-CN")
        assert hint.known is False
        assert hint.title

    assert "平台登录失败" in failure_hint("platform_auth_rejected", locale="zh-CN").title
    # Unknown locale falls back to zh-CN instead of inventing a language.
    assert "平台登录失败" in failure_hint("PLATFORM_AUTH_REJECTED", locale="fr-FR").title
    assert failure_hint("PLATFORM_AUTH_REJECTED", locale=None) == failure_hint("PLATFORM_AUTH_REJECTED")


def test_hint_is_deterministic() -> None:
    first = failure_hint("PLATFORM_AUTH_REJECTED", locale="en-US", tool="execute")
    second = failure_hint("PLATFORM_AUTH_REJECTED", locale="en-US", tool="execute")

    assert first == second


@pytest.mark.parametrize(
    ("reason", "zh_fragment", "en_fragment"),
    [
        ("CONTRACT_MISSING", "缺少定时执行配置", "lacks scheduling configuration"),
        ("CONTRACT_INVALID", "定时执行配置无效", "scheduling configuration is invalid"),
    ],
)
def test_schedule_contract_errors_are_distinct_admin_actions(
    reason: str, zh_fragment: str, en_fragment: str
) -> None:
    detail = {"field": "payload.skill_id", "reason": reason, "repair": "REIMPORT_SKILL"}

    zh = failure_hint(
        "SCHEDULE_INVOCATION_CONTRACT_MISSING", locale="zh-CN", detail=detail
    )
    en = failure_hint(
        "SCHEDULE_INVOCATION_CONTRACT_MISSING", locale="en-US", detail=detail
    )

    assert zh.known and en.known
    assert zh_fragment in zh.title
    assert en_fragment in en.title
    assert "REIMPORT_SKILL" not in zh.next_step
    assert "管理员重新导入" in zh.next_step
    assert "administrator can reimport" in en.next_step
    assert zh.action_owner == en.action_owner == "ADMIN"


def test_schedule_contract_without_detail_does_not_guess_or_pick_a_repair() -> None:
    zh = failure_hint("SCHEDULE_INVOCATION_CONTRACT_MISSING", locale="zh-CN")
    en = failure_hint("SCHEDULE_INVOCATION_CONTRACT_MISSING", locale="en-US")

    assert zh.known and en.known
    assert "缺少" not in zh.title and "无效" not in zh.title
    assert "missing" not in en.title.lower() and "invalid" not in en.title.lower()
    assert "REIMPORT" not in zh.next_step and "reimport" not in en.next_step.lower()
    assert zh.action_owner is None and en.action_owner is None


def test_schedule_contract_reason_without_repair_does_not_invent_the_action() -> None:
    zh = failure_hint(
        "SCHEDULE_INVOCATION_CONTRACT_MISSING",
        locale="zh-CN",
        detail={"reason": "CONTRACT_MISSING"},
    )
    en = failure_hint(
        "SCHEDULE_INVOCATION_CONTRACT_MISSING",
        locale="en-US",
        detail={"reason": "CONTRACT_MISSING"},
    )

    assert "缺少定时执行配置" in zh.title
    assert "lacks scheduling configuration" in en.title
    assert "重新导入" not in zh.next_step
    assert "reimport" not in en.next_step.lower()
    assert zh.action_owner == en.action_owner == "ADMIN"


@pytest.mark.parametrize(
    ("detail", "zh_fragment", "en_fragment"),
    [
        ({}, "原样重做", "repeated as submitted"),
        ({"gate_reason": "UNVERIFIED_WRITE"}, "尚未核对", "not been verified"),
    ],
)
def test_retry_guard_points_at_the_previous_result_not_the_input(
    detail: dict[str, object], zh_fragment: str, en_fragment: str
) -> None:
    zh = failure_hint("TOOL_RETRY_UNSAFE", locale="zh-CN", tool="execute", detail=detail)
    en = failure_hint("TOOL_RETRY_UNSAFE", locale="en-US", tool="execute", detail=detail)

    assert zh.known and en.known
    assert zh_fragment in zh.title
    assert en_fragment in en.title
    assert "修正输入" not in zh.next_step
    assert "fix the input" not in en.next_step.lower()
    assert "核对" in zh.next_step or "结果" in zh.next_step
    assert "verify" in en.next_step.lower() or "review" in en.next_step.lower()


def test_unresolved_failure_gate_has_a_distinct_action() -> None:
    zh = failure_hint(
        "TOOL_RETRY_UNSAFE", locale="zh-CN", detail={"gate_reason": "FAILURE_UNRESOLVED"}
    )
    en = failure_hint(
        "TOOL_RETRY_UNSAFE", locale="en-US", detail={"gate_reason": "FAILURE_UNRESOLVED"}
    )

    assert "尚未解决" in zh.title
    assert "unresolved" in en.title.lower()
    assert "其他执行方式" in zh.next_step
    assert "another execution path" in en.next_step
    assert zh.action_owner == en.action_owner == "NONE"


def test_schedule_field_errors_are_user_owned() -> None:
    timezone = failure_hint("SCHEDULE_TIMEZONE_REQUIRED", locale="zh-CN")
    incomplete = failure_hint("SCHEDULE_PARAMETER_INCOMPLETE", locale="en-US")

    assert timezone.action_owner == "USER"
    assert "时区" in timezone.title
    assert incomplete.action_owner == "USER"
    assert "required information" in incomplete.title


TOOL_FACING_CODES = frozenset(
    {
        ErrorCode.COMMON_VALIDATION_ERROR.value,
        ErrorCode.COMMON_BAD_REQUEST.value,
        ErrorCode.COMMON_NOT_FOUND.value,
        ErrorCode.COMMON_CONFLICT.value,
        ErrorCode.COMMON_INTERNAL_ERROR.value,
        ErrorCode.PLATFORM_SETTINGS_UNAVAILABLE.value,
        ErrorCode.RUN_TOOL_FAILURE_UNRESOLVED.value,
        "PROMOTION_SOURCE_INVALID",
        "RUN_NOT_COMPLETED",
        "RUN_SKILL_CALL_MISSING",
        "TOOL_RETRY_UNSAFE",
        "TOOL_NOT_FOUND",
        "TOOL_FAILED",
        "SKILL_NOT_EFFECTIVE",
    }
) | frozenset(
    code.value
    for code in ErrorCode
    if code.value.startswith(("SCHEDULE_", "TOOL_"))
)


@pytest.mark.parametrize("locale", ["zh-CN", "en-US"])
def test_every_tool_facing_code_has_a_semantic_hint_in_both_locales(locale: str) -> None:
    missing = [
        code
        for code in sorted(TOOL_FACING_CODES)
        if not failure_hint(code, locale=locale, tool="draft_schedule").known
    ]

    assert not missing, f"codes without a hint: {missing}"
