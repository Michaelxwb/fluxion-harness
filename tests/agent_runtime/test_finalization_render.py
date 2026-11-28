"""`FinalizationRenderer` 的确定性文案（design §4.5 / §8 单元）。"""

from __future__ import annotations

from dataclasses import replace

from muad_agent_core.agent.termination import (
    BudgetKind,
    FailureEvidence,
    FailureState,
    TerminationReason,
    TerminationSnapshot,
)
from muad_agent_runtime.application.finalization import (
    ToolCallRecord,
    build_finalization_facts,
    render_cancelled,
    render_finalization,
)


def _facts(*, reason_status="NO_PROGRESS", error_code="RUN_NO_PROGRESS", records=(), snapshot=None):
    records = tuple(
        replace(record, tool_call_id=record.tool_call_id or f"call-{record.tool_name}")
        for record in records
    )
    if snapshot is None:
        snapshot = TerminationSnapshot(
            status=reason_status,
            turns_used=20,
            turns_limit=20,
            tool_calls_used=20,
            tool_calls_limit=30,
            elapsed_ms=99963,
            deadline_ms=3600000,
        )
    if reason_status == "NO_PROGRESS" and records:
        source = records[-1].tool_call_id
        snapshot = replace(
            snapshot,
            failure_state=replace(
                snapshot.failure_state,
                no_progress=True,
                no_progress_trigger_call_id=source,
                no_progress_source_call_id=source,
            ),
        )
    return build_finalization_facts(
        error_code=error_code,
        snapshot=snapshot,
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
    assert "终止原因：NO_PROGRESS（RUN_NO_PROGRESS）" in text
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
    assert "完成情况：已取得 1 个工具结果，但未形成最终结论。" in text
    assert "可回复“继续”接着执行" in text
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
    assert "证据摘要：mssw session rejected" in text
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
    assert "证据摘要：502 bad gateway" in text
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
                tool_call_id="call-execute",
                code="PLATFORM_AUTH_REJECTED",
                message="rejected",
                failure_layer="CONTENT",
                status="FAILED",
            ),
            no_progress=True,
            no_progress_trigger_call_id="call-execute",
            no_progress_source_call_id="call-execute",
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


def test_retryability_only_changes_the_technical_retry_label() -> None:
    blocked = render_finalization(_facts(records=[_auth_failure(retryable=False)]))
    retryable = render_finalization(_facts(records=[_auth_failure(retryable=True)]))

    for text in (blocked, retryable):
        assert "原因：平台登录失败" in text
        assert "失败不可重试，已停止" not in text
        assert "同一失败重复达到上限" not in text
    assert "原调用可重放：否" in blocked
    assert "原调用可重放：是" in retryable


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


def _contract_facts(
    *,
    reason: str | None = "CONTRACT_MISSING",
    unverified: bool = False,
    code: str = "SCHEDULE_INVOCATION_CONTRACT_MISSING",
    field_list: bool = False,
) -> object:
    detail = (
        {
            "detail": (
                {
                    "fields": [
                        {
                            "field": "payload.skill_key",
                            "reason": reason,
                            "repair": "REIMPORT_SKILL",
                        }
                    ],
                    "reason": reason,
                    "repair": "REIMPORT_SKILL",
                }
                if field_list
                else {
                    "field": "payload.skill_id",
                    "reason": reason,
                    "repair": "REIMPORT_SKILL",
                }
            ),
            "action_owner": "ADMIN",
            "message_source": "PLATFORM",
        }
        if reason is not None
        else None
    )
    record = ToolCallRecord(
        tool_name="draft_schedule",
        tool_call_id="call-schedule",
        business_status="FAILED",
        failure_layer="TOOL",
        error_code=code,
        retryable=False,
        retry_disposition="BLOCK",
        agent_disposition="STOP",
        diagnostic=detail,
        evidence_preview="该 Skill 缺少可调度的调用契约",
    )
    snapshot = TerminationSnapshot(
        status="NO_PROGRESS",
        failure_state=FailureState(
            no_progress=True,
            no_progress_trigger_call_id="call-schedule",
            no_progress_source_call_id="call-schedule",
            unknown_write_targets=frozenset({"platform:a"}) if unverified else frozenset(),
        ),
    )
    return build_finalization_facts(
        error_code="RUN_NO_PROGRESS",
        snapshot=snapshot,
        tool_calls=[record],
    )


