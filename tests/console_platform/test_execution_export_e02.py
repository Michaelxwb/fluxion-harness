"""E-02: fitting the package truncates explicitly and keeps the primary failure."""

from __future__ import annotations

from muad_console_platform.application.execution_diagnosis import build_diagnosis
from muad_console_platform.application.execution_export_render import fit_report
from muad_console_platform.application.execution_export_view import build_report

from tests.console_platform.test_execution_diagnosis import _run_bundle


def test_e02_fit_report_preserves_primary_failure() -> None:
    bundle = _run_bundle(status="FAILED", error_code="RUN_NO_PROGRESS")
    report = build_report(bundle, build_diagnosis(bundle))
    report["timeline"] = [
        {
            "seq": seq,
            "event_type": "TOOL_CALL",
            "create_time": "2026-11-09T10:00:00+00:00",
            "payload": {"text": "x" * 300},
        }
        for seq in range(1, 301)
    ]
    report["diagnosis"]["primary_failure"] = {
        "node_type": "TOOL_CALL",
        "node_id": "audit-1",
        "seq": 150,
        "service": "muad-agent-worker",
        "basis": "EARLIEST_TERMINAL_FAILURE",
    }

    fitted = fit_report(report, max_bytes=20_000, max_events=40)

    assert fitted["truncation"]["truncated"] is True
    assert fitted["truncation"]["omitted_events"] > 0
    assert any(item["seq"] == 150 for item in fitted["timeline"])
    assert fitted["diagnosis"]["primary_failure"]["seq"] == 150
    assert len(fitted["timeline"]) < 301
