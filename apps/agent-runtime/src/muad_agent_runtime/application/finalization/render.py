"""平台确定性的收尾文案（design §4.5）。

失败收尾不调用模型：同一份 facts 在任何历史/压缩状态下都渲染出同一段文本。文案只引用
facts 中已脱敏、有界的摘要，不渲染原始 stdout/stderr，也不写与事实矛盾的固定句。

用户可见正文只放「发生了什么、下一步」；终止码、工具码、Run ID 与平台原文属于运维证据，
统一放在末尾的技术信息块，供用户复制给开发排查。
"""

from __future__ import annotations

from muad_agent_core.agent.termination import TerminationReason
from muad_agent_core.failures import FailureHint, failure_hint
from muad_logging import get_logger

from ...metrics import FINALIZATION_FAILURE_HINT_MISSING_METRIC, record_counter
from .facts import EvidenceStatus, FailureSummary, FinalizationFacts

logger = get_logger(__name__)
_PREVIEW_LIMIT = 300


def render_finalization(
    facts: FinalizationFacts,
    *,
    locale: str | None = "zh-CN",
    run_id: str | None = None,
    include_technical: bool = True,
) -> str:
    """确定性渲染；未知 locale 回落 zh-CN，不编造语言。"""
    zh = _is_chinese(locale)
    reason = facts.termination.reason
    if reason is TerminationReason.CANCELLED:
        return render_cancelled(facts, locale=locale, run_id=run_id)

    primary = _primary_failure(facts)
    reason_text, next_step = _reason_and_next(facts, primary, locale=locale, zh=zh)
    lines = [
        _pick(zh, "执行未完成。", "The run did not complete."),
        _pick(zh, "原因：", "Cause: ") + reason_text,
        _pick(zh, "下一步：", "Next step: ") + next_step,
        _completion_line(facts, zh),
    ]
    if include_technical:
        lines.extend(["", *render_technical_details(facts, locale=locale, run_id=run_id)])
    return "\n".join(lines)


def render_technical_details(
    facts: FinalizationFacts, *, locale: str | None, run_id: str | None,
) -> list[str]:
    return _technical_lines(facts, _primary_failure(facts), zh=_is_chinese(locale), run_id=run_id)


def render_cancelled(
    facts: FinalizationFacts | None = None,
    *,
    locale: str | None = "zh-CN",
    run_id: str | None = None,
) -> str:
    """取消不是失败：不写错误码，只说明已停止与可查看的既有结果。"""
    zh = _is_chinese(locale)
    lines = [
        _pick(zh, "执行已被用户停止。", "The run was stopped by the user."),
        _pick(
            zh,
            "没有生成最终结论；停止前已完成的工具结果仍可在运行记录中查看。",
            "No final conclusion was produced; completed tool results remain available in the run record.",
        ),
    ]
    technical: list[str] = []
    if run_id:
        technical.append(_pick(zh, "- Run ID：", "- Run ID: ") + str(run_id))
    if facts is not None:
        usage = _usage_brief(facts, zh)
        if usage:
            technical.append("- " + usage)
    if technical:
        lines.extend(
            [
                "",
                _pick(zh, "技术信息（提供给开发排查）：", "Technical details (for support):"),
                *technical,
            ]
        )
    return "\n".join(lines)


def _primary_failure(facts: FinalizationFacts) -> FailureSummary | None:
    """NO_PROGRESS 用决定性失败；预算/期限仍用最近失败作为技术信息。"""
    evidence = facts.business_failure_evidence
    if facts.termination.reason is TerminationReason.NO_PROGRESS:
        return evidence.decisive_failure or evidence.last_failure
    return evidence.last_failure


def _reason_and_next(
    facts: FinalizationFacts,
    primary: FailureSummary | None,
    *,
    locale: str | None,
    zh: bool,
) -> tuple[str, str]:
    reason = facts.termination.reason
    if reason is TerminationReason.TURN_BUDGET:
        return (
            _pick(
                zh,
                "本轮执行达到最大轮次上限，已停止。",
                "The run reached its turn limit and stopped.",
            ),
            _pick(
                zh,
                "请拆分任务或减少排查轮次后重试。",
                "Split the task or reduce diagnosis turns, then retry.",
            ),
        )
    if reason is TerminationReason.TOOL_BUDGET:
        return (
            _pick(
                zh,
                "本轮执行达到工具调用上限，已停止。",
                "The run reached its tool-call limit and stopped.",
            ),
            _pick(zh, "请拆分任务或减少工具调用后重试。", "Split the task or reduce tool calls, then retry."),
        )
    if reason is TerminationReason.DEADLINE:
        return (
            _pick(zh, "本轮执行超过期限，已停止。", "The run exceeded its deadline and stopped."),
            _pick(zh, "请缩小范围或延长期限后重试。", "Narrow the task or extend the deadline, then retry."),
        )
    if reason is TerminationReason.NO_PROGRESS:
        hint = _hint(
            primary.error_code if primary is not None else facts.termination.error_code,
            locale=locale,
            tool=primary.tool if primary is not None else None,
        )
        if primary is not None and not primary.retryable:
            prefix = _pick(
                zh, "失败不可重试，已停止。", "The failure is not retryable, so the run stopped."
            )
        elif primary is not None:
            prefix = _pick(
                zh,
                "同一失败重复达到上限，已停止。",
                "The same failure reached the retry limit, so the run stopped.",
            )
        else:
            prefix = _pick(zh, "执行失败，已停止。", "The run failed and stopped.")
        return _sentence(prefix, hint.title, zh), hint.next_step

    hint = _hint(facts.termination.error_code, locale=locale)
    if hint.known:
        return hint.title, hint.next_step
    return (
        _pick(zh, "执行过程中发生错误，已停止。", "The run stopped after an error."),
        _pick(
            zh,
            "请根据技术信息修正后重试；若持续失败，请联系管理员。",
            "Fix the cause using the technical details; if it persists, contact an administrator.",
        ),
    )


