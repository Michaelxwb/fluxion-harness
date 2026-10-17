"""B-204: strict model resource, fenced reservation and settlement contracts."""

from datetime import UTC, datetime, timedelta
from importlib import import_module
from uuid import uuid4

import pytest
from pydantic import BaseModel, ValidationError

pytestmark = pytest.mark.unit
START = datetime(2026, 10, 17, tzinfo=UTC)


def _contract(name: str) -> type[BaseModel]:
    value: object = getattr(import_module("muad_contracts.model_pool"), name)
    assert isinstance(value, type) and issubclass(value, BaseModel)
    return value


def _credential() -> dict[str, object]:
    return {"key": "key-a", "provider_key": "openai", "base_url": "https://model.example/v1",
            "quota_group_id": str(uuid4()), "max_inflight": 8}


def _reservation(kind: str = "RUN") -> dict[str, object]:
    owner_key = {"RUN": "run_id", "MEMORY_JOB": "memory_job_id", "PROBE": "probe_id"}[kind]
    return {
        "call_attempt_id": str(uuid4()), "model_id": str(uuid4()), "deployment_id": str(uuid4()),
        "credential_id": str(uuid4()), "credential_revision": 1,
        "key_resource_id": str(uuid4()), "quota_group_id": str(uuid4()),
        "owner": {"kind": kind, owner_key: str(uuid4())},
        "workload": {"RUN": "INTERACTIVE", "MEMORY_JOB": "BACKGROUND", "PROBE": "PROBE"}[kind],
        "deadline_at": START + timedelta(minutes=1), "reserved_tokens": 2048,
    }


def test_b204_resources_preserve_reference_identity_without_copying_secrets() -> None:
    quota = _contract("ModelQuotaGroupInput")(key="account-a", max_inflight=16, rpm=60, tpm=None)
    assert quota.model_dump() == {"key": "account-a", "max_inflight": 16, "rpm": 60, "tpm": None}
    credential = _contract("ModelCredentialConfig")(**_credential(), id=uuid4(), revision=1)
    public = credential.model_dump(mode="json")
    assert public["max_inflight"] == 8 and public["enabled"] is True
    assert "api_key" not in public and "api_key_digest" not in public and "key_resource_id" not in public
    deployment = _contract("ModelDeploymentInput")(key="member-a", credential_id=credential.id)
    assert deployment.weight == 1 and deployment.credential_id == credential.id
    assert "api_key" not in deployment.model_dump() and "model_id" not in deployment.model_dump()


@pytest.mark.parametrize("field", ["max_inflight", "rpm", "tpm"])
@pytest.mark.parametrize("value", [0, -1, True, 1.0, "1"])
def test_b204_quota_numbers_require_positive_integers(field: str, value: object) -> None:
    with pytest.raises(ValidationError, match=field):
        _contract("ModelQuotaGroupInput")(**{"key": "account-a", "max_inflight": 1, field: value})


def test_b204_missing_resource_capacity_is_not_given_a_production_default() -> None:
    with pytest.raises(ValidationError, match="max_inflight"):
        _contract("ModelQuotaGroupInput")(key="account-a")
    payload = _credential()
    del payload["max_inflight"]
    with pytest.raises(ValidationError, match="max_inflight"):
        _contract("ModelCredentialCreate")(**payload, api_key="key-canary")


@pytest.mark.parametrize("value", [0, 101, True, 1.0, "1"])
def test_b204_member_weight_is_strict_and_bounded(value: object) -> None:
    with pytest.raises(ValidationError, match="weight"):
        _contract("ModelDeploymentInput")(key="member-a", credential_id=uuid4(), weight=value)


@pytest.mark.parametrize("key", ["api_key", "api_key_digest", "key_resource_id", "protocol", "model_id"])
def test_b204_member_input_cannot_copy_credentials_or_override_model_semantics(key: str) -> None:
    with pytest.raises(ValidationError, match=key):
        _contract("ModelDeploymentInput")(**{"key": "member-a", "credential_id": str(uuid4()), key: "canary"})


