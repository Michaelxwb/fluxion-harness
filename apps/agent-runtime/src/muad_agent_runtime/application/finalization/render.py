"""平台确定性的收尾文案（design §4.5）。

失败收尾不调用模型：同一份 facts 在任何历史/压缩状态下都渲染出同一段文本。文案只引用
facts 中已脱敏、有界的摘要，不渲染原始 stdout/stderr，也不写与事实矛盾的固定句。
"""

from __future__ import annotations

from muad_agent_core.agent.termination import TerminationReason

from .facts import EvidenceStatus, FailureSummary, FinalizationFacts

_PREVIEW_LIMIT = 300
_REASON_LABELS = {
    TerminationReason.TURN_BUDGET: ("轮次预算耗尽", "turn budget exhausted"),
    TerminationReason.TOOL_BUDGET: ("工具调用预算耗尽", "tool-call budget exhausted"),
    TerminationReason.DEADLINE: ("执行期限已到", "run deadline exceeded"),
    TerminationReason.NO_PROGRESS: ("重复失败，已停止", "repeated failures, stopped"),
    TerminationReason.CANCELLED: ("用户已停止", "cancelled by the user"),
    TerminationReason.RUN_ERROR: ("执行错误", "run error"),
}
_SUGGESTIONS = {
    TerminationReason.TURN_BUDGET: (
        "减少单次执行中的排查轮次，或拆分任务后重试。",
        "Reduce diagnosis turns or split the task, then retry.",
    ),
    TerminationReason.TOOL_BUDGET: (
        "减少工具调用次数，或拆分任务后重试。",
        "Reduce tool calls or split the task, then retry.",
    ),
    TerminationReason.DEADLINE: (
        "任务在期限内未完成；可缩小范围或延长期限后重试。",
        "The task did not finish in time; narrow it or extend the deadline.",
    ),
    TerminationReason.NO_PROGRESS: (
        "先按上面的失败证据修正参数、凭据或输入，避免重复相同调用。",
        "Fix the arguments, credentials or input shown above before retrying.",
    ),
    TerminationReason.CANCELLED: (
        "如需完整结果，请重新发起执行。",
        "Start a new run if you still need the result.",
    ),
    TerminationReason.RUN_ERROR: (
        "根据失败证据修正后重试；若持续失败，请联系平台管理员。",
        "Fix the cause shown above and retry; contact the platform admin if it persists.",
    ),
}


def render_finalization(facts: FinalizationFacts, *, locale: str = "zh-CN") -> str:
    """确定性渲染；未知 locale 回落 zh-CN，不编造语言。"""
    zh = _is_chinese(locale)
    reason = facts.termination.reason
    if reason is TerminationReason.CANCELLED:
        return render_cancelled(facts, locale=locale)

    lines: list[str] = []
    lines.append(
        _pick(zh, "执行未完成。", "The run did not complete.")
    )
    reason_text = _reason_label(reason, zh)
    secondary = [
        _reason_label(item, zh)
        for item in facts.termination.secondary_reasons
        if item is not reason
    ]
    termination_line = _pick(zh, "终止原因：", "Termination: ") + reason_text
    if facts.termination.error_code:
        termination_line += f"（{facts.termination.error_code}）"
    if secondary:
        termination_line += _pick(zh, "；同时命中：", "; also reached: ") + "、".join(secondary)
    lines.append(termination_line + "。")
    lines.append(_usage_line(facts, zh))
    lines.append(_tool_outcome_line(facts, zh))

    failures = facts.business_failure_evidence.failures
    last = facts.business_failure_evidence.last_failure
    if failures:
        lines.append(_pick(zh, "业务失败证据：", "Business failure evidence:"))
        for failure in failures:
            lines.append(_failure_line(failure, zh))
        if last is not None and last not in failures:
            lines.append(_pick(zh, "最近失败：", "Most recent failure: ") + _failure_line(last, zh))
    elif last is not None:
        lines.append(
            _pick(zh, "业务失败证据（来自运行快照）：", "Business failure evidence (run snapshot):")
        )
        lines.append(_failure_line(last, zh))
    else:
        lines.append(
            _pick(
                zh,
                "业务失败证据：未确认到业务失败。",
                "Business failure evidence: none confirmed.",
            )
        )

    lines.append(_evidence_line(facts, zh))
    if facts.pending_tool_calls:
        names = "、".join(
            call.get("tool_name") or call.get("call_id") or "?"
            for call in facts.pending_tool_calls
        )
        lines.append(
            _pick(zh, "未执行的调用：", "Calls not executed: ")
            + f"{len(facts.pending_tool_calls)}"
            + _pick(zh, f" 个（{names}），不计入失败。", f" ({names}); not counted as failures.")
        )
    lines.append(_completion_line(facts, zh))
    suggestion = _SUGGESTIONS.get(reason, _SUGGESTIONS[TerminationReason.RUN_ERROR])
    lines.append(_pick(zh, "建议：", "Next step: ") + suggestion[0 if zh else 1])
    return "\n".join(lines)