def test_contract_missing_copy_names_admin_reimport_and_safe_impact() -> None:
    facts = _contract_facts(reason="CONTRACT_MISSING")

    text = render_finalization(facts, run_id="run-1")
    body, _, technical = text.partition("技术信息")

    assert "技能缺少定时执行配置" in body
    assert "管理员重新导入" in body
    assert "本次草稿未通过预检" in body
    assert "SCHEDULE_INVOCATION_CONTRACT_MISSING" not in body
    assert "SCHEDULE_INVOCATION_CONTRACT_MISSING" in technical
    assert "字段：payload.skill_id" in technical
    assert "原因：CONTRACT_MISSING" in technical
    assert "修复动作：REIMPORT_SKILL" in technical
    assert "来源调用：call-schedule" in technical
    assert "平台返回：该 Skill 缺少可调度的调用契约" in technical


def test_contract_invalid_copy_is_distinct_from_missing() -> None:
    text = render_finalization(_contract_facts(reason="CONTRACT_INVALID"))

    assert "技能的定时执行配置无效" in text
    assert "技能缺少定时执行配置" not in text


def test_tool_failure_shows_the_business_cause_and_the_protecting_gate() -> None:
    snapshot = TerminationSnapshot(
        status="TOOL_FAILURE",
        failure_state=FailureState(
            no_progress=True,
            failure_pending=True,
            no_progress_trigger_call_id="call-retry",
            no_progress_source_call_id="call-timeout",
        ),
    )
    records = [
        ToolCallRecord(
            tool_name="execute",
            tool_call_id="call-timeout",
            business_status="FAILED",
            failure_layer="TOOL",
            error_code="SANDBOX_TIMEOUT",
            retry_disposition="BLOCK",
            agent_disposition="CONTINUE",
            evidence_preview="sandbox execution exceeded 2s",
        ),
        ToolCallRecord(
            tool_name="execute",
            tool_call_id="call-retry",
            business_status="FAILED",
            failure_layer="TOOL",
            error_code="TOOL_RETRY_UNSAFE",
            retry_disposition="BLOCK",
            agent_disposition="STOP",
            diagnostic={"gate_reason": "RETRY_GUARDED", "action_owner": "NONE"},
            evidence_preview="The previous operation cannot safely be run again.",
        ),
    ]

    text = render_finalization(
        build_finalization_facts(
            error_code="RUN_TOOL_FAILURE_UNRESOLVED",
            snapshot=snapshot,
            tool_calls=records,
        )
    )
    _, _, technical = text.partition("技术信息")

    assert "错误码：SANDBOX_TIMEOUT" in technical
    assert "保护动作：已阻止未核对的原样重试（RETRY_GUARDED）" in technical
    assert "来源调用：call-timeout" in technical
    assert "触发调用：call-retry" in technical


def test_contract_field_list_names_the_field_and_reason() -> None:
    text = render_finalization(_contract_facts(reason="CONTRACT_INVALID", field_list=True))

    body, _, technical = text.partition("技术信息")
    assert "payload.skill_key (CONTRACT_INVALID)" in technical
    assert "{'field'" not in technical
    assert "技能的定时执行配置无效" in body


def test_tool_failure_reason_uses_the_configured_business_cause() -> None:
    facts = _contract_facts(reason="CONTRACT_MISSING")
    facts = replace(
        facts,
        termination=replace(facts.termination, reason=TerminationReason.TOOL_FAILURE),
    )

    text = render_finalization(facts)
    body, _, technical = text.partition("技术信息")

    assert "技能缺少定时执行配置" in body
    assert "管理员重新导入" in body
    assert "工具失败尚未解决" not in body
    assert "终止原因：TOOL_FAILURE" in technical


