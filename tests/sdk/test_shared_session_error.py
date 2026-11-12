"""Local shared rejection stays distinct from an upstream authentication error."""
import pytest
from muad_platform_sdk.shared_redis import SharedLimitExceeded
from muad_platform_sdk.skill_bridge import _broker_error


@pytest.mark.parametrize("kind,code", [("TOTAL", "LOCAL_BUDGET_EXHAUSTED"),
                                     ("WINDOW", "LOCAL_RATE_LIMITED"), ("INFLIGHT", "LOCAL_RATE_LIMITED")])
def test_session_bridge_reports_shared_limit_without_upstream_rewrite(kind, code):
    result = _broker_error(SharedLimitExceeded(kind, "rule", 0, scope="invocation"))
    assert result == {"error": code, "detail": {"limit_kind": kind, "scope": "invocation", "rule_id": "rule"},
                      "retryable": False}