def test_b204_secret_input_and_internal_wire_are_distinct_from_public_config() -> None:
    secret = "model-credential-canary"
    create = _contract("ModelCredentialCreate")(**_credential(), api_key=secret)
    assert create.api_key.get_secret_value() == secret
    assert secret not in repr(create) and "api_key" not in create.model_dump()
    with pytest.raises(ValidationError, match="api_key"):
        _contract("ModelCredentialConfig")(**_credential(), id=uuid4(), revision=1, api_key=secret)
    selected = _contract("SelectedModelCredential")(
        model_id=uuid4(), deployment_id=uuid4(), credential_id=uuid4(), credential_revision=2,
        key_resource_id=uuid4(), quota_group_id=uuid4(), provider_key="openai",
        base_url="https://model.example/v1", api_key=secret,
    )
    assert secret not in repr(selected) and "api_key" not in selected.model_dump(mode="json")
    wire = selected.to_internal_payload()
    assert wire["api_key"] == secret and wire["credential_revision"] == 2
    assert set(wire) == set(type(selected).model_fields)


@pytest.mark.parametrize("secret", ["", "  "])
def test_b204_blank_api_keys_are_rejected(secret: str) -> None:
    with pytest.raises(ValidationError, match="api_key"):
        _contract("ModelCredentialCreate")(**_credential(), api_key=secret)


@pytest.mark.parametrize("kind", ["RUN", "MEMORY_JOB", "PROBE"])
def test_b204_call_owner_is_discriminated_and_round_trips(kind: str) -> None:
    model = _contract("ModelCapacityReservationRequest")(**_reservation(kind))
    assert model.owner.kind == kind
    assert model.reserved_tokens == 2048
    assert type(model).model_validate_json(model.model_dump_json()) == model


@pytest.mark.parametrize(
    "owner",
    [{}, {"kind": "RUN"}, {"kind": "OTHER", "run_id": str(uuid4())},
     {"kind": "RUN", "run_id": str(uuid4()), "probe_id": str(uuid4())}],
)
def test_b204_missing_or_ambiguous_owners_are_rejected(owner: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="owner"):
        _contract("ModelCapacityReservationRequest")(**{**_reservation(), "owner": owner})


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "1"])
def test_b204_lease_epochs_are_positive_and_strict(value: object) -> None:
    with pytest.raises(ValidationError, match="epoch"):
        _contract("ModelLeaseFence")(epoch=value, lease_owner="runtime-a")


def test_b204_lease_timestamps_are_aware_ordered_and_deadline_bounded() -> None:
    payload = {**_reservation(), "reservation_id": str(uuid4()), "epoch": 1,
               "lease_owner": "runtime-a", "issued_at": START, "lease_until": START + timedelta(seconds=30)}
    lease = _contract("ModelCapacityLease")(**payload)
    assert lease.state == "ACTIVE" and lease.retains_capacity is True
    for changes in ({"issued_at": START.replace(tzinfo=None)}, {"lease_until": START},
                    {"lease_until": START + timedelta(seconds=61)}):
        with pytest.raises(ValidationError):
            _contract("ModelCapacityLease")(**{**payload, **changes})
    unknown = _contract("ModelCapacityLease")(**{**payload, "state": "UNKNOWN"})
    assert unknown.retains_capacity is True


@pytest.mark.parametrize("field", ["input_tokens", "output_tokens"])
@pytest.mark.parametrize("value", [-1, True, 1.0, "1"])
def test_b204_usage_is_nonnegative_strict_counts(field: str, value: object) -> None:
    with pytest.raises(ValidationError, match=field):
        _contract("ModelTokenUsage")(**{"input_tokens": 0, "output_tokens": 0, field: value})


@pytest.mark.parametrize("outcome", ["UNKNOWN", "FAILED"])
def test_b204_unknown_remote_completion_retains_capacity_and_unreported_usage(outcome: str) -> None:
    settlement = _contract("ModelCallSettlement")(epoch=1, lease_owner="runtime-a", outcome=outcome)
    assert settlement.can_release_capacity is False
    assert settlement.usage is None
    assert "release_after_sec" not in settlement.model_dump()
    with pytest.raises(ValidationError, match="release_after_sec"):
        _contract("ModelCallSettlement")(
            epoch=1, lease_owner="runtime-a", outcome=outcome, release_after_sec=30,
        )


@pytest.mark.parametrize("kind", ["PROVIDER_CONFIRMED", "SERVER_HARD_TIMEOUT"])
def test_b204_only_evidenced_remote_completion_allows_release(kind: str) -> None:
    proof = {"kind": kind, "reference": "provider-request-a", "completed_at": START}
    settlement = _contract("ModelCallSettlement")(
        epoch=2, lease_owner="runtime-b", outcome="FAILED", remote_completion=proof,
    )
    assert settlement.can_release_capacity is True and settlement.usage is None
    with pytest.raises(ValidationError, match="UNKNOWN"):
        _contract("ModelCallSettlement")(
            epoch=2, lease_owner="runtime-b", outcome="UNKNOWN", remote_completion=proof,
        )