def test_contract_without_detail_does_not_guess_or_claim_untouched_schedules() -> None:
    facts = _contract_facts(reason=None, code="SCHEDULE_INVOCATION_CONTRACT_MISSING")

    text = render_finalization(facts)
    body, _, technical = text.partition("技术信息")

    assert "定时任务预检未通过" in body
    assert "具体配置原因尚未确定" in body
    assert "技能缺少定时执行配置" not in body
    assert "配置无效" not in body
    assert "已有任务不受影响" not in body
    assert "REIMPORT_SKILL" not in technical


def test_unverified_write_never_claims_the_operation_was_safe() -> None:
    facts = _contract_facts(reason=None, unverified=True, code="TOOL_RETRY_UNSAFE")
    facts = replace(
        facts,
        business_failure_evidence=replace(
            facts.business_failure_evidence,
            decisive_failure=replace(
                facts.business_failure_evidence.decisive_failure,
                diagnostic={
                    "gate_reason": "UNVERIFIED_WRITE",
                    "source_tool_call_id": "call-original",
                },
            ),
        ),
    )

    text = render_finalization(facts)
    body, _, technical = text.partition("技术信息")

    assert "尚未核对" in body
    assert "已有任务不受影响" not in body
    assert "是否已经生效尚未确认" in body
    assert "失败不可重试" not in body
    assert "门禁原因：UNVERIFIED_WRITE" in technical


def test_deterministic_closeout_for_unknown_code_without_detail() -> None:
    record = ToolCallRecord(
        tool_name="execute",
        tool_call_id="call-unknown",
        business_status="FAILED",
        failure_layer="TOOL",
        error_code="SOME_NEW_TOOL_ERROR",
    )
    snapshot = TerminationSnapshot(
        status="NO_PROGRESS",
        failure_state=FailureState(
            no_progress=True,
            no_progress_trigger_call_id="call-unknown",
            no_progress_source_call_id="call-unknown",
        ),
    )
    facts = build_finalization_facts(
        error_code="RUN_NO_PROGRESS", snapshot=snapshot, tool_calls=[record]
    )

    text = render_finalization(facts)
    body, _, technical = text.partition("技术信息")

    assert "工具 execute 执行失败" in body
    assert "具体原因尚未确定" in body
    assert "SOME_NEW_TOOL_ERROR" not in body
    assert "SOME_NEW_TOOL_ERROR" in technical


def test_snapshot_failure_without_trigger_does_not_become_root_cause() -> None:
    snapshot = TerminationSnapshot(
        status="NO_PROGRESS",
        failure_state=FailureState(
            last_failure=FailureEvidence(
                tool="draft_schedule",
                tool_call_id="call-guess",
                code="SCHEDULE_INVOCATION_CONTRACT_MISSING",
                message="该 Skill 缺少可调度的调用契约",
                failure_layer="TOOL",
                status="FAILED",
            ),
            no_progress=True,
        ),
    )
    facts = build_finalization_facts(
        error_code="RUN_NO_PROGRESS", snapshot=snapshot, tool_calls=[]
    )

    text = render_finalization(facts)

    assert "具体原因尚无法确认" in text
    assert "技能缺少定时执行配置" not in text


def test_english_contract_copy_has_same_semantics() -> None:
    text = render_finalization(
        _contract_facts(reason="CONTRACT_MISSING"), locale="en-US", run_id="run-1"
    )
    body, _, technical = text.partition("Technical details")

    assert "lacks scheduling configuration" in body
    assert "administrator can reimport" in body
    assert "This draft did not pass preflight" in body
    assert "SCHEDULE_INVOCATION_CONTRACT_MISSING" in technical
    assert "REIMPORT_SKILL" in technical
