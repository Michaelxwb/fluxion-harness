"""S-13 acceptance skeleton: experience hits for non-standard tasks and family switch (pending TASK-007)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.skip(reason="pending TASK-007")


def test_s13_goal_match_hits_experience_without_skill() -> None:
    raise AssertionError("pending implementation: see TASK-007")


def test_s13_family_switch_after_skill_execution() -> None:
    raise AssertionError("pending implementation: see TASK-007")
