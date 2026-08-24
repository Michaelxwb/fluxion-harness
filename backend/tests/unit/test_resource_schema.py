from __future__ import annotations

import pytest

from fluxion.resources import ResourceDefinition, ResourceKind, ResourceStatus, RuntimeProfile


def test_E_R04_definition_rejects_plaintext_credential() -> None:
    with pytest.raises(ValueError, match="plaintext secret"):
        ResourceDefinition(
            kind=ResourceKind.RUNTIME_PROFILE,
            id="asst",
            tenant_id="tenant-a",
            version="1",
            status=ResourceStatus.DRAFT,
            spec_json={
                "name": "asst",
                "model_policy": {"provider_api_key": "sk-live"},
            },
        )


def test_E_R04_runtime_profile_allows_secret_ref_only() -> None:
    profile = RuntimeProfile(
        id="asst",
        version="1",
        prompt={"text": "help"},
        model_policy={"provider_secret_ref": "secret://tenant-a/openai"},
    )

    assert profile.model_policy["provider_secret_ref"] == "secret://tenant-a/openai"


def test_E_R04_binding_style_credential_field_is_rejected_in_definition() -> None:
    with pytest.raises(ValueError, match="credential"):
        ResourceDefinition(
            kind=ResourceKind.SKILL,
            id="skill-a",
            tenant_id="tenant-a",
            version="1",
            spec_json={"credential": {"token": "plain"}},
        )


@pytest.mark.parametrize(
    "spec_json",
    [
        {"provider_secret_ref": "sk-live"},
        {"secret_ref": "plain"},
        {"credential_ref": "plain"},
        {"foo_secret_ref": "plain"},
    ],
)
def test_E_R04_plaintext_under_secret_ref_keys_is_rejected(spec_json: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="plaintext secret"):
        ResourceDefinition(
            kind=ResourceKind.RUNTIME_PROFILE,
            id="asst",
            tenant_id="tenant-a",
            version="1",
            spec_json=spec_json,
        )


def test_E_R04_secret_ref_key_with_secret_uri_passes() -> None:
    definition = ResourceDefinition(
        kind=ResourceKind.RUNTIME_PROFILE,
        id="asst",
        tenant_id="tenant-a",
        version="1",
        spec_json={"provider_secret_ref": "secret://tenant-a/openai"},
    )
    assert definition.spec_json["provider_secret_ref"] == "secret://tenant-a/openai"


@pytest.mark.parametrize(
    ("id_value", "version_value"),
    [
        ("", "1"),
        ("asst", ""),
        ("id" * 200, "1"),
        ("asst", "v" * 100),
    ],
)
def test_E_R04_invalid_id_or_version_is_rejected(
    id_value: str, version_value: str
) -> None:
    with pytest.raises(ValueError):
        ResourceDefinition(
            kind=ResourceKind.RUNTIME_PROFILE,
            id=id_value,
            tenant_id="tenant-a",
            version=version_value,
            spec_json={"prompt": "hi"},
        )
