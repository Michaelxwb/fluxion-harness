"""Run the egress repair probes against the acceptance Redis Cluster."""
from __future__ import annotations

import os
import runpy
from pathlib import Path

import pytest

_PROBE = Path(__file__).resolve().parents[3] / (
    ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/"
    "test_egress_shared_repair.py"
)
_ACCEPTANCE_PROBE = _PROBE.with_name("test_egress_shared_acceptance.py")


@pytest.fixture(autouse=True)
def cluster_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "MUAD_CLUSTER_REDIS_URL",
        os.environ.get("MUAD_CLUSTER_REDIS_URL", "redis://127.0.0.1:6391/0"),
    )


@pytest.mark.integration
async def test_shared_egress_across_processes() -> None:
    await runpy.run_path(str(_PROBE))["test_s_01"]()


@pytest.mark.integration
async def test_shared_egress_sliding_window() -> None:
    await runpy.run_path(str(_PROBE))["test_b_01"]()


@pytest.mark.integration
async def test_shared_egress_execution_and_invocation_rules() -> None:
    await runpy.run_path(str(_ACCEPTANCE_PROBE))["test_s_02"]()


@pytest.mark.integration
async def test_shared_egress_policy_changes_keep_frozen_budget() -> None:
    await runpy.run_path(str(_PROBE))["test_b_04"]()
