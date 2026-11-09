"""TASK-003: report rendering, credential masking and bounded package shape."""

from __future__ import annotations

import io
import json
import zipfile

from muad_console_platform.application.execution_diagnosis import build_diagnosis
from muad_console_platform.application.execution_export_render import (
    ZIP_FILES,
    fit_report,
    mask_credentials,
    render_json,
    render_markdown,
    render_zip,
)
from muad_console_platform.application.execution_export_view import build_report
from tests.console_platform.test_execution_diagnosis import _run_bundle


def test_mask_credentials_preserves_diagnostic_token_counts() -> None:
    value = {
        "input_tokens": 123,
        "output_tokens": 45,
        "token_coverage": 88.5,
        "api_key": "sk-live-secret",
        "password": "p@ss",
        "Authorization": "Bearer abc.def",
        "nested": {"client_secret": "cs", "query": "原文参数"},
        "message": 'Authorization: Bearer abc.def api_key=sk-live-secret',
    }
    masked = mask_credentials(value)
    assert masked["input_tokens"] == 123
    assert masked["output_tokens"] == 45
    assert masked["token_coverage"] == 88.5
    assert "sk-live-secret" not in json.dumps(masked)
    assert "abc.def" not in json.dumps(masked)
    assert "p@ss" not in json.dumps(masked)
    assert masked["nested"]["query"] == "原文参数"


def test_zip_contains_fixed_files_and_markdown_summary() -> None:
    bundle = _run_bundle(status="FAILED", error_code="RUN_NO_PROGRESS")
    report = build_report(bundle, build_diagnosis(bundle))
    archive_bytes = render_zip(report)

    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        assert tuple(archive.namelist()) == ZIP_FILES
        payload = json.loads(archive.read("report.json"))
        markdown = archive.read("report.md").decode("utf-8")
        readme = archive.read("README.md").decode("utf-8")
        events = archive.read("events.csv").decode("utf-8")
        failures = archive.read("failures.csv").decode("utf-8")
        calls = archive.read("calls.csv").decode("utf-8")

    assert payload["schema_version"] == 1
    assert "失败定位" in markdown
    assert "muad-agent-worker" in markdown
    assert "诊断字段: raw" in readme
    assert "seq,event_type" in events
    assert "node_type,node_id" in failures
    assert "call_type" in calls


def test_e02_fit_report_keeps_primary_failure_and_marks_truncation() -> None:
    bundle = _run_bundle(status="FAILED", error_code="RUN_NO_PROGRESS")
    report = build_report(bundle, build_diagnosis(bundle))
    report["timeline"] = [
        {
            "seq": seq,
            "event_type": "TOOL_CALL",
            "create_time": "2026-11-09T10:00:00+00:00",
            "payload": {"text": "x" * 200},
        }
        for seq in range(1, 301)
    ]
    report["diagnosis"]["primary_failure"]["seq"] = 150
    fitted = fit_report(report, max_bytes=20_000, max_events=40)

    assert fitted["truncation"]["truncated"] is True
    assert fitted["truncation"]["omitted_events"] > 0
    assert any(item["seq"] == 150 for item in fitted["timeline"])


def test_markdown_and_json_keep_diagnostic_text_but_mask_secret() -> None:
    bundle = _run_bundle(status="FAILED", error_code="RUN_NO_PROGRESS")
    report = build_report(bundle, build_diagnosis(bundle))
    json_text = render_json(report).decode("utf-8")
    markdown = render_markdown(report)

    assert "工具失败诊断-原文" in json_text
    assert "skill failed diagnostic-原文" in json_text
    assert "sk-should-be-masked" not in json_text
    assert "sk-should-be-masked" not in markdown
