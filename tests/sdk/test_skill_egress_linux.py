"""Real production core executor/kernel probe; Runtime/Worker assembly is separate."""
import json
import os
import subprocess

import pytest

pytestmark = pytest.mark.integration


def test_core_sdk_hook_stops_script_tree_and_closes_http():
    image = os.environ.get("EGRESS_SITE_TEST_IMAGE")
    if not image:
        pytest.skip("requires the controlled Linux wheel image")
    redis = os.environ.get("EGRESS_SITE_TEST_REDIS_URL")
    if not redis:
        pytest.fail("env_blocked: provide isolated Redis reachable from the Linux image")
    result = subprocess.run([
        "docker", "run", "--rm", "--security-opt", "seccomp=unconfined",
        "-e", "REDIS_URL=" + redis, "--entrypoint", "python", image, "/opt/egress-core-probe.py",
    ], capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Unhandled exception" not in result.stderr
    assert "Task exception was never retrieved" not in result.stderr
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report == {"status": "passed", "quota_requests": 3, "grandchild_reclaimed": True,
                      "slow_http_closed": True, "startup_checks": 3}
