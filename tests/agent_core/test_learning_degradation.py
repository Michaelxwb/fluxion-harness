"""E-01 acceptance skeleton: learning pipeline degrades without failing the run (pending TASK-003)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.skip(reason="pending TASK-003")


def test_e01_learning_failure_degrades_to_no_learning() -> None:
    raise AssertionError("pending implementation: see TASK-003")
