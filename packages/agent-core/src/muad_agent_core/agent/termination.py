"""终止快照与失败事实（design §4.3）。

Kernel 只负责记录「为什么停」与「看到过什么失败」；终止原因到错误码的映射、facts 与
用户文案都在 Runtime。这里的对象必须可序列化：`SegmentResult`/`RunnerError` 跨进程内边界
携带同一份快照，Runtime 不依赖 Kernel 进程内 state。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class BudgetKind(StrEnum):
    TURN = "TURN"
    TOOL = "TOOL"
    NONE = "NONE"


class TerminationReason(StrEnum):
    TURN_BUDGET = "TURN_BUDGET"
    TOOL_BUDGET = "TOOL_BUDGET"
    DEADLINE = "DEADLINE"
    NO_PROGRESS = "NO_PROGRESS"
    TOOL_FAILURE = "TOOL_FAILURE"
    CANCELLED = "CANCELLED"
    RUN_ERROR = "RUN_ERROR"


#: Run 终态错误码 → 终止原因。Kernel 不 import API 错误码，保持宿主中立。
_ERROR_REASONS: Mapping[str, TerminationReason] = {
    "RUN_DEADLINE_EXCEEDED": TerminationReason.DEADLINE,
    "RUN_NO_PROGRESS": TerminationReason.NO_PROGRESS,
    "RUN_TOOL_FAILURE_UNRESOLVED": TerminationReason.TOOL_FAILURE,
    "CANCELLED": TerminationReason.CANCELLED,
}


@dataclass(frozen=True, slots=True)
class FailureEvidence:
    """最近观察到的失败事实；`decisive_failure` 只在 NO_PROGRESS 时取它。"""

    tool: str
    tool_call_id: str = ""
    status: str = "FAILED"
    code: str = "TOOL_FAILED"
    message: str = ""
    failure_layer: str = "TOOL"
    exit_code: int | None = None
    retryable: bool = False
    agent_disposition: str = "CONTINUE"
    retry_disposition: str = "ALLOW"
    scope: str = "CALL"
    sent: bool = False
    outcome_known: bool = True
    evidence_status: str = "CONFIRMED"
    retry_after: str | None = None
    security_disposition: str = "NORMAL"
    boundary_kind: str = "NONE"
    stop_confirmed: bool = True
    evidence_preview: str = ""
    #: Verified repair owner from the shared failure mapping, if one was provided.
    action_owner: str | None = None
    #: True/false only when the shared mapping matched; None = no configured explanation.
    hint_known: bool | None = None
    write_targets: frozenset[str] = frozenset()
    write_operation_key: str | None = None
    occurred_at: str = ""

    def as_legacy_dict(self) -> dict[str, str]:
        """宿主既有消费方（worker Task 消息）只读 tool/code/message。"""
        legacy = {"tool": self.tool, "code": self.code, "message": self.message}
        if self.failure_layer:
            legacy["failure_layer"] = self.failure_layer
        return legacy

    def as_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "tool": self.tool,
            "tool_call_id": self.tool_call_id,
            "status": self.status,
            "code": self.code,
            "message": self.message,
            "failure_layer": self.failure_layer,
            "exit_code": self.exit_code,
            "retryable": self.retryable,
            "agent_disposition": self.agent_disposition,
            "retry_disposition": self.retry_disposition,
            "scope": self.scope,
            "sent": self.sent,
            "outcome_known": self.outcome_known,
            "evidence_status": self.evidence_status,
            "retry_after": self.retry_after,
            "security_disposition": self.security_disposition,
            "boundary_kind": self.boundary_kind,
            "stop_confirmed": self.stop_confirmed,
            "evidence_preview": self.evidence_preview,
            "occurred_at": self.occurred_at,
        }
        if self.action_owner is not None:
            payload["action_owner"] = self.action_owner
        if self.hint_known is not None:
            payload["hint_known"] = self.hint_known
        if self.write_targets:
            payload["write_targets"] = sorted(self.write_targets)
        if self.write_operation_key is not None:
            payload["write_operation_key"] = self.write_operation_key
        return payload

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any] | None) -> FailureEvidence | None:
        if not isinstance(payload, Mapping):
            return None
        tool = payload.get("tool")
        if not isinstance(tool, str) or not tool:
            return None
        values: dict[str, Any] = {
            "tool": tool,
            "tool_call_id": str(payload.get("tool_call_id") or ""),
            "status": str(payload.get("status") or "FAILED"),
            "code": str(payload.get("code") or "TOOL_FAILED"),
            "message": str(payload.get("message") or ""),
            "failure_layer": str(payload.get("failure_layer") or "TOOL"),
            "exit_code": _optional_int(payload.get("exit_code")),
            "retryable": payload.get("retryable") is True,
            "agent_disposition": str(payload.get("agent_disposition") or "CONTINUE"),
            "retry_disposition": str(payload.get("retry_disposition") or "ALLOW"),
            "scope": str(payload.get("scope") or "CALL"),
            "sent": payload.get("sent") is True,
            "outcome_known": payload.get("outcome_known") is not False,
            "evidence_status": str(payload.get("evidence_status") or "CONFIRMED"),
            "retry_after": (
                str(payload.get("retry_after"))
                if payload.get("retry_after") is not None
                else None
            ),
            "security_disposition": str(payload.get("security_disposition") or "NORMAL"),
            "boundary_kind": str(payload.get("boundary_kind") or "NONE"),
            "stop_confirmed": payload.get("stop_confirmed") is not False,
            "evidence_preview": str(payload.get("evidence_preview") or ""),
            "occurred_at": str(payload.get("occurred_at") or ""),
        }
        owner = payload.get("action_owner")
        if isinstance(owner, str):
            values["action_owner"] = owner
        known = payload.get("hint_known")
        if isinstance(known, bool):
            values["hint_known"] = known
        targets = payload.get("write_targets")
        if isinstance(targets, list | tuple):
            values["write_targets"] = frozenset(
                item for item in targets if isinstance(item, str)
            )
        operation = payload.get("write_operation_key")
        if isinstance(operation, str):
            values["write_operation_key"] = operation
        return cls(**values)


@dataclass(frozen=True, slots=True)
class FailureState:
    last_failure: FailureEvidence | None = None
    #: A failure has not yet been followed by a successful corrective call.
    failure_pending: bool = False
    identical_failures: Mapping[str, int] = field(default_factory=dict)
    no_progress: bool = False
    blocked_attempts: frozenset[str] = frozenset()
    #: First unresolved failure that put the attempt on the blocked list.
    blocked_attempt_sources: Mapping[str, str] = field(default_factory=dict)
    unknown_write_targets: frozenset[str] = frozenset()
    #: First unresolved failure per bounded target (targets without an operation key).
    unknown_target_sources: Mapping[str, str] = field(default_factory=dict)
    unknown_write_operations: Mapping[str, frozenset[str]] = field(default_factory=dict)
    #: First unresolved failure per host-owned operation key.
    unknown_operation_sources: Mapping[str, str] = field(default_factory=dict)
    reviews: Mapping[str, tuple[int, int]] = field(default_factory=dict)
    #: Frozen when no_progress first becomes true: the call that tripped the limit.
    no_progress_trigger_call_id: str = ""
    #: Business failure the trigger points at; same call for a direct failure, explicit
    #: source for a retry-gate block, empty when the relation cannot be proven.
    no_progress_source_call_id: str = ""

    def as_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "last_failure": self.last_failure.as_payload() if self.last_failure else None,
            "failure_pending": self.failure_pending,
            "identical_failures": dict(self.identical_failures),
            "no_progress": self.no_progress,
            "blocked_attempts": sorted(self.blocked_attempts),
            "unknown_write_targets": sorted(self.unknown_write_targets),
            "unknown_write_operations": {
                key: sorted(v) for key, v in self.unknown_write_operations.items()
            },
            "reviews": {key: list(value) for key, value in self.reviews.items()},
        }
        if self.blocked_attempt_sources:
            payload["blocked_attempt_sources"] = dict(self.blocked_attempt_sources)
        if self.unknown_target_sources:
            payload["unknown_target_sources"] = dict(self.unknown_target_sources)
        if self.unknown_operation_sources:
            payload["unknown_operation_sources"] = dict(self.unknown_operation_sources)
        if self.no_progress_trigger_call_id:
            payload["no_progress_trigger_call_id"] = self.no_progress_trigger_call_id
        if self.no_progress_source_call_id:
            payload["no_progress_source_call_id"] = self.no_progress_source_call_id
        return payload

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any] | None) -> FailureState:
        if not isinstance(payload, Mapping):
            return cls()
        raw_counts = payload.get("identical_failures")
        counts: dict[str, int] = {}
        if isinstance(raw_counts, Mapping):
            for key, value in raw_counts.items():
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    counts[str(key)] = value
        blocked = payload.get("blocked_attempts")
        unknown = payload.get("unknown_write_targets")
        operations = payload.get("unknown_write_operations")
        raw_reviews = payload.get("reviews")
        blocked_set = (
            frozenset(v for v in blocked if isinstance(v, str))
            if isinstance(blocked, list) else frozenset()
        )
        unknown_targets = (
            frozenset(v for v in unknown if isinstance(v, str))
            if isinstance(unknown, list) else frozenset()
        )
        operation_targets = {
            key: frozenset(v for v in targets if isinstance(v, str))
            for key, targets in operations.items()
            if isinstance(key, str) and isinstance(targets, list)
        } if isinstance(operations, Mapping) else {}
        reviews = {}
        if isinstance(raw_reviews, Mapping):
            for key, values in raw_reviews.items():
                if (
                    isinstance(values, list | tuple) and len(values) == 2
                    and all(type(v) is int for v in values)
                ):
                    reviews[str(key)] = (values[0], values[1])
        blocked_sources = payload.get("blocked_attempt_sources")
        target_sources = payload.get("unknown_target_sources")
        operation_sources = payload.get("unknown_operation_sources")
        trigger = payload.get("no_progress_trigger_call_id")
        source = payload.get("no_progress_source_call_id")
        return cls(
            blocked_attempts=blocked_set,
            blocked_attempt_sources=(
                _sources_for(blocked_sources, blocked_set)
                if isinstance(blocked_sources, Mapping) else {}
            ),
            unknown_write_targets=unknown_targets,
            unknown_target_sources=(
                _sources_for(target_sources, unknown_targets)
                if isinstance(target_sources, Mapping) else {}
            ),
            unknown_write_operations=operation_targets,
            unknown_operation_sources=(
                _sources_for(operation_sources, frozenset(operation_targets))
                if isinstance(operation_sources, Mapping) else {}
            ),
            reviews=reviews,
            last_failure=FailureEvidence.from_payload(payload.get("last_failure")),
            failure_pending=payload.get("failure_pending") is True,
            identical_failures=counts,
            no_progress=payload.get("no_progress") is True,
            no_progress_trigger_call_id=trigger if isinstance(trigger, str) else "",
            no_progress_source_call_id=source if isinstance(source, str) else "",
        )


def _sources_for(raw: object, keys: frozenset[str]) -> dict[str, str]:
    """Keep only sources whose referenced attempt/target/operation still exists."""
    if not isinstance(raw, Mapping):
        return {}
    return {
        str(key): source
        for key, source in raw.items()
        if key in keys and isinstance(source, str) and source
    }


@dataclass(frozen=True, slots=True)
class PendingToolCall:
    call_id: str
    tool_name: str

    def as_payload(self) -> dict[str, str]:
        return {"call_id": self.call_id, "tool_name": self.tool_name}


@dataclass(frozen=True, slots=True)
class TerminationSnapshot:
    """Runner → Runtime 的终止事实；也是 `RunnerCheckpoint.failure_state` 的权威形状。"""

    status: str
    budget_kind: BudgetKind = BudgetKind.NONE
    turns_used: int = 0
    turns_limit: int = 0
    tool_calls_used: int = 0
    tool_calls_limit: int = 0
    elapsed_ms: int = 0
    deadline_ms: int = 0
    failure_state: FailureState = field(default_factory=FailureState)
    pending_tool_calls: tuple[PendingToolCall, ...] = ()

    @property
    def reason(self) -> TerminationReason | None:
        if self.status == "NO_PROGRESS":
            return TerminationReason.NO_PROGRESS
        if self.status == "TOOL_FAILURE":
            return TerminationReason.TOOL_FAILURE
        if self.status == "BUDGET_EXCEEDED":
            return (
                TerminationReason.TOOL_BUDGET
                if self.budget_kind is BudgetKind.TOOL
                else TerminationReason.TURN_BUDGET
            )
        return None

    def as_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "budget_kind": str(self.budget_kind),
            "turns_used": self.turns_used,
            "turns_limit": self.turns_limit,
            "tool_calls_used": self.tool_calls_used,
            "tool_calls_limit": self.tool_calls_limit,
            "elapsed_ms": self.elapsed_ms,
            "deadline_ms": self.deadline_ms,
            "failure_state": self.failure_state.as_payload(),
            "pending_tool_calls": [call.as_payload() for call in self.pending_tool_calls],
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any] | None) -> TerminationSnapshot | None:
        if not isinstance(payload, Mapping) or not isinstance(payload.get("status"), str):
            return None
        raw_pending = payload.get("pending_tool_calls")
        pending: list[PendingToolCall] = []
        if isinstance(raw_pending, list):
            for entry in raw_pending:
                if not isinstance(entry, Mapping):
                    continue
                call_id = entry.get("call_id")
                tool_name = entry.get("tool_name")
                if isinstance(call_id, str) and call_id:
                    pending.append(
                        PendingToolCall(call_id=call_id, tool_name=str(tool_name or ""))
                    )
        try:
            budget_kind = BudgetKind(str(payload.get("budget_kind") or BudgetKind.NONE))
        except ValueError:
            budget_kind = BudgetKind.NONE
        return cls(
            status=str(payload["status"]),
            budget_kind=budget_kind,
            turns_used=_optional_int(payload.get("turns_used")) or 0,
            turns_limit=_optional_int(payload.get("turns_limit")) or 0,
            tool_calls_used=_optional_int(payload.get("tool_calls_used")) or 0,
            tool_calls_limit=_optional_int(payload.get("tool_calls_limit")) or 0,
            elapsed_ms=_optional_int(payload.get("elapsed_ms")) or 0,
            deadline_ms=_optional_int(payload.get("deadline_ms")) or 0,
            failure_state=FailureState.from_payload(payload.get("failure_state")),
            pending_tool_calls=tuple(pending),
        )


def reason_for_error_code(error_code: str | None) -> TerminationReason:
    """异常出口没有快照时，用既有错误码还原终止原因（不新增错误码）。"""
    if error_code:
        for prefix, reason in _ERROR_REASONS.items():
            if error_code == prefix:
                return reason
        if error_code == "RUN_BUDGET_EXCEEDED":
            # 快照缺失时无法区分 turn/tool；保留预算语义，不冒充其它原因。
            return TerminationReason.TURN_BUDGET
    return TerminationReason.RUN_ERROR


def decisive_failure(
    snapshot: TerminationSnapshot | None, reason: TerminationReason
) -> FailureEvidence | None:
    """Only an unresolved failure stop claims `last_failure` as the stop cause."""
    if snapshot is None or reason not in {
        TerminationReason.NO_PROGRESS,
        TerminationReason.TOOL_FAILURE,
    }:
        return None
    return snapshot.failure_state.last_failure


def failure_state_from_legacy(last_failure: Mapping[str, Any] | None) -> FailureEvidence | None:
    """旧 checkpoint / 旧消费者的 dict 形状 → FailureEvidence。"""
    if not isinstance(last_failure, Mapping):
        return None
    tool = last_failure.get("tool")
    if not isinstance(tool, str) or not tool:
        return None
    return FailureEvidence(
        tool=tool,
        code=str(last_failure.get("code") or "TOOL_FAILED"),
        message=str(last_failure.get("message") or ""),
    )


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def now_iso() -> str:
    return datetime.now(UTC).isoformat()
