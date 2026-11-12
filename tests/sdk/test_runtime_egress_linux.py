"""Runtime executor with real Linux sandbox, PG, Redis and HTTP."""

import json
import os
import subprocess

import pytest

pytestmark = pytest.mark.integration


def test_runtime_budget_stops_tree_and_preserves_previous_result():
    image = os.environ.get("EGRESS_SITE_TEST_IMAGE")
    database = os.environ.get("EGRESS_SITE_TEST_DATABASE_URL")
    redis = os.environ.get("EGRESS_SITE_TEST_REDIS_URL")
    if not image or not database or not redis:
        pytest.skip("requires controlled Linux image and isolated PG/Redis")
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
            "/opt/egress-runtime-probe.py",
        ],
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1]) == {
        "status": "passed",
        "platform_requests": 3,
        "partial_results": True,
        "tree_reclaimed": True,
    }
