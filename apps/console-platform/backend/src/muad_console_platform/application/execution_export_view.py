"""Build the report view from a collected execution bundle.

The view is deliberately a plain dict with a stable schema: JSON rendering, Markdown
summary and CSV all consume the same shape. Diagnostic fields stay raw; the renderer
is responsible for masking credential-shaped keys before writing the artifact.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from ..infrastructure.repositories.execution_export_repository import (
    ExecutionExportBundle,
    ExportSection,
)
from .audit_query_service import format_console_time
from .execution_diagnosis import Diagnosis, service_for

SCHEMA_VERSION = 1
SUPPLEMENTAL_SECTIONS = (
    "runtime.run_continuation",
    "runtime.tool_result_inbox",
    "runtime.run_submission",
    "runtime.run_interrupt",
    "runtime.tool_control_outbox",
    "runtime.artifact",
)
REPORT_DATA_POLICY = {
    "diagnostic_fields": "raw",
    "credentials": "masked",
    "conversation_text": "included",
    "admin_only": True,
}


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _time_text(value: Any) -> str | None:
    parsed = _parse_time(value)
    return format_console_time(parsed) if parsed else (str(value) if value else None)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _section_rows(bundle: ExecutionExportBundle, name: str) -> tuple[dict[str, Any], ...]:
    section = bundle.sections.get(name)
    return section.rows if isinstance(section, ExportSection) and section.status == "ok" else ()


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _duration_ms(start: Any, end: Any) -> int | None:
    first, last = _parse_time(start), _parse_time(end)
    if first is None or last is None:
        return None
    return max(0, int((last - first).total_seconds() * 1000))


def _summary(bundle: ExecutionExportBundle, diagnosis: Diagnosis) -> dict[str, Any]:
    record = bundle.record
    events = _section_rows(bundle, "runtime.canonical_event")
    models = _section_rows(bundle, "runtime.model_invocation_audit")
    tools = _section_rows(bundle, "runtime.tool_call_audit")
    egress = _section_rows(bundle, "runtime.egress_audit")
    operations = _section_rows(bundle, "runtime.tool_operation")
    tasks = _section_rows(bundle, "task.task_execution")
    start = _parse_time(record.get("start_time"))
    end = _parse_time(record.get("end_time") or record.get("finished_at"))
    deadline = _parse_time(record.get("deadline_at"))
    timing: dict[str, Any] = {}
    if start and end:
        timing["elapsed_seconds"] = round((end - start).total_seconds(), 3)
    queued = _parse_time(record.get("queued_at"))
    if queued and start:
        timing["queue_wait_ms"] = max(0, int((start - queued).total_seconds() * 1000))
    if deadline and end:
        gap = (deadline - end).total_seconds()
        timing["seconds_before_absolute_deadline"] = round(gap, 3)
        timing["ended_before_absolute_deadline"] = gap > 0
    slow: list[dict[str, Any]] = []
    for row in models:
        latency = _int(row.get("latency_ms"))
        if latency is not None:
            slow.append(
                {
                    "node_type": "MODEL_CALL",
                    "label": f"{row.get('provider')}/{row.get('model')}",
                    "duration_ms": latency,
                    "status": row.get("status"),
                    "error_code": row.get("error_code"),
                }
            )
    for row in tools:
        latency = _int(row.get("latency_ms"))
        if latency is not None:
            slow.append(
                {
                    "node_type": "TOOL_CALL",
                    "label": str(row.get("tool_name") or ""),
                    "duration_ms": latency,
                    "status": row.get("status"),
                    "error_code": row.get("error_code"),
                }
            )
    for row in egress:
        latency = _int(row.get("latency_ms"))
        if latency is not None:
            slow.append(
                {
                    "node_type": "EGRESS_CALL",
                    "label": str(row.get("target") or ""),
                    "duration_ms": latency,
                    "status": row.get("result_status"),
                    "error_code": row.get("error_code"),
                }
            )
    slow.sort(key=lambda item: item["duration_ms"], reverse=True)
    return {
        "event_count": len(events),
        "model_call_count": len(models),
        "tool_call_count": len(tools),
        "egress_call_count": len(egress),
        "operation_count": len(operations),
        "task_count": len(tasks),
        "failure_count": len(diagnosis.contributing_failures) + (1 if diagnosis.primary_failure else 0),
        "retry_count": sum(1 for row in models if row.get("status") == "RETRY"),
        "total_model_latency_ms": sum(_int(row.get("latency_ms")) or 0 for row in models),
        "total_tool_latency_ms": sum(_int(row.get("latency_ms")) or 0 for row in tools),
        "timing": timing,
        "slowest_steps": slow[:5],
    }


def _execution(bundle: ExecutionExportBundle) -> dict[str, Any]:
    record = bundle.record
    kind = bundle.kind
    task = kind == "TASK"
    snapshot_rows = _section_rows(bundle, "runtime.runtime_snapshot")
    snapshot = None
    if task:
        snapshot_value = _mapping(record.get("execution_snapshot_json"))
        if snapshot_value:
            agent = _mapping(snapshot_value.get("agent"))
            model = _mapping(snapshot_value.get("model"))
            skills = _list(snapshot_value.get("skills"))
            snapshot = {
                "schema_version": snapshot_value.get("schema_version"),
                "hash": record.get("snapshot_hash"),
                "agent_revision": agent.get("revision"),
                "model_id": model.get("id"),
                "model_revision": model.get("revision"),
                "skills": skills,
                "prompt_template_version": snapshot_value.get("prompt_template_version"),
                "mcp_catalog_revision": snapshot_value.get("mcp_catalog_revision"),
                "mcp_catalog_hash": snapshot_value.get("mcp_catalog_hash"),
            }
    elif snapshot_rows:
        row = snapshot_rows[0]
        agent = _mapping(row.get("agent_json"))
        model = _mapping(row.get("model_json"))
        skills = _list(row.get("skill_catalog_json"))
        mcp = _mapping(row.get("mcp_catalog_json"))
        snapshot = {
            "schema_version": row.get("schema_version"),
            "hash": row.get("content_hash"),
            "agent_revision": row.get("agent_revision") or agent.get("revision"),
            "model_id": model.get("model_id") or model.get("id"),
            "model_revision": row.get("model_revision") or model.get("revision"),
            "skills": skills,
            "prompt_template_version": row.get("prompt_template_version"),
            "mcp_catalog_revision": mcp.get("revision"),
            "mcp_catalog_hash": mcp.get("hash"),
        }
    start = record.get("start_time")
    end = record.get("end_time") or record.get("finished_at")
    return {
        "kind": kind,
        "id": str(record.get("id")),
        "status": record.get("status"),
        "outcome": None,
        "error_code": record.get("error_code"),
        "error_message": record.get("error_message"),
        "trace_id": record.get("trace_id"),
        "run_id": str(record.get("id"))
        if kind == "RUN"
        else (str(record.get("source_run_id")) if record.get("source_run_id") else None),
        "task_id": str(record.get("id")) if task else None,
        "start_time": _time_text(start),
        "end_time": _time_text(end),
        "duration_ms": _duration_ms(start, end),
        "deadline_at": _time_text(record.get("deadline_at")),
        "agent": {"id": str(record.get("agent_id")) if record.get("agent_id") else None, "name": None},
        "actor": {
            "id": str(record.get("user_id") or record.get("actor_user_id") or "") or None,
            "name": None,
        },
        "attempt": record.get("attempt") if task else None,
        "max_attempts": record.get("max_attempts") if task else None,
        "skill_id": str(record.get("skill_id")) if task and record.get("skill_id") else None,
        "intent_key": record.get("intent_key") if task else None,
        "source_run_id": str(record.get("source_run_id")) if task and record.get("source_run_id") else None,
        "delivery_status": record.get("delivery_status") if task else None,
        "input_text": record.get("input_text") if kind == "RUN" else None,
        "input": record.get("input_json") if task else None,
        "result": record.get("result_json") if task else None,
        "snapshot": snapshot,
    }


def _timeline(bundle: ExecutionExportBundle) -> list[dict[str, Any]]:
    rows = _section_rows(bundle, "runtime.canonical_event")
    events: list[dict[str, Any]] = []
    previous = None
    for row in rows:
        payload = _mapping(row.get("payload_json"))
        current = _parse_time(row.get("create_time"))
        elapsed = None
        interval = None
        origin = _parse_time(bundle.record.get("start_time"))
        if origin and current:
            elapsed = max(0, int((current - origin).total_seconds() * 1000))
        if previous and current:
            interval = max(0, int((current - previous).total_seconds() * 1000))
        previous = current
        text = payload.get("text") if isinstance(payload.get("text"), str) else None
        events.append(
            {
                "seq": row.get("seq"),
                "event_type": row.get("event_type"),
                "create_time": _time_text(row.get("create_time")),
                "elapsed_ms": elapsed,
                "interval_ms": interval,
                "status": payload.get("status") or payload.get("terminal_status"),
                "error_code": payload.get("error_code"),
                "service": service_for(
                    "EVENT",
                    str(payload.get("error_code") or "") or None,
                    has_task=bool(payload.get("task_id")),
                ),
                "tool_call_id": payload.get("tool_call_id"),
                "task_id": payload.get("task_id"),
                "operation_id": payload.get("operation_id"),
                "has_artifact": bool(row.get("artifact_id")),
                "text": text,
                "payload": dict(payload),
            }
        )
    return events


def _model_calls(bundle: ExecutionExportBundle) -> list[dict[str, Any]]:
    return [
        {
            "audit_id": str(row.get("id")),
            "occurred_at": _time_text(row.get("create_time")),
            "provider": row.get("provider"),
            "model": row.get("model"),
            "attempt": row.get("attempt"),
            "status": row.get("status"),
            "retry_reason": row.get("retry_reason"),
            "error_code": row.get("error_code"),
            "input_tokens": row.get("input_tokens"),
            "output_tokens": row.get("output_tokens"),
            "latency_ms": row.get("latency_ms"),
        }
        for row in _section_rows(bundle, "runtime.model_invocation_audit")
    ]


def _tool_calls(bundle: ExecutionExportBundle) -> list[dict[str, Any]]:
    return [
        {
            "audit_id": str(row.get("id")),
            "occurred_at": _time_text(row.get("create_time")),
            "tool_call_id": row.get("tool_call_id"),
            "tool_name": row.get("tool_name"),
            "tool_kind": row.get("tool_kind"),
            "status": row.get("status"),
            "error_code": row.get("error_code"),
            "error_message": row.get("error_message"),
            "latency_ms": row.get("latency_ms"),
            "task_id": str(row.get("task_id")) if row.get("task_id") else None,
            "arguments": row.get("args_preview_json"),
        }
        for row in _section_rows(bundle, "runtime.tool_call_audit")
    ]


def _egress_calls(bundle: ExecutionExportBundle) -> list[dict[str, Any]]:
    return [
        {
            "audit_id": str(row.get("id")),
            "occurred_at": _time_text(row.get("create_time")),
            "target_type": row.get("target_type"),
            "target": row.get("target"),
            "adapter_key": row.get("adapter_key"),
            "platform_id": str(row.get("platform_id")) if row.get("platform_id") else None,
            "method": row.get("method"),
            "operation": row.get("operation"),
            "policy_decision": row.get("policy_decision"),
            "status_code": row.get("status_code"),
            "result_status": row.get("result_status"),
            "error_code": row.get("error_code"),
            "latency_ms": row.get("latency_ms"),
        }
        for row in _section_rows(bundle, "runtime.egress_audit")
    ]


def _operations(bundle: ExecutionExportBundle) -> list[dict[str, Any]]:
    return [
        {
            "operation_id": str(row.get("id")),
            "source_tool_call_id": row.get("source_tool_call_id"),
            "completion_mode": row.get("completion_mode"),
            "status": row.get("status"),
            "error_phase": row.get("error_phase"),
            "error_code": row.get("error_code"),
            "task_id": str(row.get("task_id")) if row.get("task_id") else None,
            "submitted_at": _time_text(row.get("submitted_at")),
            "completed_at": _time_text(row.get("completed_at")),
        }
        for row in _section_rows(bundle, "runtime.tool_operation")
    ]


def _tasks(bundle: ExecutionExportBundle) -> list[dict[str, Any]]:
    events_by_task: dict[str, list[dict[str, Any]]] = {}
    for name in ("task.task_event", "task.task_event.children"):
        for row in _section_rows(bundle, name):
            task_id = str(row.get("task_id"))
            events_by_task.setdefault(task_id, []).append(
                {
                    "seq": row.get("seq"),
                    "event_type": row.get("event_type"),
                    "create_time": _time_text(row.get("create_time")),
                    "trace_id": row.get("trace_id"),
                    "payload": row.get("payload_json"),
                }
            )
    tasks: list[dict[str, Any]] = []
    for row in _section_rows(bundle, "task.task_execution"):
        task_id = str(row.get("id"))
        tasks.append(
            {
                "task_id": task_id,
                "parent_id": str(row.get("parent_id")) if row.get("parent_id") else None,
                "root_id": str(row.get("root_id")) if row.get("root_id") else None,
                "status": row.get("status"),
                "error_code": row.get("error_code"),
                "error_message": row.get("error_message"),
                "attempt": row.get("attempt"),
                "max_attempts": row.get("max_attempts"),
                "intent_key": row.get("intent_key"),
                "skill_id": str(row.get("skill_id")) if row.get("skill_id") else None,
                "source_run_id": str(row.get("source_run_id")) if row.get("source_run_id") else None,
                "deadline_at": _time_text(row.get("deadline_at")),
                "started_at": _time_text(row.get("started_at")),
                "finished_at": _time_text(row.get("finished_at")),
                "delivery_status": row.get("delivery_status"),
                "events": events_by_task.get(task_id, []),
            }
        )
    return tasks


def _links(
    bundle: ExecutionExportBundle, timeline: list[dict[str, Any]], tasks: list[dict[str, Any]]
) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    root = f"{bundle.kind.lower()}:{bundle.record.get('id')}"
    for event in timeline:
        event_id = f"event:{event['seq']}"
        links.append({"from": root, "to": event_id, "type": "HAS_EVENT"})
        payload = _mapping(event.get("payload"))
        if payload.get("model_audit_id"):
            links.append(
                {"from": event_id, "to": f"model_call:{payload['model_audit_id']}", "type": "MODEL_CALL"}
            )
        if payload.get("tool_call_id"):
            links.append(
                {"from": event_id, "to": f"tool_call:{payload['tool_call_id']}", "type": "TOOL_CALL"}
            )
        if payload.get("operation_id"):
            links.append(
                {"from": event_id, "to": f"operation:{payload['operation_id']}", "type": "OPERATION"}
            )
        if payload.get("task_id"):
            links.append({"from": event_id, "to": f"task:{payload['task_id']}", "type": "SPAWNED_TASK"})
    for operation in _operations(bundle):
        if operation["task_id"]:
            links.append(
                {
                    "from": f"operation:{operation['operation_id']}",
                    "to": f"task:{operation['task_id']}",
                    "type": "SUBMITTED_TASK",
                }
            )
    for task in tasks:
        if task.get("source_run_id"):
            links.append(
                {
                    "from": f"run:{task['source_run_id']}",
                    "to": f"task:{task['task_id']}",
                    "type": "SPAWNED_TASK",
                }
            )
        if task.get("parent_id"):
            links.append(
                {"from": f"task:{task['parent_id']}", "to": f"task:{task['task_id']}", "type": "CHILD_TASK"}
            )
        for event in task.get("events", []):
            links.append(
                {
                    "from": f"task:{task['task_id']}",
                    "to": f"task_event:{task['task_id']}:{event['seq']}",
                    "type": "HAS_EVENT",
                }
            )
    return links


def _supplemental(bundle: ExecutionExportBundle) -> dict[str, Any]:
    return {
        "continuations": [dict(row) for row in _section_rows(bundle, "runtime.run_continuation")],
        "tool_result_inbox": [dict(row) for row in _section_rows(bundle, "runtime.tool_result_inbox")],
        "submissions": [dict(row) for row in _section_rows(bundle, "runtime.run_submission")],
        "interrupts": [dict(row) for row in _section_rows(bundle, "runtime.run_interrupt")],
        "control_outbox": [dict(row) for row in _section_rows(bundle, "runtime.tool_control_outbox")],
        "artifacts": [dict(row) for row in _section_rows(bundle, "runtime.artifact")],
        "conversation": next((dict(row) for row in _section_rows(bundle, "runtime.conversation")), None),
    }


def _data_quality(bundle: ExecutionExportBundle) -> dict[str, Any]:
    return {
        "complete": not bundle.warnings,
        "warnings": [dict(item) for item in bundle.warnings],
        "section_status": {name: section.status for name, section in bundle.sections.items()},
    }


def build_report(
    bundle: ExecutionExportBundle, diagnosis: Diagnosis, *, locale: str = "zh-CN"
) -> dict[str, Any]:
    timeline = _timeline(bundle)
    tasks = _tasks(bundle)
    execution = _execution(bundle)
    execution["outcome"] = diagnosis.outcome
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": format_console_time(datetime.now(UTC)),
        "locale": locale,
        "execution": execution,
        "summary": _summary(bundle, diagnosis),
        "diagnosis": diagnosis.as_dict(),
        "timeline": timeline,
        "model_calls": _model_calls(bundle),
        "tool_calls": _tool_calls(bundle),
        "egress_calls": _egress_calls(bundle),
        "operations": _operations(bundle),
        "tasks": tasks,
        "links": _links(bundle, timeline, tasks),
        "supplemental": _supplemental(bundle),
        "truncation": dict(bundle.truncation),
        "data_quality": _data_quality(bundle),
        "database_snapshot": dict(bundle.database_snapshot),
        "data_policy": dict(REPORT_DATA_POLICY),
    }
