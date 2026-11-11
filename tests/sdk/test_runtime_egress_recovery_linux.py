"""Runtime executor recovery with real PG, Redis, HTTP and Linux sandbox."""

import json
import os
import subprocess

import pytest

pytestmark = pytest.mark.integration


def test_recovered_runtime_keeps_execution_budget():
    image = os.environ.get("EGRESS_SITE_TEST_IMAGE")
    database = os.environ.get("EGRESS_SITE_TEST_DATABASE_URL")
    redis = os.environ.get("EGRESS_SITE_TEST_REDIS_URL")
    if not image or not database or not redis:
        pytest.fail("env_blocked: controlled Linux image and isolated PG/Redis are required")
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--security-opt",
            "seccomp=unconfined",
            "--entrypoint",
            "python",
            "-e",
            "DATABASE_URL=" + database,
            "-e",
            "REDIS_URL=" + redis,
            image,
            "/opt/egress-runtime-recovery-probe.py",
        ],
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1]) == {
        "status": "passed",
        "first_requests": 30,
        "after_recovery": 20,
    }
