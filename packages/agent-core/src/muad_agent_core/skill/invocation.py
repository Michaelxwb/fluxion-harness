"""Shape and rendered-value validation for a Skill invocation contract.

The contract covers the **default entrypoint** only; callers decide whether the selected
script is the default entrypoint. This module has no I/O and does not execute the Skill.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

import jsonschema
from jsonschema import FormatChecker
from muad_contracts import SkillInvocationContract
from muad_contracts.skill_invocation import validate_skill_argv

ValidationPhase = Literal["shape", "rendered"]


class SkillInvocationError(ValueError):
    """A fixable invocation error; callers translate it to a field/tool error."""

    def __init__(
        self,
        *,
        field: str,
        reason: str,
        repair: str,
        invocation: str,
        required: Sequence[str] = (),
        allowed_values: Sequence[str] = (),
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        self.field = field
        self.reason = reason
        self.repair = repair
        self.invocation = invocation
        self.required = tuple(required)
        self.allowed_values = tuple(allowed_values)
        self.detail = dict(detail or {})
        super().__init__(f"{field}: {reason}")

    def as_detail(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "field": self.field,
            "reason": self.reason,
            "repair": self.repair,
            "invocation": self.invocation,
        }
        if self.required:
            data["required"] = list(self.required)
        if self.allowed_values:
            data["allowed_values"] = list(self.allowed_values)
        if self.detail:
            data["detail"] = dict(self.detail)
        return data


def validate_skill_invocation(
    contract: SkillInvocationContract,
    *,
    input_data: Mapping[str, Any],
    argv: Sequence[str],
    phase: ValidationPhase,
) -> None:
    """Validate one call against the declared channel.

    ``shape`` checks structure, required keys, unknown keys and templates; ``rendered``
    additionally checks concrete values (``format``/``pattern``/``enum``/range).
    """
    schema = contract.contract_schema
    properties = schema["properties"]
    if contract.invocation == "argv":
        if input_data:
            raise SkillInvocationError(
                field="payload.input",
                reason="CHANNEL_MISMATCH",
                repair="USE_DECLARED_CHANNEL",
                invocation="argv",
            )
        parsed = _parse_argv(argv, properties)
        _check_required(
            parsed,
            schema,
            field="payload.argv",
            invocation="argv",
            allowed_values=_argv_allowed_values(properties),
        )
        if phase == "rendered":
            _validate_values(
                parsed,
                schema,
                field="payload.argv",
                invocation="argv",
                allowed_values=_argv_allowed_values(properties),
            )
        return

    if argv:
        raise SkillInvocationError(
            field="payload.argv",
            reason="CHANNEL_MISMATCH",
            repair="USE_DECLARED_CHANNEL",
            invocation="stdin",
        )
    if not isinstance(input_data, Mapping):
        raise SkillInvocationError(
            field="payload.input",
            reason="INVALID_TYPE",
            repair="PROVIDE_SKILL_INPUT",
            invocation="stdin",
        )
    allowed_values = tuple(properties)
    _check_unknown(
        input_data,
        properties,
        field="payload.input",
        invocation="stdin",
        allowed_values=allowed_values,
    )
    _check_required(
        input_data,
        schema,
        field="payload.input",
        invocation="stdin",
        allowed_values=allowed_values,
    )
    if phase == "rendered":
        _validate_values(
            dict(input_data),
            schema,
            field="payload.input",
            invocation="stdin",
            allowed_values=allowed_values,
        )


def _parse_argv(argv: Sequence[str], properties: Mapping[str, Any]) -> dict[str, Any]:
    try:
        tokens = validate_skill_argv(argv)
    except ValueError as exc:
        raise SkillInvocationError(
            field="payload.argv",
            reason="INVALID",
            repair="PROVIDE_SKILL_ARGV",
            invocation="argv",
            detail={"message": str(exc)},
        ) from exc

    parsed: dict[str, Any] = {}
    allowed_values = _argv_allowed_values(properties)
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "--" or not token.startswith("--"):
            reason = (
                "POSITIONAL_NOT_SUPPORTED"
                if not token.startswith("-")
                else "SHORT_OPTION_NOT_SUPPORTED"
            )
            raise SkillInvocationError(
                field="payload.argv",
                reason=reason,
                repair="USE_LONG_OPTIONS",
                invocation="argv",
            )
        name, separator, value = token[2:].partition("=")
        if not name:
            raise SkillInvocationError(
                field="payload.argv",
                reason="INVALID_OPTION",
                repair="USE_LONG_OPTIONS",
                invocation="argv",
            )
        if name not in properties:
            raise SkillInvocationError(
                field="payload.argv",
                reason="UNKNOWN_OPTION",
                repair="PROVIDE_SKILL_ARGV",
                invocation="argv",
                allowed_values=allowed_values,
            )
        if name in parsed:
            raise SkillInvocationError(
                field="payload.argv",
                reason="DUPLICATE_OPTION",
                repair="PROVIDE_SKILL_ARGV",
                invocation="argv",
            )
        definition = properties[name]
        is_boolean = isinstance(definition, Mapping) and definition.get("type") == "boolean"
        if separator:
            if is_boolean:
                raise SkillInvocationError(
                    field="payload.argv",
                    reason="BOOLEAN_MUST_BE_FLAG",
                    repair="PROVIDE_SKILL_ARGV",
                    invocation="argv",
                    allowed_values=(f"--{name}",),
                )
            parsed[name] = value
        elif is_boolean:
            parsed[name] = True
        else:
            index += 1
            if index >= len(tokens) or tokens[index].startswith("--"):
                raise SkillInvocationError(
                    field="payload.argv",
                    reason="MISSING_VALUE",
                    repair="PROVIDE_SKILL_ARGV",
                    invocation="argv",
                    allowed_values=allowed_values,
                )
            parsed[name] = tokens[index]
        index += 1
    return parsed


def _check_required(
    values: Mapping[str, Any],
    schema: Mapping[str, Any],
    *,
    field: str,
    invocation: str,
    allowed_values: Sequence[str],
) -> None:
    missing = [name for name in schema.get("required", []) if name not in values]
    if not missing:
        return
    raise SkillInvocationError(
        field=field,
        reason="MISSING_REQUIRED",
        repair="PROVIDE_SKILL_ARGV" if invocation == "argv" else "PROVIDE_SKILL_INPUT",
        invocation=invocation,
        required=missing,
        allowed_values=allowed_values,
    )


def _check_unknown(
    values: Mapping[str, Any],
    properties: Mapping[str, Any],
    *,
    field: str,
    invocation: str,
    allowed_values: Sequence[str],
) -> None:
    unknown = [name for name in values if name not in properties]
    if not unknown:
        return
    raise SkillInvocationError(
        field=field,
        reason="UNKNOWN_ARGUMENT",
        repair="PROVIDE_SKILL_INPUT",
        invocation=invocation,
        detail={"unknown": unknown},
        allowed_values=allowed_values,
    )


def _validate_values(
    values: dict[str, Any],
    schema: Mapping[str, Any],
    *,
    field: str,
    invocation: str,
    allowed_values: Sequence[str],
) -> None:
    try:
        jsonschema.validate(
            instance=values,
            schema=schema,
            format_checker=FormatChecker(),
        )
    except jsonschema.SchemaError as exc:
        raise SkillInvocationError(
            field=field,
            reason="CONTRACT_INVALID",
            repair="REIMPORT_SKILL",
            invocation=invocation,
            detail={"message": exc.message},
        ) from exc
    except jsonschema.ValidationError as exc:
        reason = "INVALID_FORMAT" if exc.validator == "format" else "INVALID_VALUE"
        path = ".".join(str(part) for part in exc.absolute_path) or field
        raise SkillInvocationError(
            field=f"{field}.{path}" if path != field else field,
            reason=reason,
            repair="FIX_SKILL_ARGV" if invocation == "argv" else "FIX_SKILL_INPUT",
            invocation=invocation,
            allowed_values=allowed_values,
            detail={
                "validator": str(exc.validator),
                "expected": exc.validator_value,
            },
        ) from exc


def _argv_allowed_values(properties: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(f"--{name}" for name in properties)
