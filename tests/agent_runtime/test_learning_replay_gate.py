"""S-10 / S-15 acceptance skeletons: two-arm replay gate and batch item judging (pending TASK-004)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.skip(reason="pending TASK-004")


def test_s10_activation_gate_requires_two_arm_improvement_and_reuses_baseline() -> None:
    raise AssertionError("pending implementation: see TASK-004")


def test_s15_batch_items_missing_or_failed_make_the_case_fail() -> None:
    raise AssertionError("pending implementation: see TASK-004")
