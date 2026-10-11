"""Ad-hoc Run timeline scope: check actual API/DTO/browser coverage without an active TASK."""

import ast
import re
from pathlib import Path
from typing import get_args

import pytest
from muad_console_platform.application.run_event_view import EventFieldKey, ModelRunSummary

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "apps/console-platform/frontend/src/modules/run-observability"
BACKEND_TESTS = ROOT / "tests/console_platform/test_run_event_details.py"
MODEL_TESTS = ROOT / "tests/console_platform/test_model_event_context.py"
RUNTIME_TESTS = ROOT / "tests/agent_runtime/test_execution_audit.py"
BROWSER = ROOT / "e2e/tests/run-observability.spec.ts"
CONFIG = ROOT / "e2e/playwright.run-observability.config.ts"
REQUIRED_BACKEND_CASES = {
    "test_message_text_is_event_specific_and_redacted",
    "test_tool_details_join_audit_without_copying_payload",
    "test_model_event_joins_exact_attempt_not_nearest_timestamp",
    "test_async_event_links_current_operation_and_task_status",
    "test_event_scope_and_soft_deletion_return_same_not_found",
    "test_background_result_uses_excerpt_without_internal_reference_fields",
    "test_historical_model_uses_frozen_identity_not_current_definition",
    "test_historical_model_summary_distinguishes_unknown_from_zero",
    "test_historical_summary_excludes_other_tenant_run_and_deleted_audits",
    "test_bad_or_completed_model_reference_is_unlinked_for_active_run",
}


def _fields(source: str, array: str = "EVENT_DETAIL_FIELDS") -> set[str]:
    match = re.search(rf"{array} = \[(.*?)\] as const", source, re.DOTALL)
    assert match, f"{array} missing"
    return set(re.findall(r"'([^']+)'", match.group(1)))


def _verify(fields: set[str], browser: str, config: str, backend_cases: set[str]) -> list[str]:
    errors = []
    if fields != set(get_args(EventFieldKey)):
        errors.append("EventFieldKey / EVENT_DETAIL_FIELDS drift")
    for case in REQUIRED_BACKEND_CASES - backend_cases:
        errors.append(f"missing backend case {case}")
    for case in ("S-25", "S-26", "E-23", "E-24"):
        if not re.search(rf"test\('{case} ", browser):
            errors.append(f"missing browser case {case}")
    if "run-observability.spec.ts" not in config:
        errors.append("browser suite not configured")
    for case in ("S-25", "S-26"):
        match = re.search(rf"test\('{case} .*?(?=\ntest\(|\Z)", browser, re.DOTALL)
        if match and ("page.route(" in match.group() or "route.fulfill" in match.group()):
            errors.append(f"{case} replaces real API boundary")
    return errors


def _sources() -> tuple[set[str], str, str, set[str]]:
    cases = {
        node.name
        for path in (BACKEND_TESTS, MODEL_TESTS)
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.AsyncFunctionDef)
    }
    return (
        _fields((FRONTEND / "services/runs.ts").read_text()),
        BROWSER.read_text(),
        CONFIG.read_text(),
        cases,
    )


def test_run_event_details_scope_matches_real_sources() -> None:
    assert _verify(*_sources()) == []
    runtime = ast.parse(RUNTIME_TESTS.read_text())
    assert any(
        isinstance(node, ast.AsyncFunctionDef)
        and node.name == "test_model_events_link_each_retry_to_its_exact_audit_row"
        for node in ast.walk(runtime)
    )


def test_model_context_summary_matches_api_schema_and_browser_coverage() -> None:
    source = (FRONTEND / "services/runs.ts").read_text()
    assert _fields(source, "MODEL_SUMMARY_FIELDS") == set(ModelRunSummary.model_fields)
    browser = BROWSER.read_text()
    assert "'本次运行模型调用汇总'" in browser
    assert "model.getByTestId('run-event-model-summary')).toHaveCount(0)" in browser
    assert "'累计调用耗时'" in browser


@pytest.mark.parametrize(
    "perturbation,expected",
    [
        ("field", "EventFieldKey"),
        ("case", "S-25"),
        ("config", "suite"),
        ("boundary", "S-25 replaces real API"),
    ],
)
def test_inventory_detects_drift(perturbation: str, expected: str) -> None:
    fields, browser, config, cases = _sources()
    if perturbation == "field":
        fields.remove("operation_status")
    elif perturbation == "case":
        browser = browser.replace("test('S-25 ", "test('removed ")
    elif perturbation == "config":
        config = config.replace("run-observability.spec.ts", "missing.spec.ts")
    else:
        browser = browser.replace("test('S-25 ", "test('S-25 page.route( ")
    assert any(expected in error for error in _verify(fields, browser, config, cases))
