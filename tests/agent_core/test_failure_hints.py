"""Plain-language failure hints for tool/platform codes."""

from __future__ import annotations

from muad_agent_core.failures import failure_hint


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