def render_cancelled(facts: FinalizationFacts | None = None, *, locale: str = "zh-CN") -> str:
    zh = _is_chinese(locale)
    lines = [
        _pick(zh, "执行已被用户停止。", "The run was stopped by the user."),
        _pick(
            zh,
            "没有生成最终结论；停止前已完成的工具结果仍可在运行记录中查看。",
            "No final conclusion was produced; completed tool results remain available in the run record.",
        ),
    ]
    if facts is not None:
        lines.append(_usage_line(facts, zh))
    return "\n".join(lines)


def _usage_line(facts: FinalizationFacts, zh: bool) -> str:
    usage = facts.usage
    parts = [
        _pick(zh, "轮次 ", "turns ")
        + f"{usage.turns_used}/{usage.turns_limit or '-'}",
        _pick(zh, "工具 ", "tools ")
        + f"{usage.tool_calls_used}/{usage.tool_calls_limit or '-'}",
        _pick(zh, "耗时 ", "elapsed ")
        + f"{usage.elapsed_ms / 1000:.1f}s/{usage.deadline_ms / 1000:.1f}s",
    ]
    return _pick(zh, "用量：", "Usage: ") + "，".join(parts) + "。"


def _tool_outcome_line(facts: FinalizationFacts, zh: bool) -> str:
    counts = facts.tool_outcomes
    parts = [
        _pick(zh, "成功 ", "succeeded ") + str(counts.succeeded),
        _pick(zh, "失败 ", "failed ") + str(counts.failed),
    ]
    if counts.timed_out:
        parts.append(_pick(zh, "超时 ", "timed out ") + str(counts.timed_out))
    if counts.denied:
        parts.append(_pick(zh, "拒绝 ", "denied ") + str(counts.denied))
    if counts.cancelled:
        parts.append(_pick(zh, "取消 ", "cancelled ") + str(counts.cancelled))
    if counts.not_applicable:
        parts.append(_pick(zh, "非业务 ", "not applicable ") + str(counts.not_applicable))
    if counts.unknown:
        parts.append(_pick(zh, "未知 ", "unknown ") + str(counts.unknown))
    layer_parts = [
        f"{layer} {count}"
        for layer, count in sorted(counts.failure_layers.items())
        if count
    ]
    line = _pick(zh, "工具结果：", "Tool results: ") + f"{counts.total}" + _pick(zh, " 次（", " calls (")
    line += "，".join(parts) + "）"
    if layer_parts:
        line += _pick(zh, "；失败层：", "; failure layers: ") + "，".join(layer_parts)
    return line + "。"


def _failure_line(failure: FailureSummary, zh: bool) -> str:
    layer_labels = {
        "TOOL": ("工具层", "tool layer"),
        "CONTENT": ("内容层", "content layer"),
    }
    layer_pair = layer_labels.get(
        failure.failure_layer or "", ("未知层", "unknown layer")
    )
    layer = layer_pair[0 if zh else 1]
    line = f"- {failure.tool or '?'}（{layer}）"
    if failure.error_code:
        line += _pick(zh, "错误码 ", " code ") + failure.error_code
    if failure.exit_code is not None:
        line += _pick(zh, "，退出码 ", ", exit code ") + str(failure.exit_code)
    if failure.retryable:
        line += _pick(zh, "，可重试", ", retryable")
    preview = (failure.evidence_preview or "").strip()
    if preview:
        line += "：" + _clip(preview)
    return line


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
            "；输出被截断，证据可能不完整。",
            "; output was truncated, evidence may be incomplete.",
        )
    return line + "。"


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


def _reason_label(reason: TerminationReason, zh: bool) -> str:
    labels = _REASON_LABELS.get(reason, _REASON_LABELS[TerminationReason.RUN_ERROR])
    return labels[0 if zh else 1]


def _pick(zh: bool, chinese: str, english: str) -> str:
    return chinese if zh else english


def _is_chinese(locale: str) -> bool:
    return str(locale or "").lower().startswith("zh")


def _clip(text: str) -> str:
    text = " ".join(text.split())
    if len(text) <= _PREVIEW_LIMIT:
        return text
    return text[: _PREVIEW_LIMIT - 1] + "…"
