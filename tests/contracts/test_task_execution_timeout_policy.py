"""Task timeout is a frozen business policy rather than a Worker process default."""

from copy import deepcopy

import pytest
from muad_contracts.platform_settings import PlatformSettingsError
from muad_contracts.snapshot import (
    TASK_SNAPSHOT_SCHEMA_VERSION,
    freeze_task_execution_timeout,
    snapshot_hash,
    task_execution_timeout,
)

pytestmark = pytest.mark.unit


def test_freezing_timeout_preserves_the_input_and_participates_in_the_hash() -> None:
    incoming = {"schema_version": TASK_SNAPSHOT_SCHEMA_VERSION, "budget": {"max_attempts": 3}}
    before = deepcopy(incoming)
    first = freeze_task_execution_timeout(incoming, 1800)
    later = freeze_task_execution_timeout(incoming, 3600)
    assert incoming == before
    assert task_execution_timeout(first) == 1800
    assert task_execution_timeout(later) == 3600
    assert first["budget"]["max_attempts"] == 3
    assert snapshot_hash(first) != snapshot_hash(later)


@pytest.mark.parametrize("budget", [{}, {"execution_timeout_sec": True}, {"execution_timeout_sec": 0},
                                    {"execution_timeout_sec": 3601}, {"execution_timeout_sec": "300"}])
def test_execution_rejects_missing_or_invalid_frozen_timeout(budget) -> None:
    with pytest.raises(PlatformSettingsError, match="task.execution_timeout_sec"):
        task_execution_timeout({"budget": budget})


def test_freezing_uses_the_creation_policy_and_does_not_mutate_an_earlier_task() -> None:
    first = freeze_task_execution_timeout({"budget": {}}, 1200)
    before = deepcopy(first)
    next_task = freeze_task_execution_timeout(first, 600)
    assert first == before
    assert task_execution_timeout(first) == 1200
    assert task_execution_timeout(next_task) == 600
