"""平台会话最后报告的纯逻辑契约：顺序、三态可用性与安全投影。"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from muad_platform_sdk.skill_access import PlatformAttempt, PlatformSessionReport

pytestmark = pytest.mark.unit


def _ok(key: str) -> PlatformAttempt:
    return PlatformAttempt(
        key=key,
        name=key,
        authentication="session",
        available=True,
        expires_at=datetime(2026, 11, 20, 10, 0, tzinfo=UTC),
    )


def test_report_keeps_request_order_and_projects_platform_fields() -> None:
    report = PlatformSessionReport()
    report.record(_ok("mssp"))
    report.record(
        PlatformAttempt(
            key="mssw",
            name="mssw",
            authentication="session",
            available=False,
            code="PLATFORM_AUTH_REJECTED",
            detail={"http_status": 401, "message": "denied"},
            retryable=False,
        )
    )

    payload = report.payload()

    assert [item["key"] for item in payload["platforms"]] == ["mssp", "mssw"]
    assert payload["platforms"][0]["available"] is True
    assert payload["platforms"][0]["expiresAt"] == "2026-11-20T10:00:00+00:00"
    assert payload["platforms"][1]["available"] is False
    assert payload["platforms"][1]["error"] == {
        "code": "PLATFORM_AUTH_REJECTED",
        "detail": {"http_status": 401, "message": "denied"},
    }
    assert report.has_success is True
    assert report.has_failures is True
    assert [attempt.key for attempt in report.successes()] == ["mssp"]


def test_report_last_attempt_wins_and_can_project_a_subset() -> None:
    report = PlatformSessionReport()
    report.record(_ok("mssp"))
    report.record(_ok("mssw"))
    report.record(
        PlatformAttempt(
            key="mssp",
            name="mssp",
            authentication="session",
            available=False,
            code="PLATFORM_NETWORK_ERROR",
            retryable=True,
        )
    )

    payload = report.payload(["mssw", "mssp"])

    assert [item["key"] for item in payload["platforms"]] == ["mssw", "mssp"]
    assert payload["platforms"][0]["available"] is True
    assert payload["platforms"][1]["available"] is False
    assert payload["platforms"][1]["error"]["retryable"] is True
    # last record wins; the first successful attempt is no longer reported
    assert [attempt.key for attempt in report.successes()] == ["mssw"]


def test_report_can_express_not_checked_without_fabricating_a_failure() -> None:
    report = PlatformSessionReport()
    report.record(
        PlatformAttempt(
            key="mssw",
            name="mssw",
            authentication="session",
            available=None,
            reason="budget_exhausted",
        )
    )

    payload = report.payload()

    assert payload["platforms"][0] == {
        "key": "mssw",
        "name": "mssw",
        "authentication": "session",
        "available": None,
        "reason": "budget_exhausted",
    }
    assert report.has_failures is False
    assert report.has_success is False


def test_report_never_projects_session_material() -> None:
    report = PlatformSessionReport()
    report.record(
        PlatformAttempt(
            key="mssp",
            name="mssp",
            authentication="session",
            available=True,
            expires_at=datetime(2026, 11, 20, 10, 0, tzinfo=UTC),
            session={"cookie": "secret-cookie"},  # type: ignore[arg-type]
        )
    )

    rendered = json.dumps(report.payload(), ensure_ascii=False)

    assert "secret-cookie" not in rendered
    assert "cookie" not in rendered
