"""Actual Worker loop + PG + Redis + Linux child tree; Run acceptance is separate."""
import json
import os
import subprocess

import pytest

pytestmark = pytest.mark.integration


def test_worker_shared_sdk_stops_tree_and_does_not_retry():
    image = os.environ.get("EGRESS_SITE_TEST_IMAGE")
    if not image:
        pytest.skip("requires installed Worker wheels and the controlled Linux sandbox")
    database = os.environ.get("EGRESS_SITE_TEST_DATABASE_URL")
    redis = os.environ.get("EGRESS_SITE_TEST_REDIS_URL")
    if not database or not redis:
        pytest.fail("env_blocked: isolated PG and Redis reachable from the test image are required")
    result = subprocess.run([
        "docker", "run", "--rm", "--security-opt", "seccomp=unconfined", "--entrypoint", "python",
        "-e", "DATABASE_URL=" + database, "-e", "REDIS_URL=" + redis, image, "/opt/egress-worker-probe.py",
    ], capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report == {"status": "passed", "platform_requests": 3, "task_retry": False,
                      "grandchild_reclaimed": True, "slow_http_closed": True}