def _hint(code: str | None, *, locale: str | None, tool: str | None = None) -> FailureHint:
    """Render a hint and make an unregistered code family visible to operators."""
    hint = failure_hint(code, locale=locale, tool=tool)
    if not hint.known:
        record_counter(FINALIZATION_FAILURE_HINT_MISSING_METRIC)
        logger.warning("finalization_failure_hint_missing code=%s", code or "")
    return hint


def _technical_lines(
    facts: FinalizationFacts,
    primary: FailureSummary | None,
    *,
    zh: bool,
    run_id: str | None,
) -> list[str]:
    lines = [_pick(zh, "技术信息（提供给开发排查）：", "Technical details (for support):")]
    if facts.termination.error_code:
        lines.append(_pick(zh, "- 终止码：", "- Termination code: ") + facts.termination.error_code)
    if primary is not None:
        if primary.error_code:
            lines.append(_pick(zh, "- 错误码：", "- Error code: ") + primary.error_code)
        if primary.tool:
            lines.append(_pick(zh, "- 工具：", "- Tool: ") + primary.tool)
        if primary.exit_code is not None:
            lines.append(_pick(zh, "- 退出码：", "- Exit code: ") + str(primary.exit_code))
        if primary.retryable:
            lines.append(_pick(zh, "- 可重试：是", "- Retryable: yes"))
    if run_id:
        lines.append(_pick(zh, "- Run ID：", "- Run ID: ") + str(run_id))
    usage = _usage_brief(facts, zh)
    if usage:
        lines.append("- " + usage)
    lines.append("- " + _evidence_line(facts, zh))
    if primary is not None and primary.evidence_preview.strip():
        lines.append(
            _pick(zh, "- 平台返回：", "- Platform response: ") + _clip(primary.evidence_preview)
        )
    if facts.pending_tool_calls:
        names = "、".join(
            call.get("tool_name") or call.get("call_id") or "?"
            for call in facts.pending_tool_calls
        )
        lines.append(
            _pick(
                zh,
                f"- 未执行调用：{len(facts.pending_tool_calls)} 个（{names}）",
                f"- Calls not executed: {len(facts.pending_tool_calls)} ({names})",
            )
        )
    return lines


def _usage_brief(facts: FinalizationFacts, zh: bool) -> str:
    usage = facts.usage
    if not any((usage.turns_used, usage.tool_calls_used, usage.elapsed_ms)):
        return ""
    parts = [
        _pick(zh, "轮次 ", "turns ") + f"{usage.turns_used}/{usage.turns_limit or '-'}",
        _pick(zh, "工具 ", "tools ") + f"{usage.tool_calls_used}/{usage.tool_calls_limit or '-'}",
        _pick(zh, "耗时 ", "elapsed ")
        + f"{usage.elapsed_ms / 1000:.1f}s/{usage.deadline_ms / 1000:.1f}s",
    ]
    return _pick(zh, "用量：", "Usage: ") + "，".join(parts)


def _evidence_line(facts: FinalizationFacts, zh: bool) -> str:
    labels = {
        EvidenceStatus.CONFIRMED_FAILURE: ("已确认失败", "failure confirmed"),
        EvidenceStatus.CONFIRMED_NONE: ("已确认无业务失败", "no business failure confirmed"),
        EvidenceStatus.NOT_OBSERVED: ("未观察到业务执行结果", "no business execution observed"),
        EvidenceStatus.UNKNOWN: ("无法确认业务结果", "business result unknown"),
    }
    pair = labels.get(EvidenceStatus(facts.evidence_status), labels[EvidenceStatus.UNKNOWN])
    line = _pick(zh, "证据状态：", "Evidence: ") + pair[0 if zh else 1]
    if facts.evidence_incomplete:
        line += _pick(
            zh,
            "；输出被截断，证据可能不完整",
            "; output was truncated, evidence may be incomplete",
        )
    return line


def _completion_line(facts: FinalizationFacts, zh: bool) -> str:
    if facts.completion.status == "UNKNOWN":
        return _pick(
            zh,
            "完成情况：无法确认（没有交付物或输出契约可核对）。",
            "Completion: unknown (no deliverable or output contract to verify).",
        )
    if facts.completion.status == "CONTRACT_VERIFIED":
        return _pick(zh, "完成情况：输出契约已通过。", "Completion: output contract verified.")
    return _pick(zh, "完成情况：输出契约未通过。", "Completion: output contract failed.")


def _sentence(first: str, second: str, zh: bool) -> str:
    if not second:
        return first
    return first + second if zh else first + " " + second


def _pick(zh: bool, chinese: str, english: str) -> str:
    return chinese if zh else english


def _is_chinese(locale: str | None) -> bool:
    # Only an explicit English signal selects English; an unknown/empty locale
    # falls back to zh-CN (the project's default-locale policy).
    return not str(locale or "").lower().startswith("en")


def _clip(text: str) -> str:
    text = " ".join(text.split())
    if len(text) <= _PREVIEW_LIMIT:
        return text
    return text[: _PREVIEW_LIMIT - 1] + "…"
