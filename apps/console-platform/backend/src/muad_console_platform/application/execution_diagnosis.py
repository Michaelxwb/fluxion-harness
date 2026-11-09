"""Turn one execution collection into a failure-first analysis summary.

The collector returns raw stored rows; this module only classifies failure candidates,
attributes a primary service and produces log lookup keys. It never claims an absolute
root cause: the execution's own ``error_code`` stays authoritative and ``primary_failure``
records the basis used to pick it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ..infrastructure.repositories.execution_export_repository import (
    ExecutionExportBundle,
    ExportSection,
)

FAILURE_STATUSES = frozenset({"ERROR", "FAILED", "DENY", "DENIED"})
RETRY_STATUSES = frozenset({"RETRY"})
TERMINAL_EVENT_TYPES = frozenset({"RUN_FAILED", "TOOL_SUBMISSION_FAILED"})
RUN_LEVEL_CODES = frozenset(
    {
        "RUN_DEADLINE_EXCEEDED",
        "RUN_QUEUE_TIMEOUT",
        "RUN_CAPACITY_EXCEEDED",
        "RUN_ABANDONED",
        "RUN_BUDGET_EXCEEDED",
    }
)
RUNTERMINAL_STATUSES = frozenset({"FAILED", "CANCELLED"})

_SERVICE_BY_PREFIX = (
    (("SKILL_", "SANDBOX_", "WORKSPACE_", "BATCH_", "TASK_"), "muad-agent-worker"),
    (("CHANNEL_", "DELIVERY_", "ATTACHMENT_", "BOT_", "BIND_"), "muad-im-gateway"),
    (("MODEL_", "MCP_", "MEMORY_", "LEARNING_", "RUN_", "CONVERSATION_", "CURSOR_"), "muad-agent-runtime"),
)


@dataclass(frozen=True)
class FailureCandidate:
    node_type: str
    node_id: str
    service: str
    basis: str
    seq: int | None = None
    occurred_at: str | None = None
    status: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    is_retry: bool = False
    related_ids: Mapping[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "node_type": self.node_type,
            "node_id": self.node_id,
            "service": self.service,
            "basis": self.basis,
            "seq": self.seq,
            "occurred_at": self.occurred_at,
            "status": self.status,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "is_retry": self.is_retry,
            "related_ids": dict(self.related_ids),
        }


@dataclass(frozen=True)
class ServiceRef:
    service: str
    role: str

    def as_dict(self) -> dict[str, str]:
        return {"service": self.service, "role": self.role}


@dataclass(frozen=True)
class LogLookup:
    services: tuple[ServiceRef, ...]
    keys: Mapping[str, str]
    paths: tuple[str, ...]
    commands: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "services": [item.as_dict() for item in self.services],
            "keys": dict(self.keys),
            "paths": list(self.paths),
            "commands": list(self.commands),
        }


@dataclass(frozen=True)
class Insight:
    code: str
    params: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "params": dict(self.params)}


@dataclass(frozen=True)
class Diagnosis:
    outcome: str
    primary_failure: FailureCandidate | None
    contributing_failures: tuple[FailureCandidate, ...]
    services: tuple[ServiceRef, ...]
    log_lookup: LogLookup
    insights: tuple[Insight, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "primary_failure": self.primary_failure.as_dict() if self.primary_failure else None,
            "contributing_failures": [item.as_dict() for item in self.contributing_failures],
            "services": [item.as_dict() for item in self.services],
            "log_lookup": self.log_lookup.as_dict(),
            "insights": [item.as_dict() for item in self.insights],
        }


def outcome_for(status: str | None) -> str:
    if status == "COMPLETED":
        return "SUCCEEDED"
    if status == "FAILED":
        return "FAILED"
    if status == "CANCELLED":
        return "CANCELLED"
    if status in {"CREATED", "RUNNING", "WAITING_TOOL", "WAITING_INPUT", "QUEUED", "WAITING"}:
        return "IN_PROGRESS"
    return "UNKNOWN"


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _sort_key(candidate: FailureCandidate) -> tuple[str, int]:
    parsed = _time(candidate.occurred_at)
    return (parsed.isoformat() if parsed else "", candidate.seq or 0)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _rows(bundle: ExecutionExportBundle, name: str) -> tuple[dict[str, Any], ...]:
    section = bundle.sections.get(name)
    return section.rows if isinstance(section, ExportSection) and section.status == "ok" else ()


def _service_for_error(error_code: str | None) -> str | None:
    if not error_code:
        return None
    for prefixes, service in _SERVICE_BY_PREFIX:
        if error_code.startswith(prefixes):
            return service
    return None


def service_for(node_type: str, error_code: str | None, *, has_task: bool = False) -> str:
    by_error = _service_for_error(error_code)
    if by_error:
        return by_error
    if node_type in {"TASK", "TASK_EVENT"}:
        return "muad-agent-worker"
    if node_type == "TOOL_CALL" and has_task:
        return "muad-agent-worker"
    return "muad-agent-runtime"


def _candidate(
    *,
    node_type: str,
    node_id: Any,
    basis: str,
    status: Any = None,
    error_code: Any = None,
    error_message: Any = None,
    occurred_at: Any = None,
    seq: Any = None,
    related: Mapping[str, Any] | None = None,
    is_retry: bool = False,
) -> FailureCandidate:
    related_ids = {str(key): str(value) for key, value in (related or {}).items() if value not in (None, "")}
    status_text = str(status) if status not in (None, "") else None
    code_text = str(error_code) if error_code not in (None, "") else None
    has_task = "task_id" in related_ids
    return FailureCandidate(
        node_type=node_type,
        node_id=str(node_id),
        service=service_for(node_type, code_text, has_task=has_task),
        basis=basis,
        seq=int(seq) if isinstance(seq, int) or (isinstance(seq, str) and seq.isdigit()) else None,
        occurred_at=str(occurred_at) if occurred_at not in (None, "") else None,
        status=status_text,
        error_code=code_text,
        error_message=str(error_message) if error_message not in (None, "") else None,
        is_retry=is_retry,
        related_ids=related_ids,
    )


def _event_candidates(bundle: ExecutionExportBundle) -> list[FailureCandidate]:
    found: list[FailureCandidate] = []
    for row in _rows(bundle, "runtime.canonical_event"):
        payload = _mapping(row.get("payload_json"))
        status = payload.get("status") or payload.get("terminal_status")
        code = payload.get("error_code")
        failed = (
            row.get("event_type") in TERMINAL_EVENT_TYPES or str(status) in FAILURE_STATUSES or bool(code)
        )
        if not failed:
            continue
        found.append(
            _candidate(
                node_type="EVENT",
                node_id=row.get("seq"),
                basis="EARLIEST_TERMINAL_FAILURE",
                status=status,
                error_code=code,
                error_message=payload.get("error_message"),
                occurred_at=row.get("create_time"),
                seq=row.get("seq"),
                related={
                    "tool_call_id": payload.get("tool_call_id"),
                    "task_id": payload.get("task_id"),
                    "operation_id": payload.get("operation_id"),
                },
            )
        )
    return found


def _model_candidates(bundle: ExecutionExportBundle) -> list[FailureCandidate]:
    found: list[FailureCandidate] = []
    for row in _rows(bundle, "runtime.model_invocation_audit"):
        status = row.get("status")
        retry = str(status) in RETRY_STATUSES
        if status not in FAILURE_STATUSES and not retry:
            continue
        found.append(
            _candidate(
                node_type="MODEL_CALL",
                node_id=row.get("id"),
                basis="EARLIEST_TERMINAL_FAILURE",
                status=status,
                error_code=row.get("error_code"),
                occurred_at=row.get("create_time"),
                related={"task_id": row.get("task_id")},
                is_retry=retry,
            )
        )
    return found


def _tool_candidates(bundle: ExecutionExportBundle) -> list[FailureCandidate]:
    found: list[FailureCandidate] = []
    for row in _rows(bundle, "runtime.tool_call_audit"):
        if str(row.get("status")) not in FAILURE_STATUSES:
            continue
        found.append(
            _candidate(
                node_type="TOOL_CALL",
                node_id=row.get("id"),
                basis="EARLIEST_TERMINAL_FAILURE",
                status=row.get("status"),
                error_code=row.get("error_code"),
                error_message=row.get("error_message"),
                occurred_at=row.get("create_time") or row.get("start_time"),
                related={
                    "tool_call_id": row.get("tool_call_id"),
                    "task_id": row.get("task_id"),
                },
            )
        )
    return found


def _egress_candidates(bundle: ExecutionExportBundle) -> list[FailureCandidate]:
    found: list[FailureCandidate] = []
    for row in _rows(bundle, "runtime.egress_audit"):
        if str(row.get("result_status")) not in FAILURE_STATUSES:
            continue
        found.append(
            _candidate(
                node_type="EGRESS_CALL",
                node_id=row.get("id"),
                basis="EARLIEST_TERMINAL_FAILURE",
                status=row.get("result_status"),
                error_code=row.get("error_code"),
                occurred_at=row.get("create_time"),
                related={"task_id": row.get("task_id")},
            )
        )
    return found


def _operation_candidates(bundle: ExecutionExportBundle) -> list[FailureCandidate]:
    found: list[FailureCandidate] = []
    for row in _rows(bundle, "runtime.tool_operation"):
        if str(row.get("status")) != "FAILED":
            continue
        found.append(
            _candidate(
                node_type="OPERATION",
                node_id=row.get("id"),
                basis="EARLIEST_TERMINAL_FAILURE",
                status=row.get("status"),
                error_code=row.get("error_code"),
                occurred_at=row.get("completed_at") or row.get("create_time"),
                related={
                    "tool_call_id": row.get("source_tool_call_id"),
                    "task_id": row.get("task_id"),
                    "operation_id": row.get("id"),
                },
            )
        )
    return found


def _task_candidates(bundle: ExecutionExportBundle) -> list[FailureCandidate]:
    found: list[FailureCandidate] = []
    for row in _rows(bundle, "task.task_execution"):
        if str(row.get("status")) != "FAILED":
            continue
        found.append(
            _candidate(
                node_type="TASK",
                node_id=row.get("id"),
                basis="TASK_ERROR_CODE",
                status=row.get("status"),
                error_code=row.get("error_code"),
                error_message=row.get("error_message"),
                occurred_at=row.get("finished_at") or row.get("update_time"),
                related={"task_id": row.get("id")},
            )
        )
    for row in _rows(bundle, "task.task_event"):
        payload = _mapping(row.get("payload_json"))
        if row.get("event_type") not in {"FAILED", "DELIVERY_FAILED"} and not payload.get("error_code"):
            continue
        found.append(
            _candidate(
                node_type="TASK_EVENT",
                node_id=row.get("id"),
                basis="EARLIEST_TERMINAL_FAILURE",
                status=row.get("event_type"),
                error_code=payload.get("error_code"),
                error_message=payload.get("error_message"),
                occurred_at=row.get("create_time"),
                seq=row.get("seq"),
                related={"task_id": row.get("task_id")},
            )
        )
    return found


def _record_candidate(bundle: ExecutionExportBundle) -> FailureCandidate | None:
    record = bundle.record
    if str(record.get("status")) != "FAILED":
        return None
    node_type = "TASK" if bundle.kind == "TASK" else "RUN"
    related = (
        {"run_id": record.get("id")}
        if bundle.kind == "RUN"
        else {"task_id": record.get("id"), "run_id": record.get("source_run_id")}
    )
    return _candidate(
        node_type=node_type,
        node_id=record.get("id"),
        basis="TASK_ERROR_CODE" if bundle.kind == "TASK" else "RUN_ERROR_CODE",
        status=record.get("status"),
        error_code=record.get("error_code"),
        error_message=record.get("error_message"),
        occurred_at=record.get("finished_at") or record.get("end_time") or record.get("update_time"),
        related=related,
    )


def collect_failure_candidates(bundle: ExecutionExportBundle) -> tuple[FailureCandidate, ...]:
    candidates: list[FailureCandidate] = []
    record = _record_candidate(bundle)
    if record is not None:
        candidates.append(record)
    candidates.extend(_event_candidates(bundle))
    candidates.extend(_model_candidates(bundle))
    candidates.extend(_tool_candidates(bundle))
    candidates.extend(_egress_candidates(bundle))
    candidates.extend(_operation_candidates(bundle))
    candidates.extend(_task_candidates(bundle))

    seen: set[tuple[str, str, str | None]] = set()
    unique: list[FailureCandidate] = []
    for item in sorted(candidates, key=_sort_key):
        key = (item.node_type, item.node_id, item.error_code)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return tuple(unique)


def _primary(
    bundle: ExecutionExportBundle,
    outcome: str,
    candidates: tuple[FailureCandidate, ...],
) -> FailureCandidate | None:
    if outcome != "FAILED":
        return None
    record = bundle.record
    record_code = str(record.get("error_code") or "")
    record_candidate = next(
        (
            item
            for item in candidates
            if item.node_type == ("TASK" if bundle.kind == "TASK" else "RUN")
            and item.basis in {"RUN_ERROR_CODE", "TASK_ERROR_CODE"}
        ),
        None,
    )
    if bundle.kind == "TASK" and record_candidate is not None:
        return record_candidate
    if record_code in RUN_LEVEL_CODES and record_candidate is not None:
        return record_candidate
    terminal = [
        item
        for item in candidates
        if not item.is_retry and item.basis not in {"RUN_ERROR_CODE", "TASK_ERROR_CODE"}
    ]
    if terminal:
        return terminal[0]
    return record_candidate


def _service_refs(candidates: Iterable[FailureCandidate]) -> tuple[ServiceRef, ...]:
    services: list[str] = []
    for item in candidates:
        if item.service not in services:
            services.append(item.service)
    return tuple(
        ServiceRef(service=service, role="PRIMARY" if index == 0 else "RELATED")
        for index, service in enumerate(services)
    )


def _log_lookup(
    bundle: ExecutionExportBundle,
    primary: FailureCandidate | None,
    services: tuple[ServiceRef, ...],
) -> LogLookup:
    record = bundle.record
    keys: dict[str, str] = {}
    if bundle.kind == "TASK":
        keys["task_id"] = str(record.get("id"))
        if record.get("source_run_id"):
            keys["run_id"] = str(record["source_run_id"])
    else:
        keys["run_id"] = str(record.get("id"))
    if record.get("trace_id"):
        keys["trace_id"] = str(record["trace_id"])
    if primary:
        for name in ("task_id", "tool_call_id", "operation_id", "audit_id"):
            value = primary.related_ids.get(name)
            if value:
                keys[name] = value
    paths = tuple(f"LOG_DIR/{item.service}/<YYYY-MM-DD>.log" for item in services)
    primary_key = next(iter(keys.values()), "<execution-id>")
    commands = tuple(f'grep -R -F "{primary_key}" "$LOG_DIR/{item.service}"' for item in services) + tuple(
        f'kubectl -n muad logs deploy/{item.service} --since=2h | grep -F "{primary_key}"'
        for item in services
    )
    return LogLookup(services=services, keys=keys, paths=paths, commands=commands)


def build_diagnosis(bundle: ExecutionExportBundle) -> Diagnosis:
    outcome = outcome_for(str(bundle.record.get("status") or ""))
    candidates = collect_failure_candidates(bundle)
    primary = _primary(bundle, outcome, candidates)
    contributing = tuple(item for item in candidates if item != primary)
    services = _service_refs(candidates)
    insights: list[Insight] = []
    if primary is not None:
        insights.append(
            Insight(
                "PRIMARY_FAILURE",
                {
                    "node_type": primary.node_type,
                    "error_code": primary.error_code or "",
                    "service": primary.service,
                    "basis": primary.basis,
                },
            )
        )
    retries = [item for item in candidates if item.is_retry]
    if retries:
        insights.append(Insight("MODEL_RETRY", {"count": len(retries)}))
    if outcome == "FAILED" and primary is None:
        insights.append(Insight("NO_FAILURE_NODE", {}))
    if outcome == "SUCCEEDED" and not candidates:
        insights.append(Insight("SUCCEEDED_WITHOUT_FAILURES", {}))
    return Diagnosis(
        outcome=outcome,
        primary_failure=primary,
        contributing_failures=contributing,
        services=services,
        log_lookup=_log_lookup(bundle, primary, services),
        insights=tuple(insights),
    )
