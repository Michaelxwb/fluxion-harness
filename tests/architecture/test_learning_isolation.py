"""B-06 acceptance skeleton: replay/eval stay out of the Run request path (pending TASK-004)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.skip(reason="pending TASK-004")


def test_b06_replay_is_not_imported_by_the_request_path() -> None:
    raise AssertionError("pending implementation: see TASK-004")


def test_b06_replay_uses_background_model_budget() -> None:
    raise AssertionError("pending implementation: see TASK-004")
