"""Plain-language hints for user-visible failure messages.

The runtime finalization renderer and the worker delivery templates both need
the same "what happened / what to do next" text for a tool or platform error
code.  This module is a pure presentation mapping: no I/O, no platform imports,
and no knowledge of a specific host.  The original code stays available to the
caller for the technical-details block.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

SKILL_NOT_EFFECTIVE = "SKILL_NOT_EFFECTIVE"


def skill_not_effective_message(key: str) -> str:
    return (
        f"Skill '{key}' is not available in this execution. It may be disabled, unbound, "
        "or unauthorized. Use the skills available in this execution or ask an administrator to verify."
    )


@dataclass(frozen=True, slots=True)
class FailureHint:
    """A localized, user-readable explanation of a failure code."""

    title: str
    next_step: str
    #: False when the code did not match a known family.  Callers use it to
    #: record a visible fallback instead of pretending the code was understood.
    known: bool = True
    action_owner: str | None = None


@dataclass(frozen=True, slots=True)
class _Hint:
    zh_title: str
    en_title: str
    zh_next: str
    en_next: str
    known: bool = True
    action_owner: str | None = None


def failure_hint(
    code: str | None,
    *,
    locale: str | None = "zh-CN",
    tool: str | None = None,
    detail: Mapping[str, object] | None = None,
) -> FailureHint:
    """Return a localized hint for ``code``; unknown codes get a generic one.

    The function never raises: failure rendering must not fail the run because
    a new tool introduced a code that this mapping does not know yet.
    """

    # Only an explicit English signal selects English; an unknown/empty locale
    # falls back to zh-CN, matching the project's default-locale policy.
    zh = not str(locale or "").lower().startswith("en")
    normalized = (code or "").strip().upper()
    special = _schedule_or_gate(normalized, detail or {}, zh)
    if special is not None:
        return special
    hint = (
        _common_hint(normalized)
        or _platform_hint(normalized)
        or _execution_hint(normalized, tool)
        or _run_hint(normalized)
    )
    if hint is not None:
        return _localize(hint, zh)
    name = (tool or "").strip()
    if zh:
        title = f"工具 {name} 执行失败。" if name else "执行失败。"
        next_step = "具体原因尚未确定。"
    else:
        title = f"Tool {name} failed." if name else "The run failed."
        next_step = "The specific cause is undetermined."
    return FailureHint(title=title, next_step=next_step, known=False)


def _localize(hint: _Hint, zh: bool) -> FailureHint:
    return FailureHint(
        title=hint.zh_title if zh else hint.en_title,
        next_step=hint.zh_next if zh else hint.en_next,
        known=hint.known,
        action_owner=hint.action_owner,
    )


def _common_hint(code: str) -> _Hint | None:
    if code in {"COMMON_VALIDATION_ERROR", "COMMON_BAD_REQUEST"}:
        return _Hint(
            zh_title="请求信息不完整或格式不正确。",
            en_title="The request is incomplete or malformed.",
            zh_next="请补充或修正后重试。",
            en_next="Complete or correct it, then retry.",
            action_owner="USER",
        )
    if code == "COMMON_NOT_FOUND":
        return _Hint(
            zh_title="目标不存在或当前不可见。",
            en_title="The target does not exist or is not visible.",
            zh_next="请确认目标后重试。",
            en_next="Check the target, then retry.",
        )
    if code == "COMMON_CONFLICT":
        return _Hint(
            zh_title="目标状态已经变化。",
            en_title="The target state has changed.",
            zh_next="请刷新后重试。",
            en_next="Refresh, then retry.",
            action_owner="USER",
        )
    if code == "COMMON_INTERNAL_ERROR":
        return _Hint(
            zh_title="系统内部错误。",
            en_title="Internal system error.",
            zh_next="请稍后重试；若持续失败，请联系管理员。",
            en_next="Retry later; if it persists, contact an administrator.",
            action_owner="ADMIN",
        )
    return None


def _platform_hint(code: str) -> _Hint | None:
    if code == "PLATFORM_AUTH_REJECTED":
        return _Hint(
            zh_title="平台登录失败，通常是凭据失效、签名不正确或账号无权限。",
            en_title=(
                "Platform sign-in failed, usually because the credential is invalid, "
                "the signature is incorrect, or the account lacks permission."
            ),
            zh_next="请联系管理员检查该平台凭据后重试。",
            en_next="Ask an administrator to check the platform credential, then retry.",
            action_owner="ADMIN",
        )
    if code == "PLATFORM_ADAPTER_NOT_FOUND":
        return _Hint(
            zh_title="平台适配器不存在或未启用。",
            en_title="The platform adapter is missing or disabled.",
            zh_next="请联系管理员检查平台接入配置。",
            en_next="Ask an administrator to check the platform integration.",
            action_owner="ADMIN",
        )
    if code == "PLATFORM_SESSION_REQUIRED":
        return _Hint(
            zh_title="平台登录态缺失，当前无法访问该平台。",
            en_title="The platform session is missing, so the platform cannot be accessed.",
            zh_next="请先完成平台登录或授权后重试。",
            en_next="Sign in or authorize the platform, then retry.",
            action_owner="USER",
        )
    if code == "CREDENTIAL_MISSING":
        return _Hint(
            zh_title="平台凭据未配置。",
            en_title="The platform credential is not configured.",
            zh_next="请联系管理员在项目平台补充凭据。",
            en_next="Ask an administrator to add the platform credential.",
            action_owner="ADMIN",
        )
    if code == "PLATFORM_SETTINGS_UNAVAILABLE":
        return _Hint(
            zh_title="平台设置暂时不可读。",
            en_title="Platform settings are temporarily unavailable.",
            zh_next="请稍后重试；若持续失败，请联系管理员。",
            en_next="Retry later; if it persists, contact an administrator.",
            action_owner="ADMIN",
        )
    if code == "PLATFORM_BUSINESS_ERROR":
        return _Hint(
            zh_title="平台返回业务错误。",
            en_title="The platform returned a business error.",
            zh_next="请核对业务参数或平台返回的业务码后重试。",
            en_next="Check the business arguments or the platform business code, then retry.",
            action_owner="USER",
        )
    if code.startswith("CONTROL_") or code.startswith("CREDENTIAL_"):
        return _Hint(
            zh_title="平台控制面或凭据校验拒绝了本次请求。",
            en_title="The platform control plane or credential check rejected the request.",
            zh_next="请联系管理员核对平台配置与凭据。",
            en_next="Ask an administrator to check the platform configuration and credential.",
            action_owner="ADMIN",
        )
    return None


def _execution_hint(code: str, tool: str | None) -> _Hint | None:
    if code == SKILL_NOT_EFFECTIVE:
        return _Hint(
            zh_title="所需技能在本次运行中不可用，可能已停用、未绑定或未授权。",
            en_title="The required skill is unavailable. It may be disabled, unbound, or unauthorized.",
            zh_next="请联系管理员检查技能状态、绑定和授权。",
            en_next="Ask an administrator to check the skill status, bindings, and access.",
        )
    if code.startswith("MODEL_"):
        return _Hint(
            zh_title="模型服务不可用或调用失败。",
            en_title="The model service is unavailable or the call failed.",
            zh_next="请稍后重试；若持续失败，请联系管理员检查模型配置。",
            en_next=(
                "Retry later; if it persists, ask an administrator to check the "
                "model configuration."
            ),
        )
    if code.startswith("SANDBOX_") or code.startswith("WORKSPACE_"):
        return _Hint(
            zh_title="执行环境不可用、超时或超出限制。",
            en_title="The execution environment is unavailable, timed out, or exceeded a limit.",
            zh_next="请缩小任务范围或稍后重试。",
            en_next="Narrow the task or retry later.",
        )
    if code.startswith(("TOOL_", "SKILL_", "MCP_")):
        name = (tool or "").strip()
        return _Hint(
            zh_title=f"工具 {name} 执行失败。" if name else "工具执行失败。",
            en_title=f"Tool {name} failed." if name else "A tool failed.",
            zh_next="请根据技术信息修正输入；若持续失败，请把技术信息提供给管理员。",
            en_next=(
                "Fix the input using the technical details; if it persists, "
                "provide them to an administrator."
            ),
        )
    return None


def _run_hint(code: str) -> _Hint | None:
    if code == "RUN_BUDGET_EXCEEDED":
        return _Hint(
            zh_title="本轮执行达到轮次或工具调用上限，已停止。",
            en_title="The run reached its turn or tool-call limit and stopped.",
            zh_next="请拆分任务或减少排查轮次后重试。",
            en_next="Split the task or reduce diagnosis turns, then retry.",
        )
    if code == "RUN_DEADLINE_EXCEEDED":
        return _Hint(
            zh_title="本轮执行超过期限，已停止。",
            en_title="The run exceeded its deadline and stopped.",
            zh_next="请缩小范围或延长期限后重试。",
            en_next="Narrow the task or extend the deadline, then retry.",
        )
    if code == "RUN_EVENT_PERSISTENCE_FAILED":
        return _Hint(
            zh_title="无法保存本轮执行记录，已停止。",
            en_title="The run stopped because its execution record could not be saved.",
            zh_next="请稍后重试；如涉及写入，请先核对是否已生效。",
            en_next="Retry later. Check whether any changes took effect before retrying.",
        )
    if code == "RUN_ABANDONED":
        return _Hint(
            zh_title="本轮与执行服务失去连接，已停止。",
            en_title="The run lost its execution service and stopped.",
            zh_next="可在当前对话继续；如涉及写入，请先核对是否已生效。",
            en_next="Continue in this chat. Check whether any changes took effect before retrying.",
        )
    if code == "RUN_NO_PROGRESS":
        return _Hint(
            zh_title="执行失败且无法继续，已停止。",
            en_title="The run failed and could not continue, so it stopped.",
            zh_next="请根据技术信息修正后重试。",
            en_next="Fix the cause using the technical details, then retry.",
        )
    if code == "RUN_TOOL_FAILURE_UNRESOLVED":
        return _Hint(
            zh_title="工具失败尚未解决，已停止继续执行。",
            en_title="The tool failure is unresolved, so the run stopped.",
            zh_next="请先处理该失败，或向用户确认下一步。",
            en_next="Resolve the failure or ask the user for the next step.",
            action_owner="NONE",
        )
    if code == "PROMOTION_SOURCE_INVALID":
        return _Hint(
            zh_title="这条运行记录不能提升为定时任务。",
            en_title="This run record cannot be promoted to a schedule.",
            zh_next="请选择一次成功的 Skill 执行记录。",
            en_next="Choose a successful skill execution record.",
            action_owner="USER",
        )
    if code == "RUN_NOT_COMPLETED":
        return _Hint(
            zh_title="只有已完成的运行才能提升为定时任务。",
            en_title="Only a completed run can be promoted to a schedule.",
            zh_next="请选择已完成的运行。",
            en_next="Choose a completed run.",
            action_owner="USER",
        )
    if code == "RUN_SKILL_CALL_MISSING":
        return _Hint(
            zh_title="这条运行没有可提升的成功 Skill 调用。",
            en_title="This run has no successful skill call to promote.",
            zh_next="请选择包含成功 Skill 调用的运行。",
            en_next="Choose a run that contains a successful skill call.",
            action_owner="USER",
        )
    if code in {"RUN_CAPACITY_EXCEEDED", "RUN_QUEUE_TIMEOUT"}:
        return _Hint(
            zh_title="当前执行容量不足或排队超时。",
            en_title="Execution capacity is unavailable or the queue timed out.",
            zh_next="请稍后重试。",
            en_next="Retry later.",
        )
    if code.startswith("TASK_"):
        return _Hint(
            zh_title="后台任务超时、预算耗尽或失败无法继续。",
            en_title="The background task timed out, exhausted its budget, or could not continue.",
            zh_next="请缩小任务范围或稍后重试。",
            en_next="Narrow the task or retry later.",
        )
    return None


#: Exact schedule hints: (zh_title, en_title, zh_next, en_next, action_owner).
_SCHEDULE_HINTS: dict[str, tuple[str, str, str, str, str | None]] = {
    "SCHEDULE_DRAFT_INVALID": (
        "定时任务信息不合法。",
        "The schedule information is invalid.",
        "请按提示修正后重试。",
        "Correct the information and try again.",
        "USER",
    ),
    "SCHEDULE_NO_FUTURE_OCCURRENCE": (
        "触发时间已经过去。",
        "The trigger time has passed.",
        "请重新选择未来的时间。",
        "Choose a future time.",
        "USER",
    ),
    "SCHEDULE_DEPENDENCY_MISSING": (
        "定时任务依赖的技能或工具不可用。",
        "A skill or tool required by the schedule is unavailable.",
        "请联系管理员检查依赖配置。",
        "Ask an administrator to check the dependency.",
        "ADMIN",
    ),
    "SCHEDULE_ARGV_INVALID": (
        "技能命令行参数不符合要求。",
        "The skill command-line arguments are invalid.",
        "请按技能声明的参数格式修正后重试。",
        "Correct them to match the skill contract and try again.",
        "USER",
    ),
    "SCHEDULE_INPUT_INVALID": (
        "技能输入参数不符合要求。",
        "The skill input is invalid.",
        "请按技能声明的参数格式修正后重试。",
        "Correct it to match the skill contract and try again.",
        "USER",
    ),
    "SCHEDULE_GRANT_DENIED": (
        "没有使用该 Agent 或工具的授权。",
        "The agent or tool is not authorized.",
        "请联系管理员恢复授权。",
        "Ask an administrator to restore access.",
        "ADMIN",
    ),
    "SCHEDULE_DRAFT_EXPIRED": (
        "定时任务草稿已过期或已使用。",
        "The schedule draft expired or was already used.",
        "请重新创建草稿。",
        "Create the draft again.",
        "USER",
    ),
    "SCHEDULE_CONFIRMATION_REQUIRED": (
        "这条草稿需要你本人确认。",
        "This draft must be confirmed by you.",
        "请在聊天中确认后再继续。",
        "Confirm it in the chat to continue.",
        "USER",
    ),
    "SCHEDULE_CONFIRMATION_REPLAY": (
        "这次确认已经处理过。",
        "This confirmation was already processed.",
        "请刷新后查看任务状态。",
        "Refresh to see the schedule state.",
        "USER",
    ),
    "SCHEDULE_VERSION_CONFLICT": (
        "任务配置已经变化。",
        "The schedule configuration changed.",
        "请刷新后重试。",
        "Refresh and try again.",
        "USER",
    ),
    "SCHEDULE_NOT_FOUND": (
        "定时任务不存在或当前不可见。",
        "The schedule does not exist or is not visible.",
        "请确认任务后重试。",
        "Check the schedule and try again.",
        "USER",
    ),
    "SCHEDULE_STATE_INVALID": (
        "当前状态不允许这个操作。",
        "The current state does not allow this action.",
        "请查看任务状态后重试。",
        "Review the schedule state and try again.",
        "USER",
    ),
    "SCHEDULE_AMBIGUOUS": (
        "匹配到多个定时任务。",
        "Several schedules match.",
        "请说明是哪一个。",
        "Say which one you mean.",
        "USER",
    ),
    "SCHEDULE_OCCURRENCE_DUPLICATE": (
        "这个触发时间已经存在。",
        "This occurrence already exists.",
        "请选择其他时间。",
        "Choose a different time.",
        "USER",
    ),
    "SCHEDULE_ADMISSION_FAILED": (
        "定时任务在触发时未通过校验，任务没有执行。",
        "The schedule failed admission and did not run.",
        "请稍后重试；若持续失败，请联系管理员。",
        "Retry later; if it persists, contact an administrator.",
        None,
    ),
    "SCHEDULE_QUOTA_EXCEEDED": (
        "已达到定时任务数量或触发频率限制。",
        "The schedule quota or minimum interval limit was reached.",
        "请减少任务或调整触发间隔。",
        "Reduce schedules or increase the interval.",
        "USER",
    ),
    "SCHEDULE_DELIVERY_UNSUPPORTED": (
        "当前入口不支持这种主动投递。",
        "This surface does not support that push delivery.",
        "请调整投递方式后重试。",
        "Change the delivery route and try again.",
        "USER",
    ),
    "SCHEDULE_TOOL_WILDCARD_UNSUPPORTED": (
        "该技能不支持工具通配。",
        "This skill does not support tool wildcards.",
        "请指定具体工具后重试。",
        "Name the tool explicitly and try again.",
        "USER",
    ),
}


def _schedule_or_gate(code: str, detail: Mapping[str, object], zh: bool) -> FailureHint | None:
    """Schedule and retry-gate hints.

    The first rule group reads only verified `reason`/`repair` facts; when the
    facts are absent it must not guess the cause or the repair action.
    """
    if code == "TOOL_RETRY_UNSAFE":
        if detail.get("gate_reason") == "UNVERIFIED_WRITE":
            return FailureHint(
                "上一次操作的结果尚未核对。" if zh else "The earlier operation has not been verified.",
                "请先核对上次操作是否已经生效。" if zh
                else "Verify whether the earlier operation took effect.",
            )
        if detail.get("gate_reason") == "FAILURE_UNRESOLVED":
            return FailureHint(
                "上一次失败尚未解决。" if zh else "The earlier failure is still unresolved.",
                "请先处理该失败，或向用户确认下一步；不要改用其他执行方式。" if zh
                else "Resolve it or ask the user first; do not switch to another execution path.",
                action_owner="NONE",
            )
        return FailureHint(
            "当前操作不能原样重做。" if zh else "This operation cannot be repeated as submitted.",
            "重试已受保护；请先查看上一次调用的结果。" if zh
            else "The retry is guarded; review the previous result first.",
        )
    if not code.startswith("SCHEDULE_"):
        return None
    if code == "SCHEDULE_INVOCATION_CONTRACT_MISSING":
        reason = detail.get("reason")
        if reason in {"CONTRACT_MISSING", "CONTRACT_INVALID"}:
            missing = reason == "CONTRACT_MISSING"
            title = "技能缺少定时执行配置。" if missing else "技能的定时执行配置无效。"
            en_title = (
                "The skill lacks scheduling configuration."
                if missing
                else "The skill scheduling configuration is invalid."
            )
            if detail.get("repair") == "REIMPORT_SKILL":
                return FailureHint(
                    title if zh else en_title,
                    "这是平台侧配置问题。管理员重新导入技能后可再试。" if zh
                    else "An administrator can reimport the skill, after which you can try again.",
                    action_owner="ADMIN",
                )
            return FailureHint(
                title if zh else en_title,
                "这是平台侧配置问题；具体修复动作尚未确定。" if zh
                else "This is a platform configuration issue; the repair action is undetermined.",
                action_owner="ADMIN",
            )
        return FailureHint(
            "定时任务预检未通过。" if zh else "The schedule preflight did not pass.",
            "具体配置原因尚未确定。" if zh else "The specific configuration cause is undetermined.",
        )
    if code == "SCHEDULE_TIMEZONE_REQUIRED":
        return FailureHint(
            "定时任务缺少时区。" if zh else "The schedule needs a time zone.",
            "请提供定时任务的时区。" if zh else "Provide the schedule time zone.",
            action_owner="USER",
        )
    if code == "SCHEDULE_PARAMETER_INCOMPLETE":
        return FailureHint(
            "定时任务缺少必填信息。" if zh else "The schedule is missing required information.",
            "请补充缺失的任务信息。" if zh else "Provide the missing schedule information.",
            action_owner="USER",
        )
    hint = _SCHEDULE_HINTS.get(code)
    if hint is not None:
        zh_title, en_title, zh_next, en_next, owner = hint
        return FailureHint(
            zh_title if zh else en_title,
            zh_next if zh else en_next,
            action_owner=owner,
        )
    return FailureHint(
        "定时任务操作未完成。" if zh else "The scheduling operation did not complete.",
        "具体原因尚未确定。" if zh else "The specific cause is undetermined.",
    )
