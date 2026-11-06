"""[E-03] 沙箱读不到服务 Token/DB/Redis/SA Token/敏感挂载（真实 bwrap --clearenv）。"""

from __future__ import annotations

import pytest

from tests.acceptance.user_skill_sandbox.sandboxed_support import run_probe

pytestmark = pytest.mark.integration


def test_e03_secret_boundary_and_minimal_mounts() -> None:
    payload = run_probe("e03")

    assert payload["ok"] is True, payload
    result = payload["result"]
    assert result["status"] == "SUCCEEDED", result
    observed = result["result"]
    assert observed["secrets"] == {
        "INTERNAL_SERVICE_TOKEN": None,
        "DATABASE_URL": None,
        "REDIS_URL": None,
        "AWS_SECRET_ACCESS_KEY": None,
        "MUAD_SESSION_SOCKET": None,
        "SKILL_TOKEN": None,
        "SKILL_SECRET": None,
    }
    assert observed["paths"] == {
        "/mnt": False,
        "/proc/1": False,
        "/var/run/docker.sock": False,
        "/var/run/secrets/kubernetes.io/serviceaccount/token": False,
        "/skill/SKILL.md": True,
        "/workspace": True,
    }
    assert observed["workspace_writable"] is True
    assert observed["skill_writable"] is False
    assert observed["allowed_env"] == "visible"
    assert observed["output"] == "/workspace/outputs/e03-exec"
    assert observed["data"] == "/workspace/data"
    # 密钥/环境值不得出现在 stdout/stderr。
    for secret in (
        "probe-service-token-81f2",
        "request-service-token-a2d1",
        "skill-token-should-drop",
        "skill-secret-should-drop",
        "probe-secret",
        "request-secret",
    ):
        assert secret not in result["stdout"]
        assert secret not in result["stderr"]
