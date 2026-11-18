"""TASK 导出：timeline 来自 task_event，tasks.events 按 entity_id 归组，TASK 有开始时间。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from muad_console_platform.application.execution_diagnosis import build_diagnosis
from muad_console_platform.application.execution_export_view import build_report
from muad_console_platform.infrastructure.repositories.execution_export_repository import (
    ExecutionExportBundle,
    ExportSection,
)

TASK_ID = uuid.uuid4()
START = datetime(2026, 11, 18, 8, 15, 5, tzinfo=UTC)
END = START + timedelta(seconds=12)


def _bundle() -> ExecutionExportBundle:
    task_event = {
        "id": uuid.uuid4(),
        "tenant_id": "default",
        "entity_type": "EXECUTION",
        "entity_id": TASK_ID,
        "seq": 1,
        "event_type": "TOOL_CALL",
        "payload_json": {
            "state": "COMPLETED",
            "tool_call_id": "call-1",
            "tool_name": "execute",
            "status": "FAILED",
            "error_code": "SKILL_EXECUTION_FAILED",
            "result": '{"error":{"code":"SKILL_EXECUTION_FAILED","message":"missing --company-id"}}',
        },
        "create_time": (START + timedelta(seconds=3)).isoformat(),
    }
    failed_event = {
        **task_event,
        "id": uuid.uuid4(),
        "seq": 2,
        "event_type": "FAILED",
        "payload_json": {"error_code": "TASK_NO_PROGRESS", "error_message": "made no progress"},
        "create_time": END.isoformat(),
    }
    task_row = {
        "id": TASK_ID,
        "tenant_id": "default",
        "parent_id": None,
        "root_id": None,
        "status": "FAILED",
        "error_code": "TASK_NO_PROGRESS",
        "error_message": "agentic execution made no progress",
        "attempt": 1,
        "max_attempts": 3,
        "skill_id": None,
        "source_run_id": None,
        "deadline_at": (END + timedelta(hours=24)).isoformat(),
        "started_at": START.isoformat(),
        "finished_at": END.isoformat(),
        "delivery_status": None,
        "execution_snapshot_json": {},
        "snapshot_hash": "sha256:" + "a" * 64,
    }
    return ExecutionExportBundle(
        kind="TASK",
        record=task_row,
        sections={
            "task.task_execution": ExportSection(status="ok", rows=(task_row,)),
            "task.task_event": ExportSection(status="ok", rows=(task_event, failed_event)),
        },
        database_snapshot={"snapshot_time": START.isoformat()},
    )


def test_task_export_uses_task_events_and_started_at() -> None:
    bundle = _bundle()
    report = build_report(bundle, build_diagnosis(bundle))

    assert [item["event_type"] for item in report["timeline"]] == ["TOOL_CALL", "FAILED"]
    assert report["timeline"][0]["tool_call_id"] == "call-1"
    assert report["execution"]["start_time"] is not None
    assert report["execution"]["duration_ms"] == 12000
    assert report["tasks"][0]["events"][0]["event_type"] == "TOOL_CALL"
    assert report["tasks"][0]["events"][1]["event_type"] == "FAILED"


def test_task_export_does_not_claim_success_for_cancelled() -> None:
    bundle = _bundle()
    report = build_report(bundle, build_diagnosis(bundle))
    assert report["diagnosis"]["outcome"] == "FAILED"
