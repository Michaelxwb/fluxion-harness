"""S-04 controlled Linux image drill; explicitly provisioned by the caller."""
import json
import os
import subprocess

import pytest

pytestmark = pytest.mark.integration


def test_linux_system_site_hook_and_fail_closed_startup():
    image = os.environ.get("EGRESS_SITE_TEST_IMAGE")
    if not image:
        pytest.skip("requires a provisioned controlled Linux image")
    redis_url = os.environ.get("EGRESS_SITE_TEST_REDIS_URL")
    if not redis_url:
        pytest.fail("env_blocked: isolated Redis reachable from the test image is required")
    result = subprocess.run([
        "docker", "run", "--rm", "--security-opt", "seccomp=unconfined",
        "-e", f"REDIS_URL={redis_url}", image,
    ], capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report["status"] == "passed"
    assert report["controlled_requests"] == 1
    assert report["startup_blocks"] == 2
    assert report["uncontrolled_requests"] == 1
