"""`FinalizationFacts`：平台收尾的唯一事实来源（design §4.4）。

输入是 Kernel 的 `TerminationSnapshot` + 本 Run 已持久化的 `tool_call_audit` + 已有完成契约
结果。输出只含脱敏、有界的摘要；完整记录仍在审计表，facts 不复制原始 stdout/stderr。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from muad_agent_core.agent.termination import (
    FailureEvidence,
    TerminationReason,
    TerminationSnapshot,
    reason_for_error_code,
)

SCHEMA_VERSION = 1
FAILURE_STATUSES = frozenset(("FAILED", "TIMED_OUT", "DENIED"))
FAILURE_LAYERS = frozenset(("TOOL", "CONTENT"))
RETRY_DISPOSITIONS = frozenset(("ALLOW", "WAIT_THEN_RETRY", "BLOCK"))
AGENT_DISPOSITIONS = frozenset(("CONTINUE", "WAIT", "STOP"))
DIAGNOSTIC_KEYS = frozenset(
    (
        "detail",
        "action_owner",
        "hint_known",
        "message_source",
        "gate_reason",
        "trigger_tool_call_id",
        "source_tool_call_id",
        "source_attempt",
    )
)


@dataclass(frozen=True, slots=True)
class ToolCallRecord:
    """一条审计行的业务视图（与 ORM 解耦，facts 构建可纯函数测试）。"""

    tool_name: str
    tool_call_id: str = ""
    status: str = "OK"
    business_status: str | None = None
    failure_layer: str | None = None
    error_code: str | None = None
    exit_code: int | None = None
    #: ``None`` for legacy rows or unknown outcomes; false is explicit refusal.
    retryable: bool | None = None
    retry_disposition: str | None = None
    agent_disposition: str | None = None
    diagnostic: Mapping[str, Any] | None = None
    evidence_preview: str = ""
    output_truncated: bool | None = None
    occurred_at: str = ""

    @property
    def confirmed_failure(self) -> bool:
        return (
            self.business_status in FAILURE_STATUSES
            and self.failure_layer in FAILURE_LAYERS
        )


def tool_call_record(row: Mapping[str, Any]) -> ToolCallRecord:
    """ORM 行 / 字典 → 业务视图；缺失值保持未知，不猜成功。"""
    raw_status = row.get("business_status")
    raw_layer = row.get("failure_layer")
    truncated = row.get("output_truncated")
    retryable = row.get("retryable")
    retry_disposition = row.get("retry_disposition")
    agent_disposition = row.get("agent_disposition")
    return ToolCallRecord(
        tool_name=str(row.get("tool_name") or ""),
        tool_call_id=str(row.get("tool_call_id") or ""),
        status=str(row.get("status") or ""),
        business_status=str(raw_status) if isinstance(raw_status, str) and raw_status else None,
        failure_layer=str(raw_layer) if isinstance(raw_layer, str) and raw_layer else None,
        error_code=str(row["error_code"]) if row.get("error_code") else None,
        exit_code=(
            row.get("exit_code")
            if isinstance(row.get("exit_code"), int) and not isinstance(row.get("exit_code"), bool)
            else None
        ),
        retryable=retryable if isinstance(retryable, bool) else None,
        retry_disposition=(
            retry_disposition if retry_disposition in RETRY_DISPOSITIONS else None
        ),
        agent_disposition=(
            agent_disposition if agent_disposition in AGENT_DISPOSITIONS else None
        ),
        diagnostic=_diagnostic_of(row.get("diagnostic_json")),
        evidence_preview=str(row.get("evidence_preview") or ""),
        output_truncated=truncated if isinstance(truncated, bool) else None,
        occurred_at=str(row.get("occurred_at") or ""),
    )


def _diagnostic_of(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    return {
        str(key): item
        for key, item in value.items()
        if key in DIAGNOSTIC_KEYS
    } or None


@dataclass(frozen=True, slots=True)
class TerminationFacts:
    reason: TerminationReason
    secondary_reasons: tuple[TerminationReason, ...] = ()
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class UsageFacts:
    turns_used: int = 0
    turns_limit: int = 0
    tool_calls_used: int = 0
    tool_calls_limit: int = 0
    elapsed_ms: int = 0
    deadline_ms: int = 0

    @classmethod
    def of(cls, snapshot: TerminationSnapshot | None) -> UsageFacts:
        if snapshot is None:
            return cls()
        return cls(
            turns_used=snapshot.turns_used,
            turns_limit=snapshot.turns_limit,
            tool_calls_used=snapshot.tool_calls_used,
            tool_calls_limit=snapshot.tool_calls_limit,
            elapsed_ms=snapshot.elapsed_ms,
            deadline_ms=snapshot.deadline_ms,
        )


@dataclass(frozen=True, slots=True)
class ToolOutcomeCounts:
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    timed_out: int = 0
    denied: int = 0
    cancelled: int = 0
    not_applicable: int = 0
    unknown: int = 0
    failure_layers: Mapping[str, int] = field(default_factory=dict)

    @classmethod
    def of(cls, records: Sequence[ToolCallRecord]) -> ToolOutcomeCounts:
        counts = {
            "SUCCEEDED": 0,
            "FAILED": 0,
            "TIMED_OUT": 0,
            "DENIED": 0,
            "CANCELLED": 0,
            "NOT_APPLICABLE": 0,
            "UNKNOWN": 0,
        }
        layers: dict[str, int] = {"TOOL": 0, "CONTENT": 0}
        for record in records:
            # 迁移前的行 business_status=NULL：按 UNKNOWN 计，不得当作成功。
            status = record.business_status or "UNKNOWN"
            counts[status] = counts.get(status, 0) + 1
            if record.failure_layer in layers:
                layers[record.failure_layer] += 1
        return cls(
            total=len(records),
            succeeded=counts["SUCCEEDED"],
            failed=counts["FAILED"],
            timed_out=counts["TIMED_OUT"],
            denied=counts["DENIED"],
            cancelled=counts["CANCELLED"],
            not_applicable=counts["NOT_APPLICABLE"],
            unknown=counts["UNKNOWN"],
            failure_layers=layers,
        )


@dataclass(frozen=True, slots=True)
class FailureSummary:
    tool: str
    tool_call_id: str
    business_status: str
    failure_layer: str
    error_code: str | None
    exit_code: int | None
    retryable: bool | None
    retry_disposition: str | None
    agent_disposition: str | None
    diagnostic: Mapping[str, Any] | None
    evidence_preview: str
    occurred_at: str

    @classmethod
    def of(cls, record: ToolCallRecord) -> FailureSummary:
        return cls(
            tool=record.tool_name,
            tool_call_id=record.tool_call_id,
            business_status=record.business_status or "UNKNOWN",
            failure_layer=record.failure_layer or "",
            error_code=record.error_code,
            exit_code=record.exit_code,
            retryable=record.retryable,
            retry_disposition=record.retry_disposition,
            agent_disposition=record.agent_disposition,
            diagnostic=record.diagnostic,
            evidence_preview=record.evidence_preview,
            occurred_at=record.occurred_at,
        )

    @classmethod
    def of_evidence(cls, evidence: FailureEvidence | None) -> FailureSummary | None:
        if evidence is None:
            return None
        return cls(
            tool=evidence.tool,
            tool_call_id=evidence.tool_call_id,
            business_status=evidence.status,
            failure_layer=evidence.failure_layer,
            error_code=evidence.code,
            exit_code=evidence.exit_code,
            retryable=evidence.retryable,
            retry_disposition=evidence.retry_disposition,
            agent_disposition=evidence.agent_disposition,
            diagnostic=None,
            evidence_preview=evidence.evidence_preview,
            occurred_at=evidence.occurred_at,
        )


@dataclass(frozen=True, slots=True)
class BusinessFailureFacts:
    failures: tuple[FailureSummary, ...] = ()
    #: Timeline view only; never used to infer the root cause.
    last_failure: FailureSummary | None = None
    #: Frozen by the kernel when NO_PROGRESS was reached.
    trigger_tool_call_id: str | None = None
    source_tool_call_id: str | None = None
    #: Audit row matching the frozen source ID; None means the root cause is unproven.
    decisive_failure: FailureSummary | None = None


@dataclass(frozen=True, slots=True)
class CompletionFacts:
    status: str = "UNKNOWN"
    source: str | None = None


class EvidenceStatus(StrEnum):
    CONFIRMED_FAILURE = "CONFIRMED_FAILURE"
    CONFIRMED_NONE = "CONFIRMED_NONE"
    NOT_OBSERVED = "NOT_OBSERVED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class FinalizationFacts:
    termination: TerminationFacts
    usage: UsageFacts = field(default_factory=UsageFacts)
    tool_outcomes: ToolOutcomeCounts = field(default_factory=ToolOutcomeCounts)
    business_failure_evidence: BusinessFailureFacts = field(default_factory=BusinessFailureFacts)
    evidence_status: str = EvidenceStatus.NOT_OBSERVED
    evidence_incomplete: bool = False
    #: True when this Run still has a write whose outcome was never verified.
    has_unverified_write: bool | None = None
    pending_tool_calls: tuple[dict[str, str], ...] = ()
    completion: CompletionFacts = field(default_factory=CompletionFacts)
    schema_version: int = SCHEMA_VERSION

    def to_payload(self) -> dict[str, Any]:
        failures = self.business_failure_evidence
        return {
            "schema_version": self.schema_version,
            "termination": {
                "reason": str(self.termination.reason),
                "secondary_reasons": [str(reason) for reason in self.termination.secondary_reasons],
                "error_code": self.termination.error_code,
            },
            "usage": {
                "turns_used": self.usage.turns_used,
                "turns_limit": self.usage.turns_limit,
                "tool_calls_used": self.usage.tool_calls_used,
                "tool_calls_limit": self.usage.tool_calls_limit,
                "elapsed_ms": self.usage.elapsed_ms,
                "deadline_ms": self.usage.deadline_ms,
            },
            "tool_outcomes": {
                "total": self.tool_outcomes.total,
                "succeeded": self.tool_outcomes.succeeded,
                "failed": self.tool_outcomes.failed,
                "timed_out": self.tool_outcomes.timed_out,
                "denied": self.tool_outcomes.denied,
                "cancelled": self.tool_outcomes.cancelled,
                "not_applicable": self.tool_outcomes.not_applicable,
                "unknown": self.tool_outcomes.unknown,
                "failure_layers": dict(self.tool_outcomes.failure_layers),
            },
            "business_failure_evidence": {
                "failures": [_failure_payload(item) for item in failures.failures],
                "last_failure": (
                    _failure_payload(failures.last_failure) if failures.last_failure else None
                ),
                "trigger_tool_call_id": failures.trigger_tool_call_id,
                "source_tool_call_id": failures.source_tool_call_id,
                "decisive_failure": (
                    _failure_payload(failures.decisive_failure)
                    if failures.decisive_failure
                    else None
                ),
            },
            "evidence_status": self.evidence_status,
            "evidence_incomplete": self.evidence_incomplete,
            "has_unverified_write": self.has_unverified_write,
            "pending_tool_calls": [dict(call) for call in self.pending_tool_calls],
            "completion": {"status": self.completion.status, "source": self.completion.source},
        }


def build_finalization_facts(
    *,
    error_code: str | None,
    snapshot: TerminationSnapshot | None,
    tool_calls: Sequence[ToolCallRecord | Mapping[str, Any]] = (),
    completion: CompletionFacts | None = None,
) -> FinalizationFacts:
    """纯函数：同样的 snapshot + 审计 + 契约结果 → 同样的 facts。"""
    records = tuple(
        item if isinstance(item, ToolCallRecord) else tool_call_record(item) for item in tool_calls
    )
    termination = _termination_facts(error_code=error_code, snapshot=snapshot)
    failures = tuple(record for record in records if record.confirmed_failure)
    summaries = tuple(FailureSummary.of(record) for record in failures)
    last_failure = summaries[-1] if summaries else None
    snapshot_evidence = snapshot.failure_state.last_failure if snapshot is not None else None
    snapshot_failure = (
        snapshot_evidence
        if snapshot_evidence is not None
        and snapshot_evidence.status in FAILURE_STATUSES
        and snapshot_evidence.failure_layer in FAILURE_LAYERS
        else None
    )
    if last_failure is None and snapshot_failure is not None:
        last_failure = FailureSummary.of_evidence(snapshot_failure)
    trigger_id = source_id = None
    decisive = None
    if termination.reason in {
        TerminationReason.NO_PROGRESS,
        TerminationReason.TOOL_FAILURE,
    } and snapshot is not None:
        trigger_id = snapshot.failure_state.no_progress_trigger_call_id or None
        source_id = snapshot.failure_state.no_progress_source_call_id or None
        if source_id:
            decisive = next(
                (item for item in summaries if item.tool_call_id == source_id), None
            )
            if decisive is None and last_failure is not None:
                # Audit unavailable/missing: only the frozen evidence may stand in,
                # and only when it is the frozen source itself.
                if last_failure.tool_call_id == source_id:
                    decisive = last_failure
    return FinalizationFacts(
        termination=termination,
        usage=UsageFacts.of(snapshot),
        tool_outcomes=ToolOutcomeCounts.of(records),
        business_failure_evidence=BusinessFailureFacts(
            failures=summaries,
            last_failure=last_failure,
            trigger_tool_call_id=trigger_id,
            source_tool_call_id=source_id,
            decisive_failure=decisive,
        ),
        evidence_status=_evidence_status(records, bool(failures) or snapshot_failure is not None),
        evidence_incomplete=any(record.output_truncated is True for record in records),
        has_unverified_write=(
            bool(
                snapshot.failure_state.unknown_write_targets
                or snapshot.failure_state.unknown_write_operations
            )
            if snapshot is not None
            else None
        ),
        pending_tool_calls=(
            tuple(call.as_payload() for call in snapshot.pending_tool_calls)
            if snapshot is not None
            else ()
        ),
        completion=completion or CompletionFacts(),
    )


def _termination_facts(
    *, error_code: str | None, snapshot: TerminationSnapshot | None
) -> TerminationFacts:
    reason = (
        snapshot.reason
        if snapshot is not None and snapshot.reason is not None
        else reason_for_error_code(error_code)
    )
    secondary: list[TerminationReason] = []
    if snapshot is not None:
        budgets = _budget_reasons(snapshot)
        for candidate in budgets:
            if candidate is not reason:
                secondary.append(candidate)
    return TerminationFacts(
        reason=reason,
        secondary_reasons=tuple(secondary),
        error_code=error_code,
    )


def _budget_reasons(snapshot: TerminationSnapshot) -> tuple[TerminationReason, ...]:
    """预算/期限的实际命中集合：只从计数器与快照状态推导，不依赖单个 bool。"""
    reasons: list[TerminationReason] = []
    if snapshot.turns_limit and snapshot.turns_used >= snapshot.turns_limit:
        reasons.append(TerminationReason.TURN_BUDGET)
    if snapshot.tool_calls_limit and snapshot.tool_calls_used >= snapshot.tool_calls_limit:
        reasons.append(TerminationReason.TOOL_BUDGET)
    if snapshot.status == "BUDGET_EXCEEDED" and not reasons:
        reasons.append(TerminationReason.TURN_BUDGET)
    return tuple(reasons)


def _evidence_status(records: Sequence[ToolCallRecord], has_confirmed_failure: bool) -> str:
    if has_confirmed_failure:
        return EvidenceStatus.CONFIRMED_FAILURE
    if not records:
        return EvidenceStatus.NOT_OBSERVED
    business = [record for record in records if record.business_status != "NOT_APPLICABLE"]
    if not business:
        return EvidenceStatus.NOT_OBSERVED
    if any(
        record.business_status in (None, "UNKNOWN", "CANCELLED")
        or record.output_truncated is True
        for record in business
    ):
        return EvidenceStatus.UNKNOWN
    return EvidenceStatus.CONFIRMED_NONE


def _failure_payload(summary: FailureSummary) -> dict[str, Any]:
    return {
        "tool": summary.tool,
        "tool_call_id": summary.tool_call_id,
        "business_status": summary.business_status,
        "failure_layer": summary.failure_layer,
        "error_code": summary.error_code,
        "exit_code": summary.exit_code,
        "retryable": summary.retryable,
        "retry_disposition": summary.retry_disposition,
        "agent_disposition": summary.agent_disposition,
        "diagnostic": dict(summary.diagnostic) if summary.diagnostic else None,
        "evidence_preview": summary.evidence_preview,
        "occurred_at": summary.occurred_at,
    }
