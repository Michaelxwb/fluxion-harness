"""`FinalizationRenderer` 的确定性文案（design §4.5 / §8 单元）。"""

from __future__ import annotations

from muad_agent_core.agent.termination import BudgetKind, TerminationSnapshot
from muad_agent_runtime.application.finalization import (
    ToolCallRecord,
    build_finalization_facts,
    render_cancelled,
    render_finalization,
)


def _facts(*, reason_status="NO_PROGRESS", error_code="RUN_NO_PROGRESS", records=(), snapshot=None):
    return build_finalization_facts(
        error_code=error_code,
        snapshot=snapshot
        or TerminationSnapshot(
            status=reason_status,
            turns_used=20,
            turns_limit=20,
            tool_calls_used=20,
            tool_calls_limit=30,
            elapsed_ms=99963,
            deadline_ms=3600000,
        ),
        tool_calls=records,
    )


def _auth_failure(*, retryable: bool = False) -> ToolCallRecord:
    return ToolCallRecord(
        tool_name="execute",
        business_status="FAILED",
        failure_layer="CONTENT",
        error_code="PLATFORM_AUTH_REJECTED",
        exit_code=1,
        retryable=retryable,
        evidence_preview="platform_sessions: mssp=ok; mssw=PLATFORM_AUTH_REJECTED(http=401) denied",
        output_truncated=False,
    )


def test_platform_failure_has_a_plain_cause_and_technical_details() -> None:
    text = render_finalization(_facts(records=[_auth_failure()]), run_id="run-1")

    assert "平台登录失败" in text
    assert "下一步：" in text
    assert "技术信息（提供给开发排查）：" in text
    assert "终止码：RUN_NO_PROGRESS" in text
    assert "错误码：PLATFORM_AUTH_REJECTED" in text
    assert "工具：execute" in text
    assert "Run ID：run-1" in text
    assert "platform_sessions:" in text
    assert "mssw=PLATFORM_AUTH_REJECTED" in text


def test_codes_are_not_in_the_user_facing_body() -> None:
    text = render_finalization(_facts(records=[_auth_failure()]), run_id="run-1")

    body, _, technical = text.partition("技术信息")

    assert "RUN_NO_PROGRESS" not in body
    assert "PLATFORM_AUTH_REJECTED" not in body
    assert "platform_sessions:" not in body
    assert "RUN_NO_PROGRESS" in technical
    assert "PLATFORM_AUTH_REJECTED" in technical


def test_budget_rendering_states_facts_without_contradiction() -> None:
    facts = _facts(
        reason_status="BUDGET_EXCEEDED",
        error_code="RUN_BUDGET_EXCEEDED",
        records=[
            ToolCallRecord(
                tool_name="execute",
                business_status="SUCCEEDED",
                failure_layer="NONE",
                output_truncated=False,
            )
        ],
    )

    text = render_finalization(facts)

    assert "执行未完成" in text
    assert "原因：本轮执行达到最大轮次上限，已停止。" in text
    assert "用量：轮次 20/20，工具 20/30" in text
    assert "证据状态：已确认无业务失败" in text
    assert "完成情况：无法确认" in text
    assert "没有任何工具调用" not in text
    assert "没有任何失败信息" not in text


def test_content_failure_uses_bounded_preview_not_raw_stdout() -> None:
    facts = _facts(
        records=[
            ToolCallRecord(
                tool_name="daily-duty-report-mssp",
                business_status="FAILED",
                failure_layer="CONTENT",
                error_code="PLATFORM_AUTH_REJECTED",
                exit_code=3,
                evidence_preview="mssw session rejected: HTTP 401",
                output_truncated=False,
            )
        ]
    )

    text = render_finalization(facts)

    assert "错误码：PLATFORM_AUTH_REJECTED" in text
    assert "工具：daily-duty-report-mssp" in text
    assert "退出码：3" in text
    assert "平台返回：mssw session rejected" in text
    assert "内容层" not in text
    assert "_{+}" not in text


def test_budget_remains_primary_when_a_tool_failure_also_exists() -> None:
    snapshot = TerminationSnapshot(
        status="BUDGET_EXCEEDED",
        budget_kind=BudgetKind.TURN,
        turns_used=20,
        turns_limit=20,
        tool_calls_used=20,
        tool_calls_limit=30,
    )
    facts = build_finalization_facts(
        error_code="RUN_BUDGET_EXCEEDED",
        snapshot=snapshot,
        tool_calls=[
            ToolCallRecord(
                tool_name="execute",
                business_status="FAILED",
                failure_layer="CONTENT",
                error_code="UPSTREAM_5XX",
                exit_code=1,
                evidence_preview="502 bad gateway",
            )
        ],
    )

    text = render_finalization(facts)

    assert "原因：本轮执行达到最大轮次上限" in text
    assert "错误码：UPSTREAM_5XX" in text
    assert "工具：execute" in text
    assert "平台返回：502 bad gateway" in text
    assert "下一步：" in text


