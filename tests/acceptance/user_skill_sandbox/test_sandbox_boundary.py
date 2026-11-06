"""[harness-secret#RULE-secret-001] 沙箱环境/输出不泄露服务凭据。"""

from __future__ import annotations

import pytest

from tests.acceptance.user_skill_sandbox.sandboxed_support import run_probe

pytestmark = pytest.mark.integration


def test_sandbox_environment_and_output_never_expose_service_secrets() -> None:
    payload = run_probe("e03")

    assert payload["ok"] is True, payload
    result = payload["result"]
    assert result["status"] == "SUCCEEDED", result
    observed = result["result"]
    assert all(value is None for value in observed["secrets"].values()), observed["secrets"]
    assert observed["paths"]["/mnt"] is False
    assert observed["paths"]["/proc/1"] is False
    assert observed["paths"]["/var/run/docker.sock"] is False
    assert observed["paths"]["/var/run/secrets/kubernetes.io/serviceaccount/token"] is False
    assert observed["skill_writable"] is False
    combined = result["stdout"] + result["stderr"]
    for canary in (
        "probe-service-token-81f2",
        "request-service-token-a2d1",
        "probe-secret",
        "request-secret",
        "skill-token-should-drop",
        "skill-secret-should-drop",
    ):
        assert canary not in combined
