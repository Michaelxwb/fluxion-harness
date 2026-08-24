from __future__ import annotations

import pytest

from fluxion.runtime.secrets import (
    CredentialResolver,
    LocalEncryptedSecretStore,
    SecretProviderError,
)


@pytest.mark.asyncio
async def test_E_R09_local_secret_store_encrypts_and_fails_closed_on_bad_master_key() -> None:
    store = LocalEncryptedSecretStore(master_key=b"a" * 32)
    ref = await store.put("tenant-a", "mcp/weather", "weather-token")

    metadata = store.export_encrypted_records()
    assert "weather-token" not in repr(metadata)
    assert metadata[ref].ciphertext != b"weather-token"

    resolver = CredentialResolver(store)
    assert await resolver.resolve(ref) == "weather-token"

    wrong_store = LocalEncryptedSecretStore(master_key=b"b" * 32)
    wrong_store.import_encrypted_records(metadata)
    with pytest.raises(SecretProviderError) as exc_info:
        await CredentialResolver(wrong_store).resolve(ref)
    assert exc_info.value.code == "secret_decrypt_failed"


def test_E_R09_master_key_must_be_external_and_valid() -> None:
    with pytest.raises(SecretProviderError) as exc_info:
        LocalEncryptedSecretStore(master_key=None)

    assert exc_info.value.code == "secret_master_key_missing"