def test_truncated_evidence_is_visible() -> None:
    facts = _facts(
        records=[
            ToolCallRecord(
                tool_name="execute",
                business_status="SUCCEEDED",
                failure_layer="NONE",
                output_truncated=True,
            )
        ],
    )

    text = render_finalization(facts)

    assert "证据状态：无法确认业务结果" in text
    assert "输出被截断" in text


def test_pending_calls_are_listed_but_not_counted_as_failures() -> None:
    from muad_agent_core.agent.termination import PendingToolCall

    snapshot = TerminationSnapshot(
        status="BUDGET_EXCEEDED",
        budget_kind=BudgetKind.TOOL,
        tool_calls_used=30,
        tool_calls_limit=30,
        pending_tool_calls=(PendingToolCall(call_id="c9", tool_name="execute"),),
    )
    facts = build_finalization_facts(
        error_code="RUN_BUDGET_EXCEEDED", snapshot=snapshot, tool_calls=[]
    )

    text = render_finalization(facts)

    assert "未执行调用：1 个（execute）" in text
    assert "失败 0" not in text


def test_snapshot_only_failure_is_not_hidden() -> None:
    from muad_agent_core.agent.termination import FailureEvidence, FailureState

    snapshot = TerminationSnapshot(
        status="NO_PROGRESS",
        failure_state=FailureState(
            last_failure=FailureEvidence(
                tool="execute",
                code="PLATFORM_AUTH_REJECTED",
                message="rejected",
                failure_layer="CONTENT",
                status="FAILED",
            ),
            no_progress=True,
        ),
    )
    facts = build_finalization_facts(
        error_code="RUN_NO_PROGRESS", snapshot=snapshot, tool_calls=[]
    )

    assert facts.evidence_status == "CONFIRMED_FAILURE"
    text = render_finalization(facts)

    assert "平台登录失败" in text
    assert "PLATFORM_AUTH_REJECTED" in text
    assert "未确认到业务失败" not in text


def test_first_deterministic_failure_is_not_labeled_repeated() -> None:
    text = render_finalization(_facts(records=[_auth_failure(retryable=False)]))

    assert "失败不可重试，已停止。" in text
    assert "同一失败重复达到上限" not in text


def test_threshold_failure_is_labeled_repeated() -> None:
    text = render_finalization(_facts(records=[_auth_failure(retryable=True)]))

    assert "同一失败重复达到上限，已停止。" in text
    assert "失败不可重试" not in text


def test_unknown_code_keeps_the_original_code_in_technical_details() -> None:
    facts = _facts(
        records=[
            ToolCallRecord(
                tool_name="execute",
                business_status="FAILED",
                failure_layer="TOOL",
                error_code="SOME_NEW_TOOL_ERROR",
            )
        ]
    )

    text = render_finalization(facts)

    assert "工具 execute 执行失败" in text
    assert "SOME_NEW_TOOL_ERROR" in text.partition("技术信息")[2]


def test_unknown_code_records_the_fallback_metric() -> None:
    from muad_api import render_metrics

    facts = _facts(
        records=[
            ToolCallRecord(
                tool_name="execute",
                business_status="FAILED",
                failure_layer="TOOL",
                error_code="SOME_NEW_TOOL_ERROR",
            )
        ]
    )

    render_finalization(facts)

    assert "finalization_failure_hint_missing_total" in render_metrics()


def test_cancel_text_does_not_invent_failure() -> None:
    text = render_cancelled(run_id="run-1")

    assert "用户停止" in text
    assert "没有生成最终结论" in text
    assert "Run ID：run-1" in text
    assert "失败" not in text


def test_english_locale_renders_english() -> None:
    facts = _facts(
        reason_status="BUDGET_EXCEEDED",
        error_code="RUN_BUDGET_EXCEEDED",
        records=[],
    )

    text = render_finalization(facts, locale="en-US")

    assert "The run did not complete." in text
    assert "turn limit" in text
    assert "Completion: unknown" in text
    assert "Technical details (for support):" in text


def test_preview_is_clipped_deterministically() -> None:
    facts = _facts(
        records=[
            ToolCallRecord(
                tool_name="execute",
                business_status="FAILED",
                failure_layer="CONTENT",
                error_code="X",
                evidence_preview="a" * 1000,
            )
        ]
    )

    first = render_finalization(facts)
    second = render_finalization(facts)

    assert first == second
    assert "a" * 301 not in first
