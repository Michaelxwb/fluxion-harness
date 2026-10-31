"""S-02 acceptance skeletons: memory injection attribution and write dedupe (pending TASK-003)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.skip(reason="pending TASK-003")


def test_s02_injected_memory_references_match_the_model_request() -> None:
    raise AssertionError("pending implementation: see TASK-003")


def test_s02_unchanged_selection_is_referenced_not_rewritten() -> None:
    raise AssertionError("pending implementation: see TASK-003")
