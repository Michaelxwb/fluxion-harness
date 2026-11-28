"""平台确定性的收尾文案（design §4.5）。

失败收尾不调用模型：同一份 facts 在任何历史/压缩状态下都渲染出同一段文本。文案只引用
facts 中已脱敏、有界的摘要，不渲染原始 stdout/stderr，也不写与事实矛盾的固定句。

用户可见正文只放「发生了什么、下一步」；终止码、工具码、Run ID 与平台原文属于运维证据，
统一放在末尾的技术信息块，供用户复制给开发排查。
"""

from __future__ import annotations

from collections.abc import Mapping

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
    ]
    if facts.has_unverified_write:
        next_step = _pick(zh, "请先核对上一次操作的结果。", "Verify the previous operation first.")
    impact = _impact_line(facts, primary, zh)
    if impact:
        lines.append(_pick(zh, "影响：", "Impact: ") + impact)
    lines.extend(
        [
            _pick(zh, "下一步：", "Next step: ") + next_step,
            _completion_line(facts, zh),
        ]
    )
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
    """NO_PROGRESS uses only the frozen decisive failure; never the last row."""
    evidence = facts.business_failure_evidence
    if facts.termination.reason in {
        TerminationReason.NO_PROGRESS,
        TerminationReason.TOOL_FAILURE,
    }:
        return evidence.decisive_failure
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
                "请拆分任务或减少排查轮次后重试；已完成的结果保留在会话中，可回复“继续”接着执行。",
                "Split the task or reduce diagnosis turns, then retry; completed results "
                "remain in the conversation, or reply \"continue\" to resume.",
            ),
        )
    if reason is TerminationReason.TOOL_BUDGET:
        return (
            _pick(
                zh,
                "本轮执行达到工具调用上限，已停止。",
                "The run reached its tool-call limit and stopped.",
            ),
            _pick(
                zh,
                "请缩小任务后重试；已完成的工具结果保留在会话中，可回复“继续”接着执行。",
                "Narrow the task, then retry; completed tool results remain in the conversation, "
                "or reply \"continue\" to resume.",
            ),
        )
    if reason is TerminationReason.DEADLINE:
        return (
            _pick(zh, "本轮执行超过期限，已停止。", "The run exceeded its deadline and stopped."),
            _pick(zh, "请缩小范围或延长期限后重试。", "Narrow the task or extend the deadline, then retry."),
        )
    if reason in {TerminationReason.NO_PROGRESS, TerminationReason.TOOL_FAILURE}:
        if primary is None:
            return (
                _pick(
                    zh,
                    "执行未能继续，具体原因尚无法确认。",
                    "The run could not continue, and the specific cause is not confirmed.",
                ),
                _pick(
                    zh,
                    "请重试；若持续失败，请联系管理员在运行记录中查看。",
                    "Retry; if it persists, ask an administrator to check the run record.",
                ),
            )
        hint = _hint(
            primary.error_code,
            locale=locale,
            tool=primary.tool,
            detail=_hint_detail(primary),
        )
        next_step = hint.next_step
        if hint.action_owner == "ADMIN":
            next_step = _sentence(
                next_step,
                _pick(
                    zh,
                    "管理员可在运行记录中查看详情。",
                    "An administrator can see the details in the run record.",
                ),
                zh,
            )
        return hint.title, next_step

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


def _detail_of(primary: FailureSummary | None) -> dict[str, object]:
    if primary is None or not isinstance(primary.diagnostic, Mapping):
        return {}
    detail = primary.diagnostic.get("detail")
    return dict(detail) if isinstance(detail, Mapping) else {}


def _hint_detail(primary: FailureSummary | None) -> dict[str, object] | None:
    detail = _detail_of(primary)
    if primary is not None and isinstance(primary.diagnostic, Mapping):
        for key in ("gate_reason", "source_tool_call_id"):
            value = primary.diagnostic.get(key)
            if isinstance(value, str) and value:
                detail.setdefault(key, value)
    return detail or None


def _impact_line(
    facts: FinalizationFacts, primary: FailureSummary | None, zh: bool
) -> str:
    if facts.has_unverified_write is True:
        return _pick(
            zh,
            "上一次操作是否已经生效尚未确认，请先核对结果。",
            "Whether the earlier operation took effect is not confirmed; verify it first.",
        )
    detail = _detail_of(primary)
    confirmed_preflight = (
        facts.has_unverified_write is False
        and primary is not None
        and primary.tool in {"draft_schedule", "draft_schedule_update"}
        and primary.error_code == "SCHEDULE_INVOCATION_CONTRACT_MISSING"
        and bool(detail.get("reason") or detail.get("repair") or detail.get("fields"))
    )
    if confirmed_preflight:
        return _pick(
            zh,
            "本次草稿未通过预检。",
            "This draft did not pass preflight.",
        )
    return ""


