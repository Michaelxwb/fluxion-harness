from __future__ import annotations

import pytest

from fluxion.runtime.mcp import MCPClientPool
from fluxion.runtime.secrets import (
    CredentialResolver,
    LocalEncryptedSecretStore,
    SecretProviderError,
)


@pytest.mark.asyncio
async def test_E_R08_mcp_client_pool_key_includes_credential_version_and_revoke_blocks_reuse() -> None:
    secret_store = LocalEncryptedSecretStore(master_key=b"c" * 32)
    ref_v1 = await secret_store.put("tenant-a", "weather", "token-v1")
    resolver = CredentialResolver(secret_store)
    pool = MCPClientPool(resolver)

    client_v1 = await pool.get_client(
        tenant_id="tenant-a",
        user_id="user-a",
        server_uri="stdio://weather",
        credential_ref=ref_v1,
    )
    assert client_v1.credential_version == "1"

    ref_v2 = await secret_store.rotate(ref_v1, "token-v2")
    client_v2 = await pool.get_client(
        tenant_id="tenant-a",
        user_id="user-a",
        server_uri="stdio://weather",
        credential_ref=ref_v2,
    )
    assert client_v2 is not client_v1
    assert client_v2.credential_version == "2"

    await secret_store.revoke(ref_v2)
    with pytest.raises(SecretProviderError) as exc_info:
        await pool.get_client(
            tenant_id="tenant-a",
            user_id="user-a",
            server_uri="stdio://weather",
            credential_ref=ref_v2,
        )

    assert exc_info.value.code == "secret_revoked"
    assert pool.client_count == 1
