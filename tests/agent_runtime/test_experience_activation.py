"""S-06 / S-08 acceptance skeletons: activation gate, audit and rollback (pending TASK-007)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.skip(reason="pending TASK-007")


def test_s06_activation_gate_requires_replay_improvement() -> None:
    raise AssertionError("pending implementation: see TASK-007")


def test_s08_activation_and_retirement_are_audited() -> None:
    raise AssertionError("pending implementation: see TASK-007")
