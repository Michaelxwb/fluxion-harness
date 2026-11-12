"""Lifecycle acceptance with real subprocess, HTTP and PG boundaries.

Each pytest stage runs in its own process, so Worker and acceptance datastore
fixtures cannot overwrite one another's DATABASE_URL/REDIS_URL or loop caches.
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
from contextlib import suppress
from pathlib import Path

ROOT = next(
    parent for parent in Path(__file__).resolve().parents if (parent / ".code-flow/.version").is_file()
)


def _pytest(*targets: str) -> None:
    env = os.environ.copy()
    if any("agent_worker" in target or "acceptance/runtime" in target for target in targets):
        from muad_common import SharedSettings

        settings = SharedSettings()
        if settings.database_url:
            env["DATABASE_URL"] = settings.database_url
        if settings.redis_url:
            env["REDIS_URL"] = settings.redis_url
        env["INTERNAL_SERVICE_TOKEN"] = env.get(
            "INTERNAL_SERVICE_TOKEN", "test-internal-service-token"
        )
    with subprocess.Popen(
        [sys.executable, "-m", "pytest", "-q", *targets], cwd=ROOT,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True, env=env,
    ) as process:
        try:
            stdout, stderr = process.communicate(timeout=180)
        finally:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)
        assert process.returncode == 0, stdout[-4000:] + stderr[-1000:]
        assert re.search(r"\b[1-9]\d* passed\b", stdout), stdout[-1000:]
        assert not re.search(r"\b\d+ skipped\b", stdout), stdout[-1000:]


def test_s_04() -> None:
    _pytest(
        "tests/sdk/test_egress_budget_assembly.py",
        "tests/sdk/test_request_budget.py::test_b_01_atomic_parent_and_child_rules",
        "tests/sdk/test_platform_sliding_session.py::test_script_cannot_override_execution_identity",
    )
    _pytest("tests/acceptance/runtime/test_egress_budget.py")


def test_e_02() -> None:
    _pytest(
        "tests/sdk/test_egress_budget_lifecycle.py::test_sandbox_budget_denial_reaps_running_process_group",
        "tests/sdk/test_egress_budget_stop.py",
    )


def test_e_03() -> None:
    _pytest("tests/sdk/test_egress_budget_lifecycle.py::test_new_process_segment_starts_a_new_local_counter")
    _pytest("tests/acceptance/runtime/test_egress_budget.py")
