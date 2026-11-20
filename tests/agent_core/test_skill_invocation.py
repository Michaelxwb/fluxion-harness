"""Skill invocation contract validation (shape and rendered values)."""

from __future__ import annotations

from typing import Any

import pytest
from muad_agent_core.skill import SkillInvocationError, validate_skill_invocation
from muad_agent_core.skill.invocation import ValidationPhase
from muad_contracts import SkillInvocationContract


def _argv_contract(**schema_overrides: Any) -> SkillInvocationContract:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "company-id": {"type": "string", "pattern": "^[0-9]+$"},
            "date": {"type": "string", "format": "date"},
            "no-wecom-push": {"type": "boolean"},
        },
        "required": ["company-id"],
        "additionalProperties": False,
    }
    schema.update(schema_overrides)
    return SkillInvocationContract.model_validate(
        {"invocation": "argv", "schema": schema}
    )


def _stdin_contract(**schema_overrides: Any) -> SkillInvocationContract:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "company_id": {"type": "string", "pattern": "^[0-9]+$"},
            "date": {"type": "string", "format": "date"},
        },
        "required": ["company_id"],
        "additionalProperties": False,
    }
    schema.update(schema_overrides)
    return SkillInvocationContract.model_validate(
        {"invocation": "stdin", "schema": schema}
    )


def _assert_error(
    contract: SkillInvocationContract,
    *,
    input_data: dict[str, Any],
    argv: list[str],
    phase: ValidationPhase,
    reason: str,
) -> SkillInvocationError:
    with pytest.raises(SkillInvocationError) as error:
        validate_skill_invocation(
            contract,
            input_data=input_data,
            argv=argv,
            phase=phase,
        )
    assert error.value.reason == reason
    return error.value


def test_argv_shape_accepts_template_values() -> None:
    validate_skill_invocation(
        _argv_contract(),
        input_data={},
        argv=[
            "--company-id",
            "22921947",
            "--date",
            "{{previous_day}}",
            "--no-wecom-push",
        ],
        phase="shape",
    )


def test_argv_rendered_accepts_concrete_values() -> None:
    validate_skill_invocation(
        _argv_contract(),
        input_data={},
        argv=["--company-id", "22921947", "--date", "2026-11-19", "--no-wecom-push"],
        phase="rendered",
    )


def test_argv_rendered_rejects_invalid_date() -> None:
    error = _assert_error(
        _argv_contract(),
        input_data={},
        argv=["--company-id", "22921947", "--date", "2026-13-01"],
        phase="rendered",
        reason="INVALID_FORMAT",
    )
    assert "date" in error.field


def test_argv_unknown_option_returns_allowed_values() -> None:
    error = _assert_error(
        _argv_contract(),
        input_data={},
        argv=["--company-id", "22921947", "--help"],
        phase="shape",
        reason="UNKNOWN_OPTION",
    )
    assert "--company-id" in error.allowed_values


def test_argv_duplicate_option_is_rejected() -> None:
    _assert_error(
        _argv_contract(),
        input_data={},
        argv=["--company-id", "1", "--company-id", "2"],
        phase="shape",
        reason="DUPLICATE_OPTION",
    )


@pytest.mark.parametrize(
    ("argv", "reason"),
    [
        (["-h"], "SHORT_OPTION_NOT_SUPPORTED"),
        (["--company-id", "1", "positional"], "POSITIONAL_NOT_SUPPORTED"),
        (["--company-id"], "MISSING_VALUE"),
    ],
)
def test_argv_syntax_errors(argv: list[str], reason: str) -> None:
    _assert_error(
        _argv_contract(),
        input_data={},
        argv=argv,
        phase="shape",
        reason=reason,
    )


def test_boolean_must_be_a_bare_flag() -> None:
    error = _assert_error(
        _argv_contract(),
        input_data={},
        argv=["--company-id", "1", "--no-wecom-push=true"],
        phase="shape",
        reason="BOOLEAN_MUST_BE_FLAG",
    )
    assert error.allowed_values == ("--no-wecom-push",)


def test_argv_required_missing() -> None:
    error = _assert_error(
        _argv_contract(),
        input_data={},
        argv=["--date", "2026-11-19"],
        phase="shape",
        reason="MISSING_REQUIRED",
    )
    assert "company-id" in error.required


def test_argv_rejects_non_empty_input() -> None:
    _assert_error(
        _argv_contract(),
        input_data={"company_id": "1"},
        argv=["--company-id", "1"],
        phase="shape",
        reason="CHANNEL_MISMATCH",
    )


def test_stdin_shape_allows_empty_object() -> None:
    contract = _stdin_contract(required=[])

    validate_skill_invocation(contract, input_data={}, argv=[], phase="shape")


def test_stdin_rejects_unknown_argument() -> None:
    error = _assert_error(
        _stdin_contract(),
        input_data={"company_id": "1", "extra": True},
        argv=[],
        phase="shape",
        reason="UNKNOWN_ARGUMENT",
    )
    assert "company_id" in error.allowed_values


def test_stdin_required_missing() -> None:
    error = _assert_error(
        _stdin_contract(),
        input_data={},
        argv=[],
        phase="shape",
        reason="MISSING_REQUIRED",
    )
    assert "company_id" in error.required


def test_stdin_rendered_rejects_invalid_date() -> None:
    _assert_error(
        _stdin_contract(),
        input_data={"company_id": "1", "date": "2026-13-01"},
        argv=[],
        phase="rendered",
        reason="INVALID_FORMAT",
    )


def test_stdin_rejects_non_empty_argv() -> None:
    _assert_error(
        _stdin_contract(),
        input_data={"company_id": "1"},
        argv=["--company-id", "1"],
        phase="shape",
        reason="CHANNEL_MISMATCH",
    )


def test_zero_argument_argv_contract_is_valid() -> None:
    contract = SkillInvocationContract.model_validate(
        {
            "invocation": "argv",
            "schema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        }
    )

    validate_skill_invocation(contract, input_data={}, argv=[], phase="rendered")
