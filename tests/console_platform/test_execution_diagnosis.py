"""TASK-002: failure diagnosis and report view over a collected bundle."""

from __future__ import annotations

import re
import uuid

from muad_console_platform.application.execution_diagnosis import build_diagnosis
from muad_console_platform.application.execution_export_view import build_report
from muad_console_platform.infrastructure.repositories.execution_export_repository import (
    ExecutionExportBundle,
    ExportSection,
)

RUN_ID = str(uuid.uuid4())
TASK_ID = str(uuid.uuid4())
AGENT_ID = str(uuid.uuid4())
USER_ID = str(uuid.uuid4())
TRACE_ID = "trace-export-1"
CONSOLE_TIME = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\+00:00$")


def _section(name: str, rows: list[dict[str, object]]) -> tuple[str, ExportSection]:
    return name, ExportSection(status="ok", rows=tuple(rows))


def _run_bundle(*, status: str, error_code: str | None = None) -> ExecutionExportBundle:
    record = {
        "id": RUN_ID,
        "agent_id": AGENT_ID,
        "user_id": USER_ID,
        "status": status,
        "error_code": error_code,
        "error_message": "run failed" if error_code else None,
        "trace_id": TRACE_ID,
        "input_text": "原始用户问题-do-not-redact",
        "start_time": "2026-11-09T10:00:00+00:00",
        "end_time": "2026-11-09T10:00:02+00:00",
        "deadline_at": "2026-11-09T10:10:00+00:00",
    }
    sections = dict(
        [
            _section(
                "runtime.canonical_event",
                [
                    {
                        "seq": 1,
                        "event_type": "RUN_CREATED",
                        "create_time": "2026-11-09T10:00:00+00:00",
                        "payload_json": {"status": "CREATED"},
                        "artifact_id": None,
                    },
                    {
                        "seq": 2,
                        "event_type": "TOOL_CALL",
                        "create_time": "2026-11-09T10:00:01+00:00",
                        "payload_json": {
                            "status": "ERROR",
                            "error_code": "SKILL_EXECUTION_FAILED",
                            "tool_call_id": "call-1",
                            "task_id": TASK_ID,
                            "text": "工具失败诊断-原文",
                        },
                        "artifact_id": None,
                    },
                ],
            ),
            _section(
                "runtime.model_invocation_audit",
                [
                    {
                        "id": str(uuid.uuid4()),
                        "create_time": "2026-11-09T10:00:00.500000+00:00",
                        "provider": "openai",
                        "model": "gpt-4o-mini",
                        "attempt": 0,
                        "status": "RETRY",
                        "retry_reason": "RATE_LIMITED",
                        "error_code": "MODEL_UNAVAILABLE",
                        "latency_ms": 120,
                    }
                ],
            ),
            _section(
                "runtime.tool_call_audit",
                [
                    {
                        "id": str(uuid.uuid4()),
                        "create_time": "2026-11-09T10:00:01+00:00",
                        "tool_call_id": "call-1",
                        "tool_name": "execute_skill",
                        "tool_kind": "SKILL",
                        "status": "ERROR",
                        "error_code": "SKILL_EXECUTION_FAILED",
                        "error_message": "skill failed diagnostic-原文",
                        "latency_ms": 800,
                        "task_id": TASK_ID,
                        "args_preview_json": {"api_key": "sk-should-be-masked", "query": "原文参数"},
                    }
                ],
            ),
            _section(
                "task.task_execution",
                [
                    {
                        "id": TASK_ID,
                        "status": "FAILED",
                        "error_code": "SKILL_EXECUTION_FAILED",
                        "error_message": "worker failed diagnostic-原文",
                        "finish_time": "2026-11-09T10:00:01.500000+00:00",
                        "finished_at": "2026-11-09T10:00:01.500000+00:00",
                        "attempt": 1,
                        "max_attempts": 3,
                        "intent_key": "export",
                        "source_run_id": RUN_ID,
                    }
                ],
            ),
            _section(
                "task.task_event",
                [
                    {
                        "id": str(uuid.uuid4()),
                        "task_id": TASK_ID,
                        "seq": 1,
                        "event_type": "FAILED",
                        "create_time": "2026-11-09T10:00:01.500000+00:00",
                        "payload_json": {"error_code": "SKILL_EXECUTION_FAILED"},
                    }
                ],
            ),
        ]
    )
    return ExecutionExportBundle(
        kind="RUN",
        record=record,
        sections=sections,
        database_snapshot={
            "snapshot_time": "2026-11-09 10:00:02+00:00",
            "transaction_read_only": "on",
            "transaction_isolation": "repeatable read",
        },
        warnings=(),
        truncation={"truncated": False, "omitted_events": 0},
    )


