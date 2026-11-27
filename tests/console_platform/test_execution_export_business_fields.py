"""导出/诊断必须区分 transport 成功与业务内容失败（E-01 / S-16）。"""

from __future__ import annotations

import csv
import io
import uuid

from muad_console_platform.application.execution_diagnosis import build_diagnosis
from muad_console_platform.application.execution_export_render import (
    fit_report,
    render_calls_csv,
    render_failures_csv,
)
from muad_console_platform.application.execution_export_view import build_report
from muad_console_platform.infrastructure.repositories.execution_export_repository import (
    ExecutionExportBundle,
    ExportSection,
)

RUN_ID = str(uuid.uuid4())
AGENT_ID = str(uuid.uuid4())
USER_ID = str(uuid.uuid4())
TRACE_ID = "trace-business-1"
FINALIZATION = {
    "schema_version": 1,
    "termination": {
        "reason": "NO_PROGRESS",
        "secondary_reasons": [],
        "error_code": "RUN_NO_PROGRESS",
    },
    "usage": {
        "turns_used": 3,
        "turns_limit": 8,
        "tool_calls_used": 3,
        "tool_calls_limit": 16,
        "elapsed_ms": 1200,
        "deadline_ms": 60000,
    },
    "tool_outcomes": {
        "total": 3,
        "succeeded": 0,
        "failed": 1,
        "timed_out": 0,
        "denied": 0,
        "cancelled": 0,
        "not_applicable": 1,
        "unknown": 1,
        "failure_layers": {"TOOL": 0, "CONTENT": 1},
    },
    "business_failure_evidence": {
        "failures": [
            {
                "tool": "execute",
                "tool_call_id": "call-1",
                "business_status": "FAILED",
                "failure_layer": "CONTENT",
                "error_code": "SKILL_INPUT_INVALID",
                "exit_code": 2,
                "retryable": False,
                "evidence_preview": "missing --company-id",
                "occurred_at": "2026-11-19T00:00:01+00:00",
            }
        ],
        "last_failure": None,
        "decisive_failure": None,
    },
    "evidence_status": "CONFIRMED_FAILURE",
    "evidence_incomplete": True,
    "pending_tool_calls": [],
    "completion": {"status": "UNKNOWN", "source": None},
}


def _section(name: str, rows: list[dict[str, object]]) -> tuple[str, ExportSection]:
    return name, ExportSection(status="ok", rows=tuple(rows))


def _bundle(*, timeline_size: int = 2) -> ExecutionExportBundle:
    events = [
        {
            "seq": index,
            "event_type": "MESSAGE_DELTA",
            "create_time": f"2026-11-19T00:00:{index:02d}+00:00",
            "payload_json": {"text": f"chunk-{index}"},
        }
        for index in range(1, timeline_size + 1)
    ]
    events[-1] = {
        "seq": timeline_size,
        "event_type": "RUN_FAILED",
        "create_time": "2026-11-19T00:00:09+00:00",
        "payload_json": {
            "status": "FAILED",
            "error_code": "RUN_NO_PROGRESS",
            "finalization": FINALIZATION,
        },
    }
    record = {
        "id": RUN_ID,
        "agent_id": AGENT_ID,
        "user_id": USER_ID,
        "status": "FAILED",
        "error_code": "RUN_NO_PROGRESS",
        "error_message": "made no progress",
        "trace_id": TRACE_ID,
        "start_time": "2026-11-19T00:00:00+00:00",
        "end_time": "2026-11-19T00:00:10+00:00",
        "deadline_at": "2026-11-19T00:01:00+00:00",
    }
    sections = dict(
        [
            _section("runtime.canonical_event", events),
            _section(
                "runtime.tool_call_audit",
                [
                    {
                        "id": str(uuid.uuid4()),
                        "create_time": "2026-11-19T00:00:01+00:00",
                        "tool_call_id": "call-1",
                        "tool_name": "execute",
                        "tool_kind": "SKILL",
                        "status": "OK",
                        "error_code": "SKILL_INPUT_INVALID",
                        "error_message": "input rejected",
                        "business_status": "FAILED",
                        "failure_layer": "CONTENT",
                        "exit_code": 2,
                        "output_truncated": True,
                        "evidence_preview": "missing --company-id",
                        "latency_ms": 30,
                    },
                    {
                        "id": str(uuid.uuid4()),
                        "create_time": "2026-11-19T00:00:02+00:00",
                        "tool_call_id": "call-2",
                        "tool_name": "read_attachment",
                        "tool_kind": "ATTACHMENT",
                        "status": "OK",
                        "business_status": "UNKNOWN",
                        "failure_layer": None,
                        "latency_ms": 5,
                    },
                    {
                        "id": str(uuid.uuid4()),
                        "create_time": "2026-11-19T00:00:03+00:00",
                        "tool_call_id": "call-3",
                        "tool_name": "current_time",
                        "tool_kind": "PLATFORM",
                        "status": "OK",
                        "business_status": "NOT_APPLICABLE",
                        "failure_layer": None,
                        "latency_ms": 1,
                    },
                ],
            ),
            _section("runtime.model_invocation_audit", []),
            _section("runtime.egress_audit", []),
            _section("runtime.tool_operation", []),
            _section("task.task_execution", []),
        ]
    )
    return ExecutionExportBundle(
        kind="RUN",
        record=record,
        sections=sections,
        truncation={},
        database_snapshot={"snapshot_time": "2026-11-19T00:00:10+00:00"},
    )


