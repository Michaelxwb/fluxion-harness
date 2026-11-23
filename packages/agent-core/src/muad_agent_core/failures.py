"""Plain-language hints for user-visible failure messages.

The runtime finalization renderer and the worker delivery templates both need
the same "what happened / what to do next" text for a tool or platform error
code.  This module is a pure presentation mapping: no I/O, no platform imports,
and no knowledge of a specific host.  The original code stays available to the
caller for the technical-details block.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FailureHint:
    """A localized, user-readable explanation of a failure code."""

    title: str
    next_step: str
    #: False when the code did not match a known family.  Callers use it to
    #: record a visible fallback instead of pretending the code was understood.
    known: bool = True


@dataclass(frozen=True, slots=True)
class _Hint:
    zh_title: str
    en_title: str
    zh_next: str
    en_next: str
    known: bool = True


def failure_hint(
    code: str | None,
    *,
    locale: str | None = "zh-CN",
    tool: str | None = None,
) -> FailureHint:
    """Return a localized hint for ``code``; unknown codes get a generic one.

    The function never raises: failure rendering must not fail the run because
    a new tool introduced a code that this mapping does not know yet.
    """

    # Only an explicit English signal selects English; an unknown/empty locale
    # falls back to zh-CN, matching the project's default-locale policy.
    zh = not str(locale or "").lower().startswith("en")
    normalized = (code or "").strip().upper()
    hint = (
        _platform_hint(normalized)
        or _execution_hint(normalized, tool)
        or _run_hint(normalized)
    )
    if hint is not None:
        return _localize(hint, zh)
    name = (tool or "").strip()
    if zh:
        title = f"工具 {name} 执行失败。" if name else "执行失败。"
        next_step = "请把下方技术信息提供给管理员排查。"
    else:
        title = f"Tool {name} failed." if name else "The run failed."
        next_step = "Provide the technical details below to an administrator."
    return FailureHint(title=title, next_step=next_step, known=False)


def _localize(hint: _Hint, zh: bool) -> FailureHint:
    return FailureHint(
        title=hint.zh_title if zh else hint.en_title,
        next_step=hint.zh_next if zh else hint.en_next,
        known=hint.known,
    )


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
        )
    if code == "PLATFORM_ADAPTER_NOT_FOUND":
        return _Hint(
            zh_title="平台适配器不存在或未启用。",
            en_title="The platform adapter is missing or disabled.",
            zh_next="请联系管理员检查平台接入配置。",
            en_next="Ask an administrator to check the platform integration.",
        )
    if code == "PLATFORM_SESSION_REQUIRED":
        return _Hint(
            zh_title="平台登录态缺失，当前无法访问该平台。",
            en_title="The platform session is missing, so the platform cannot be accessed.",
            zh_next="请先完成平台登录或授权后重试。",
            en_next="Sign in or authorize the platform, then retry.",
        )
    if code == "CREDENTIAL_MISSING":
        return _Hint(
            zh_title="平台凭据未配置。",
            en_title="The platform credential is not configured.",
            zh_next="请联系管理员在项目平台补充凭据。",
            en_next="Ask an administrator to add the platform credential.",
        )
    if code == "PLATFORM_SETTINGS_UNAVAILABLE":
        return _Hint(
            zh_title="平台设置暂时不可读。",
            en_title="Platform settings are temporarily unavailable.",
            zh_next="请稍后重试；若持续失败，请联系管理员。",
            en_next="Retry later; if it persists, contact an administrator.",
        )
    if code == "PLATFORM_BUSINESS_ERROR":
        return _Hint(
            zh_title="平台返回业务错误。",
            en_title="The platform returned a business error.",
            zh_next="请核对业务参数或平台返回的业务码后重试。",
            en_next="Check the business arguments or the platform business code, then retry.",
        )
    if code.startswith("CONTROL_") or code.startswith("CREDENTIAL_"):
        return _Hint(
            zh_title="平台控制面或凭据校验拒绝了本次请求。",
            en_title="The platform control plane or credential check rejected the request.",
            zh_next="请联系管理员核对平台配置与凭据。",
            en_next="Ask an administrator to check the platform configuration and credential.",
        )
    return None


def _execution_hint(code: str, tool: str | None) -> _Hint | None:
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
    if code == "RUN_NO_PROGRESS":
        return _Hint(
            zh_title="执行失败且无法继续，已停止。",
            en_title="The run failed and could not continue, so it stopped.",
            zh_next="请根据技术信息修正后重试。",
            en_next="Fix the cause using the technical details, then retry.",
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
