"""Contract checks for the execution-export report shape and time policy."""

from __future__ import annotations

import re

from muad_console_platform.application.execution_diagnosis import build_diagnosis
from muad_console_platform.application.execution_export_view import build_report

from tests.console_platform.test_execution_diagnosis import _run_bundle

TIME_WITH_OFFSET = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$")


def test_report_contract_has_all_top_level_sections() -> None:
    bundle = _run_bundle(status="FAILED", error_code="RUN_NO_PROGRESS")
    report = build_report(bundle, build_diagnosis(bundle))

    assert set(report) == {
        "schema_version",
        "generated_at",
        "locale",
        "execution",
        "summary",
        "diagnosis",
        "timeline",
        "model_calls",
        "tool_calls",
        "egress_calls",
        "operations",
        "tasks",
        "links",
        "supplemental",
        "truncation",
        "data_quality",
        "database_snapshot",
        "data_policy",
    }
    assert report["schema_version"] == 1
    assert report["execution"]["kind"] == "RUN"
    assert report["execution"]["outcome"] == "FAILED"
    assert report["diagnosis"]["primary_failure"] is not None


def test_report_time_fields_always_carry_offset() -> None:
    bundle = _run_bundle(status="FAILED", error_code="RUN_NO_PROGRESS")
    report = build_report(bundle, build_diagnosis(bundle))

    assert TIME_WITH_OFFSET.match(report["generated_at"])
    assert TIME_WITH_OFFSET.match(report["execution"]["start_time"])
    assert TIME_WITH_OFFSET.match(report["execution"]["end_time"])
    assert TIME_WITH_OFFSET.match(report["timeline"][0]["create_time"])


def test_report_policy_declares_raw_diagnostics_and_masked_credentials() -> None:
    bundle = _run_bundle(status="FAILED", error_code="RUN_NO_PROGRESS")
    report = build_report(bundle, build_diagnosis(bundle))

    assert report["data_policy"]["diagnostic_fields"] == "raw"
    assert report["data_policy"]["credentials"] == "masked"
    assert report["data_policy"]["conversation_text"] == "included"
    assert report["data_policy"]["admin_only"] is True