def test_b204_not_sent_and_success_have_distinct_settlement_requirements() -> None:
    model = _contract("ModelCallSettlement")
    not_sent = model(epoch=1, lease_owner="runtime-a", outcome="NOT_SENT")
    assert not_sent.can_release_capacity is True
    with pytest.raises(ValidationError):
        model(epoch=1, lease_owner="runtime-a", outcome="NOT_SENT",
              usage={"input_tokens": 0, "output_tokens": 1})
    with pytest.raises(ValidationError):
        model(epoch=1, lease_owner="runtime-a", outcome="SUCCEEDED")
    success = model(epoch=1, lease_owner="runtime-a", outcome="SUCCEEDED",
                    remote_completion={"kind": "PROVIDER_CONFIRMED", "reference": "response-a",
                                       "completed_at": START}, usage={"input_tokens": 10, "output_tokens": 2})
    assert success.can_release_capacity is True and success.usage.total_tokens == 12


def test_b204_probe_wire_has_exact_request_shape_and_aware_deadline() -> None:
    payload = {"probe_id": str(uuid4()), "model_id": str(uuid4()), "credential_id": str(uuid4()),
               "attempt_id": str(uuid4()), "deadline_at": START, "reserved_tokens": 1}
    probe = _contract("ProbeCapacityRequest")(**payload)
    assert set(probe.model_dump()) == set(payload)
    changes_list = [
        {"deadline_at": START.replace(tzinfo=None)}, {"reserved_tokens": 0}, {"run_id": str(uuid4())},
    ]
    for changes in changes_list:
        with pytest.raises(ValidationError):
            _contract("ProbeCapacityRequest")(**{**payload, **changes})


@pytest.mark.parametrize("base_url", ["file:///tmp/key", "http://", "https://user:secret@model.example", "http://x:bad"])
def test_b204_endpoint_rejects_non_http_and_embedded_credentials(base_url: str) -> None:
    with pytest.raises(ValidationError, match="base_url"):
        _contract("ModelCredentialCreate")(**{**_credential(), "base_url": base_url}, api_key="key-canary")


@pytest.mark.parametrize("key", ["", " ", "x" * 129])
def test_b204_resource_keys_match_persistent_shape(key: str) -> None:
    with pytest.raises(ValidationError, match="key"):
        _contract("ModelQuotaGroupInput")(key=key, max_inflight=1)


def test_b204_counters_cannot_overflow_the_declared_storage_types() -> None:
    quota = _contract("ModelQuotaGroupInput")(key="account-a", max_inflight=(1 << 31) - 1, rpm=(1 << 63) - 1)
    assert quota.rpm == (1 << 63) - 1
    for changes in ({"max_inflight": 1 << 31}, {"rpm": 1 << 63}):
        with pytest.raises(ValidationError):
            _contract("ModelQuotaGroupInput")(**{"key": "account-a", "max_inflight": 1, **changes})
    with pytest.raises(ValidationError, match="epoch"):
        _contract("ModelLeaseFence")(epoch=1 << 63, lease_owner="runtime-a")


def test_b204_settled_lease_does_not_retain_an_inflight_slot() -> None:
    lease = _contract("ModelCapacityLease")(
        **_reservation(), reservation_id=uuid4(), epoch=1, lease_owner="runtime-a",
        issued_at=START, lease_until=START + timedelta(seconds=30), state="SETTLED",
    )
    assert lease.retains_capacity is False
    with pytest.raises(ValidationError, match="retains_capacity"):
        _contract("ModelCapacityLease")(**{**lease.model_dump(), "retains_capacity": True})


@pytest.mark.parametrize("proof", [
    {"kind": "LOCAL_TIMEOUT", "reference": "local-clock", "completed_at": START},
    {"kind": "PROVIDER_CONFIRMED", "reference": "  ", "completed_at": START},
    {"kind": "SERVER_HARD_TIMEOUT", "reference": "server-a", "completed_at": START.replace(tzinfo=None)},
])
def test_b204_local_timeout_and_malformed_proofs_are_not_completion_evidence(
    proof: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="remote_completion"):
        _contract("ModelCallSettlement")(
            epoch=1, lease_owner="runtime-a", outcome="FAILED", remote_completion=proof,
        )