def _success_bundle() -> ExecutionExportBundle:
    record = {
        "id": RUN_ID,
        "agent_id": AGENT_ID,
        "user_id": USER_ID,
        "status": "COMPLETED",
        "error_code": None,
        "trace_id": TRACE_ID,
        "start_time": "2026-11-09T10:00:00+00:00",
        "end_time": "2026-11-09T10:00:03+00:00",
    }
    sections = dict(
        [
            _section(
                "runtime.canonical_event",
                [
                    {
                        "seq": 1,
                        "event_type": "RUN_CREATED",
                        "create_time": "2026-11-09T10:00:00+00:00",
                        "payload_json": {"status": "CREATED"},
                        "artifact_id": None,
                    }
                ],
            ),
            _section(
                "runtime.model_invocation_audit",
                [
                    {
                        "id": str(uuid.uuid4()),
                        "create_time": "2026-11-09T10:00:00.500000+00:00",
                        "provider": "openai",
                        "model": "gpt-4o-mini",
                        "attempt": 0,
                        "status": "RETRY",
                        "retry_reason": "RATE_LIMITED",
                        "error_code": "MODEL_UNAVAILABLE",
                        "latency_ms": 120,
                    },
                    {
                        "id": str(uuid.uuid4()),
                        "create_time": "2026-11-09T10:00:01+00:00",
                        "provider": "openai",
                        "model": "gpt-4o-mini",
                        "attempt": 1,
                        "status": "OK",
                        "error_code": None,
                        "latency_ms": 2000,
                    },
                ],
            ),
        ]
    )
    return ExecutionExportBundle(
        kind="RUN",
        record=record,
        sections=sections,
        database_snapshot={
            "snapshot_time": "2026-11-09 10:00:03+00:00",
            "transaction_read_only": "on",
            "transaction_isolation": "repeatable read",
        },
    )


def test_failed_run_primary_failure_is_earliest_terminal_node() -> None:
    bundle = _run_bundle(status="FAILED", error_code="RUN_NO_PROGRESS")
    diagnosis = build_diagnosis(bundle)

    assert diagnosis.outcome == "FAILED"
    assert diagnosis.primary_failure is not None
    assert diagnosis.primary_failure.node_type in {"TOOL_CALL", "EVENT", "MODEL_CALL"}
    assert diagnosis.primary_failure.service == "muad-agent-worker"
    assert diagnosis.log_lookup.keys["run_id"] == RUN_ID
    assert diagnosis.log_lookup.keys["trace_id"] == TRACE_ID
    assert diagnosis.log_lookup.keys["task_id"] == TASK_ID


def test_deadline_failure_prefers_run_level_code() -> None:
    bundle = _run_bundle(status="FAILED", error_code="RUN_DEADLINE_EXCEEDED")
    diagnosis = build_diagnosis(bundle)

    assert diagnosis.primary_failure is not None
    assert diagnosis.primary_failure.node_type == "RUN"
    assert diagnosis.primary_failure.error_code == "RUN_DEADLINE_EXCEEDED"
    assert diagnosis.primary_failure.basis == "RUN_ERROR_CODE"


def test_successful_run_does_not_fabricate_failure() -> None:
    bundle = _success_bundle()
    diagnosis = build_diagnosis(bundle)

    assert diagnosis.outcome == "SUCCEEDED"
    assert diagnosis.primary_failure is None
    assert diagnosis.contributing_failures
    assert diagnosis.contributing_failures[0].is_retry is True
    assert any(item.code == "MODEL_RETRY" for item in diagnosis.insights)
    assert all(item.code != "PRIMARY_FAILURE" for item in diagnosis.insights)


def test_task_failure_primary_is_task_and_worker_service() -> None:
    record = {
        "id": TASK_ID,
        "source_run_id": RUN_ID,
        "status": "FAILED",
        "error_code": "SKILL_EXECUTION_FAILED",
        "error_message": "worker failed diagnostic-原文",
        "trace_id": TRACE_ID,
        "start_time": "2026-11-09T10:00:00+00:00",
        "finished_at": "2026-11-09T10:00:01+00:00",
        "attempt": 2,
        "max_attempts": 3,
        "intent_key": "export",
    }
    bundle = ExecutionExportBundle(
        kind="TASK",
        record=record,
        sections=dict([_section("task.task_event", [])]),
        database_snapshot={
            "snapshot_time": "2026-11-09 10:00:01+00:00",
            "transaction_read_only": "on",
            "transaction_isolation": "repeatable read",
        },
    )
    diagnosis = build_diagnosis(bundle)

    assert diagnosis.primary_failure is not None
    assert diagnosis.primary_failure.node_type == "TASK"
    assert diagnosis.primary_failure.service == "muad-agent-worker"
    assert diagnosis.log_lookup.keys["task_id"] == TASK_ID
    assert diagnosis.log_lookup.keys["run_id"] == RUN_ID


def test_report_view_keeps_raw_diagnostics_and_declares_policy() -> None:
    bundle = _run_bundle(status="FAILED", error_code="RUN_NO_PROGRESS")
    report = build_report(bundle, build_diagnosis(bundle))

    assert report["execution"]["input_text"] == "原始用户问题-do-not-redact"
    assert report["timeline"][1]["text"] == "工具失败诊断-原文"
    assert report["timeline"][1]["payload"]["error_code"] == "SKILL_EXECUTION_FAILED"
    assert report["tool_calls"][0]["error_message"] == "skill failed diagnostic-原文"
    assert report["data_policy"] == {
        "diagnostic_fields": "raw",
        "credentials": "masked",
        "conversation_text": "included",
        "admin_only": True,
    }
    assert report["data_quality"]["complete"] is True
    assert report["database_snapshot"]["transaction_isolation"] == "repeatable read"
    assert report["summary"]["failure_count"] >= 1
    assert report["summary"]["timing"]["elapsed_seconds"] == 2.0


def test_report_time_fields_keep_console_offset() -> None:
    bundle = _run_bundle(status="FAILED", error_code="RUN_NO_PROGRESS")
    report = build_report(bundle, build_diagnosis(bundle))

    assert CONSOLE_TIME.match(report["generated_at"])
    assert CONSOLE_TIME.match(report["execution"]["start_time"])
    assert CONSOLE_TIME.match(report["timeline"][0]["create_time"])
