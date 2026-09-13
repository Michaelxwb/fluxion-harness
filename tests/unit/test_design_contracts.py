"""Validate documented payloads, including the failing inputs from the review."""

from copy import deepcopy
from typing import cast

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from tests.design_contracts import (
    AGENT_DOC,
    AGENT_FRONTEND,
    CAPABILITY_DOC,
    capability_examples,
    object_contract,
)

CAPABILITY_SCHEMA = object_contract(CAPABILITY_DOC, "capability-implementation-schema")
CAPABILITY_VALIDATOR = Draft202012Validator(CAPABILITY_SCHEMA, format_checker=FormatChecker())
BINDINGS_VALIDATOR = Draft202012Validator(
    object_contract(AGENT_DOC, "agent-bindings-schema"), format_checker=FormatChecker()
)


@pytest.mark.parametrize("payload", capability_examples())
def test_each_implementation_form_has_a_valid_write_payload(payload: dict[str, object]) -> None:
    CAPABILITY_VALIDATOR.validate(payload)


@pytest.mark.parametrize("field", ["path", "method"])
def test_platform_service_address_is_required(field: str) -> None:
    payload = deepcopy(capability_examples()[0])
    del cast(dict[str, object], payload["config"])[field]
    assert not CAPABILITY_VALIDATOR.is_valid(payload)


@pytest.mark.parametrize("implementation_index", [1, 2, 3])
def test_user_platform_credentials_cannot_be_used_by_other_providers(implementation_index: int) -> None:
    payload = deepcopy(capability_examples()[implementation_index])
    payload["auth_mode"] = "USER_PLATFORM"
    payload.pop("shared_secret_ref", None)
    assert not CAPABILITY_VALIDATOR.is_valid(payload)


@pytest.mark.parametrize("reference", [None, "", "   "])
def test_shared_secret_requires_a_nonblank_reference(reference: object) -> None:
    payload = deepcopy(capability_examples()[1])
    if reference is None:
        del payload["shared_secret_ref"]
    else:
        payload["shared_secret_ref"] = reference
    assert not CAPABILITY_VALIDATOR.is_valid(payload)


def test_shared_secret_cannot_hide_in_config() -> None:
    payload = deepcopy(capability_examples()[1])
    cast(dict[str, object], payload["config"])["shared_secret_ref"] = payload.pop("shared_secret_ref")
    assert not CAPABILITY_VALIDATOR.is_valid(payload)


def test_none_auth_does_not_accept_a_secret_reference() -> None:
    payload = deepcopy(capability_examples()[1])
    payload["auth_mode"] = "NONE"
    assert not CAPABILITY_VALIDATOR.is_valid(payload)


@pytest.mark.parametrize("modes", [[], ["SYNC", "SYNC"], ["OTHER"], True, None])
def test_invalid_support_sets_are_rejected(modes: object) -> None:
    payload = deepcopy(capability_examples()[0])
    payload["supported_execution_modes"] = modes
    assert not CAPABILITY_VALIDATOR.is_valid(payload)


def test_retired_async_boolean_is_not_a_second_source_of_truth() -> None:
    payload = deepcopy(capability_examples()[0])
    payload["async_submittable"] = True
    assert not CAPABILITY_VALIDATOR.is_valid(payload)


@pytest.mark.parametrize(
    ("modes", "requested", "accepted"),
    [
        (["SYNC"], "SYNC", True),
        (["SYNC"], "ASYNC", False),
        (["ASYNC"], "SYNC", False),
        (["ASYNC"], "ASYNC", True),
        (["SYNC", "ASYNC"], "SYNC", True),
        (["SYNC", "ASYNC"], "ASYNC", True),
        (["ASYNC", "SYNC"], "SYNC", True),
    ],
)
def test_step_mode_must_belong_to_supported_modes(
    modes: list[str], requested: str, accepted: bool
) -> None:
    schema = {**CAPABILITY_SCHEMA, "$ref": "#/$defs/step_mode_selection"}
    # Apply the selection definition without the implementation's top-level oneOf.
    schema.pop("oneOf")
    validator = Draft202012Validator(schema)
    assert validator.is_valid({"supported_execution_modes": modes, "execution_mode": requested}) == accepted


def test_inline_binding_example_preserves_existing_and_untouched_bindings() -> None:
    example = object_contract(AGENT_FRONTEND, "agent-binding-inline-example")
    before = cast(dict[str, object], example["before"])
    request = cast(dict[str, object], example["request"])
    after = cast(dict[str, object], example["after"])
    BINDINGS_VALIDATOR.validate(request)
    previous = cast(list[str], before["capability_ids"])
    submitted = cast(list[str], request["capability_ids"])
    assert set(previous) < set(submitted), "Inline add must submit the full existing dimension"
    assert after["capability_ids"] == submitted
    assert "skill_ids" not in request and "service_ids" not in request
    assert after["skill_ids"] == before["skill_ids"]
    assert after["service_ids"] == before["service_ids"]


@pytest.mark.parametrize(
    "payload",
    [{"revision": 1}, {"revision": 1, "add": []}, {"capability_ids": []}, {"revision": 0, "skill_ids": []}],
)
def test_binding_requests_reject_ambiguous_or_unversioned_updates(payload: dict[str, object]) -> None:
    assert not BINDINGS_VALIDATOR.is_valid(payload)


def test_explicit_empty_binding_dimension_is_a_valid_removal() -> None:
    BINDINGS_VALIDATOR.validate({"revision": 1, "capability_ids": []})
