from __future__ import annotations

import pytest
from muad_platform_sdk import EnvSecretProvider, SecretNotFoundError, SecretValue
from muad_platform_sdk.credential import secret_env_var


def test_secret_env_var_maps_namespace_and_name() -> None:
    assert secret_env_var("secret://wecom/bot-1") == "MUAD_SECRET__WECOM__BOT_1"
    assert secret_env_var("secret://MSS.Platform/shared-ak") == "MUAD_SECRET__MSS_PLATFORM__SHARED_AK"


@pytest.mark.parametrize("secret_ref", ["wecom/bot-1", "secret://wecom", "secret:///bot-1", "secret://wecom/"])
def test_secret_env_var_rejects_malformed_refs(secret_ref: str) -> None:
    with pytest.raises(ValueError):
        secret_env_var(secret_ref)


async def test_get_returns_secret_value_with_default_version() -> None:
    provider = EnvSecretProvider({"MUAD_SECRET__WECOM__BOT_1": "top-secret"})
    secret = await provider.get("secret://wecom/bot-1")
    assert secret.value == "top-secret"
    assert secret.version == "1"


async def test_get_reads_version_override() -> None:
    provider = EnvSecretProvider(
        {
            "MUAD_SECRET__WECOM__BOT_1": "top-secret",
            "MUAD_SECRET__WECOM__BOT_1_VERSION": "v7",
        }
    )
    secret = await provider.get("secret://wecom/bot-1")
    assert secret.version == "v7"


async def test_get_falls_back_to_default_version_when_version_empty() -> None:
    provider = EnvSecretProvider(
        {
            "MUAD_SECRET__WECOM__BOT_1": "top-secret",
            "MUAD_SECRET__WECOM__BOT_1_VERSION": "",
        }
    )
    secret = await provider.get("secret://wecom/bot-1")
    assert secret.version == "1"


async def test_missing_secret_raises_typed_error() -> None:
    provider = EnvSecretProvider({})
    with pytest.raises(SecretNotFoundError) as excinfo:
        await provider.get("secret://wecom/bot-1")
    assert excinfo.value.secret_ref == "secret://wecom/bot-1"


async def test_empty_secret_is_treated_as_missing() -> None:
    provider = EnvSecretProvider({"MUAD_SECRET__WECOM__BOT_1": ""})
    with pytest.raises(SecretNotFoundError):
        await provider.get("secret://wecom/bot-1")


async def test_default_provider_reads_process_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MUAD_SECRET__DEMO__TOKEN", "from-env")
    provider = EnvSecretProvider()
    secret = await provider.get("secret://demo/token")
    assert secret == SecretValue(value="from-env", version="1")


def test_secret_value_repr_does_not_leak_value() -> None:
    secret = SecretValue(value="top-secret", version="v1")
    assert "top-secret" not in repr(secret)
    assert "v1" in repr(secret)