def test_transport_ok_business_failed_is_a_content_failure_candidate() -> None:
    bundle = _bundle()
    diagnosis = build_diagnosis(bundle)

    candidates = [*diagnosis.contributing_failures]
    if diagnosis.primary_failure is not None:
        candidates.insert(0, diagnosis.primary_failure)
    content = [item for item in candidates if item.node_type == "TOOL_CALL"]
    assert len(content) == 1
    assert content[0].basis == "BUSINESS_CONTENT_FAILURE"
    assert content[0].business_status == "FAILED"
    assert content[0].failure_layer == "CONTENT"
    assert content[0].exit_code == 2
    assert content[0].output_truncated is True
    assert content[0].evidence_preview == "missing --company-id"


def test_report_exposes_business_fields_and_finalization() -> None:
    report = build_report(_bundle(), build_diagnosis(_bundle()))

    execute_row = report["tool_calls"][0]
    assert execute_row["status"] == "OK"
    assert execute_row["business_status"] == "FAILED"
    assert execute_row["failure_layer"] == "CONTENT"
    assert execute_row["exit_code"] == 2
    assert execute_row["output_truncated"] is True
    assert execute_row["evidence_preview"] == "missing --company-id"

    assert report["execution"]["finalization"]["termination"]["reason"] == "NO_PROGRESS"
    assert report["failure_evidence"]["failures"][0]["error_code"] == "SKILL_INPUT_INVALID"
    assert report["data_quality"]["db_complete"] is True
    assert report["data_quality"]["export_truncated"] is False
    assert report["data_quality"]["evidence_incomplete"] is True


def test_report_projects_only_the_frozen_run_budget_facts() -> None:
    bundle = _bundle()
    name, section = _section("runtime.runtime_snapshot", [{
        "policy_json": {
            "max_turns": 200,
            "max_tool_calls": 300,
            "compaction": {"history_budget_tokens": 123904, "summary": {"enabled": True}},
            "summary_model": {"api_key": "opaque-test-sentinel"},
        },
    }])
    bundle.sections[name] = section

    report = build_report(bundle, build_diagnosis(bundle))

    budget = report["execution"]["snapshot"]["effective_budget"]
    assert budget["max_turns"] == 200
    assert budget["max_tool_calls"] == 300
    assert budget["history_budget_tokens"] == 123904
    assert budget["max_identical_failures"] is None
    assert budget["summary_enabled"] and budget["summary_model_frozen"]
    assert "opaque-test-sentinel" not in str(report)


def test_task_report_projects_the_frozen_budget() -> None:
    bundle = ExecutionExportBundle(
        kind="TASK",
        record={
            "id": str(uuid.uuid4()),
            "status": "COMPLETED",
            "execution_snapshot_json": {
                "schema_version": 4,
                "agent": {"revision": 1},
                "model": {"id": "m", "revision": 1},
                "skills": [],
                "budget": {
                    "max_steps": 200,
                    "max_tool_calls": 300,
                    "deadline_ms": 3_600_000,
                    "max_model_retries": 3,
                    "max_identical_failures": 5,
                    "execution_timeout_sec": 600,
                },
            },
            "snapshot_hash": "sha256:" + "a" * 64,
        },
        sections={},
        truncation={},
        database_snapshot={"snapshot_time": "2026-11-19T00:00:10+00:00"},
    )

    report = build_report(bundle, build_diagnosis(bundle))

    budget = report["execution"]["snapshot"]["effective_budget"]
    assert budget["max_turns"] == 200
    assert budget["max_tool_calls"] == 300
    assert budget["max_identical_failures"] == 5


def test_failures_csv_lists_confirmed_business_failure_only() -> None:
    report = build_report(_bundle(), build_diagnosis(_bundle()))
    text = render_failures_csv(report)
    rows = list(csv.DictReader(io.StringIO(text)))

    assert any(row["error_code"] == "SKILL_INPUT_INVALID" for row in rows)
    assert all(row["business_status"] not in {"UNKNOWN", "NOT_APPLICABLE"} for row in rows)
    assert "call-2" not in text
    assert "call-3" not in text


def test_calls_csv_keeps_transport_status_next_to_business_fields() -> None:
    report = build_report(_bundle(), build_diagnosis(_bundle()))
    rows = list(csv.DictReader(io.StringIO(render_calls_csv(report))))
    execute = next(row for row in rows if row["tool_call_id"] == "call-1")

    assert execute["status"] == "OK"
    assert execute["business_status"] == "FAILED"
    assert execute["failure_layer"] == "CONTENT"
    assert execute["exit_code"] == "2"
    assert execute["output_truncated"] == "True"


def test_truncation_keeps_failure_evidence_and_finalization() -> None:
    report = build_report(_bundle(timeline_size=200), build_diagnosis(_bundle(timeline_size=200)))
    fitted = fit_report(report, max_bytes=200_000)

    assert fitted["truncation"]["truncated"] is True
    assert fitted["failure_evidence"] == report["failure_evidence"]
    assert fitted["execution"]["finalization"] == report["execution"]["finalization"]
    assert fitted["data_quality"]["export_truncated"] is True
