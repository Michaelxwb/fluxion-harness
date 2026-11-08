from pathlib import Path

import pytest
from muad_agent_core.skill import SkillExecutionRequest
from muad_contracts.skill_invocation import (
    MAX_SKILL_ARGV_BYTES,
    MAX_SKILL_ARGV_ITEMS,
    validate_skill_argv,
)


@pytest.mark.parametrize("value", [None, "--minutes 10", {}, [1], [True], ["a\0b"], ["\ud800"]])
def test_invalid_argv_is_rejected(value: object) -> None:
    with pytest.raises(ValueError, match="argv"):
        validate_skill_argv(value)


def test_argv_preserves_empty_values_spaces_and_repeated_flags() -> None:
    values = ["--company-id", "123", "--company-id", "456", "2026-11-07 10:00", "", "$(id); *"]
    assert validate_skill_argv(values) == tuple(values)
    request = SkillExecutionRequest(ready_dir=Path("."), input={}, timeout_sec=10, argv=values)
    values.clear()
    assert len(request.argv) == 7


def test_argv_limits_are_byte_based_and_inclusive() -> None:
    assert validate_skill_argv([""] * MAX_SKILL_ARGV_ITEMS) == ("",) * MAX_SKILL_ARGV_ITEMS
    assert validate_skill_argv(["x" * (MAX_SKILL_ARGV_BYTES - 1)])
    for values in ([""] * (MAX_SKILL_ARGV_ITEMS + 1), ["x" * MAX_SKILL_ARGV_BYTES],
                   ["说" * (MAX_SKILL_ARGV_BYTES // 3 + 1)]):
        with pytest.raises(ValueError, match="limit"):
            validate_skill_argv(values)