def _hint(
    code: str | None,
    *,
    locale: str | None,
    tool: str | None = None,
    detail: Mapping[str, object] | None = None,
) -> FailureHint:
    """Render a hint and make an unregistered code family visible to operators."""
    hint = failure_hint(code, locale=locale, tool=tool, detail=detail)
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
        left, right = ("（", "）") if zh else (" (", ")")
        lines.append(
            _pick(zh, "- 终止原因：", "- Termination reason: ")
            + str(facts.termination.reason)
            + left
            + facts.termination.error_code
            + right
        )
    evidence = facts.business_failure_evidence
    if evidence.trigger_tool_call_id:
        lines.append(
            _pick(zh, "- 触发调用：", "- Trigger call: ") + evidence.trigger_tool_call_id
        )
    if evidence.source_tool_call_id:
        lines.append(
            _pick(zh, "- 来源调用：", "- Source call: ") + evidence.source_tool_call_id
        )
    if primary is not None:
        if primary.error_code:
            lines.append(_pick(zh, "- 错误码：", "- Error code: ") + primary.error_code)
        if primary.tool:
            lines.append(_pick(zh, "- 工具：", "- Tool: ") + primary.tool)
        if primary.exit_code is not None:
            lines.append(_pick(zh, "- 退出码：", "- Exit code: ") + str(primary.exit_code))
        lines.append(
            _pick(zh, "- 原调用可重放：", "- Original call replayable: ")
            + _retry_label(primary.retryable, zh)
        )
        if primary.retry_disposition:
            lines.append(
                _pick(zh, "- 重试处置：", "- Retry disposition: ") + primary.retry_disposition
            )
        if primary.agent_disposition:
            lines.append(
                _pick(zh, "- Agent 处置：", "- Agent disposition: ") + primary.agent_disposition
            )
        diagnostic = primary.diagnostic or {}
        owner = diagnostic.get("action_owner")
        if isinstance(owner, str) and owner:
            lines.append(_pick(zh, "- 修复责任方：", "- Action owner: ") + owner)
        source = diagnostic.get("message_source")
        if isinstance(source, str) and source:
            lines.append(_pick(zh, "- 消息来源：", "- Message source: ") + source)
        gate = diagnostic.get("gate_reason")
        if isinstance(gate, str) and gate:
            lines.append(_pick(zh, "- 门禁原因：", "- Gate reason: ") + gate)
        lines.extend(_detail_lines(_detail_of(primary), zh))
    protecting = next(
        (
            item
            for item in evidence.failures
            if item.error_code == "TOOL_RETRY_UNSAFE"
            and item.tool_call_id == evidence.trigger_tool_call_id
        ),
        None,
    )
    if protecting is not None and protecting is not primary:
        gate_reason = (protecting.diagnostic or {}).get("gate_reason")
        lines.append(
            _pick(zh, "- 保护动作：", "- Protection: ")
            + _gate_label(gate_reason, zh)
        )
    if run_id:
        lines.append(_pick(zh, "- Run ID：", "- Run ID: ") + str(run_id))
    usage = _usage_brief(facts, zh)
    if usage:
        lines.append("- " + usage)
    lines.append("- " + _evidence_line(facts, zh))
    if primary is not None and primary.evidence_preview.strip():
        lines.append(
            _message_label(primary, zh) + _clip(primary.evidence_preview)
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


_DETAIL_LABELS = {
    "field": ("- 字段：", "- Field: "),
    "fields": ("- 字段：", "- Fields: "),
    "reason": ("- 原因：", "- Reason: "),
    "repair": ("- 修复动作：", "- Repair: "),
    "allowed_values": ("- 可选值：", "- Allowed values: "),
    "entry": ("- 入口：", "- Entry: "),
}


def _detail_lines(detail: Mapping[str, object], zh: bool) -> list[str]:
    lines: list[str] = []
    for key, (zh_label, en_label) in _DETAIL_LABELS.items():
        if key not in detail:
            continue
        value = detail[key]
        if key == "fields" and isinstance(value, (list, tuple)):
            parts: list[str] = []
            for item in value:
                if not isinstance(item, Mapping):
                    parts.append(str(item))
                    continue
                name = str(item.get("field") or "").strip()
                reason = str(item.get("reason") or "").strip()
                parts.append(f"{name} ({reason})" if name and reason else name or reason)
            text = ", ".join(_clip(part) for part in parts if part)
        elif isinstance(value, (list, tuple)):
            text = ", ".join(_clip(str(item)) for item in value)
        else:
            text = _clip(str(value))
        if text:
            lines.append((zh_label if zh else en_label) + text)
    return lines


def _message_label(primary: FailureSummary, zh: bool) -> str:
    source = (primary.diagnostic or {}).get("message_source")
    if source == "PLATFORM":
        return _pick(zh, "- 平台返回：", "- Platform response: ")
    if source == "LOCAL":
        return _pick(zh, "- 工具描述：", "- Tool description: ")
    if source == "CATALOG":
        return _pick(zh, "- 诊断摘要：", "- Diagnostic summary: ")
    return _pick(zh, "- 证据摘要：", "- Evidence summary: ")


def _retry_label(value: bool | None, zh: bool) -> str:
    if value is True:
        return _pick(zh, "是", "yes")
    if value is False:
        return _pick(zh, "否", "no")
    return _pick(zh, "未知", "unknown")


def _gate_label(value: object, zh: bool) -> str:
    labels = {
        "RETRY_GUARDED": ("已阻止未核对的原样重试", "guarded retry blocked"),
        "UNVERIFIED_WRITE": (
            "已阻止未核对写入的其他操作",
            "other operations blocked until the write is verified",
        ),
        "BLOCKED_ATTEMPT": ("已阻止已失败的相同尝试", "blocked the failed attempt"),
    }
    key = str(value or "")
    zh_text, en_text = labels.get(
        key, ("已阻止不安全的继续执行", "unsafe continuation blocked")
    )
    suffix = f"（{key}）" if zh and key else f" ({key})" if key else ""
    return (zh_text if zh else en_text) + suffix


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
        if facts.tool_outcomes.succeeded > 0:
            return _pick(
                zh,
                f"完成情况：已取得 {facts.tool_outcomes.succeeded} 个工具结果，但未形成最终结论。",
                f"Completion: {facts.tool_outcomes.succeeded} tool result(s) were obtained, "
                "but no final conclusion was formed.",
            )
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
