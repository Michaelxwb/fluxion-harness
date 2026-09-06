"""TASK-027 S-01/S-02：凭据生命周期真实存储回归。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from fluxion.plugins.secret.postgres import PostgresEncryptedSecretStore
from fluxion.registry import SQLiteRegistryStore
from fluxion.runtime.secrets import CredentialResolver, LocalEncryptedSecretStore, SecretProviderError
from fluxion.services.console_app import ConsoleApplicationService
from fluxion.services.console_contracts import ConsoleActor

CredentialStack = tuple[ConsoleApplicationService, CredentialResolver]

ACTOR = ConsoleActor("tenant-review", "admin", "req-review", "trace-review")


@pytest.fixture(params=["local", "durable"])
async def stack(request: pytest.FixtureRequest) -> AsyncIterator[
    tuple[ConsoleApplicationService, CredentialResolver]
]:
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    await store.initialize()
    secrets: LocalEncryptedSecretStore | PostgresEncryptedSecretStore
    if request.param == "local":
        secrets = LocalEncryptedSecretStore(master_key=b"r" * 32)
    else:
        secrets = PostgresEncryptedSecretStore(engine=store.engine, master_key=b"r" * 32)
        await secrets.initialize()
    service = ConsoleApplicationService(store, secret_store=secrets, secret_metadata_store=secrets)
    try:
        yield service, CredentialResolver(secrets)
    finally:
        await store.close()


async def test_S_01_same_name_creation_preserves_existing_secret(stack: CredentialStack) -> None:
    service, resolver = stack
    first = await service.create_credential(ACTOR, name="Provider Key", plaintext="original")
    for name in ("Provider Key", "provider-key"):
        second = await service.create_credential(ACTOR, name=name, plaintext="replacement")
        assert second.id != first.id
        assert await resolver.resolve(second.spec_json["secret_ref"], tenant_id=ACTOR.tenant_id) == "replacement"
        assert await resolver.resolve(first.spec_json["secret_ref"], tenant_id=ACTOR.tenant_id) == "original"


async def test_S_01_concurrent_same_name_creation_is_independent(stack: CredentialStack) -> None:
    service, resolver = stack
    rows = await asyncio.gather(*(
        service.create_credential(ACTOR, name="shared", plaintext=str(index))
        for index in range(3)
    ))
    assert len({row.id for row in rows}) == 3
    for index, row in enumerate(rows):
        assert await resolver.resolve(row.spec_json["secret_ref"], tenant_id=ACTOR.tenant_id) == str(index)


async def test_S_02_disable_revokes_all_rotations_without_other_credentials(stack: CredentialStack) -> None:
    service, resolver = stack
    first = await service.create_credential(ACTOR, name="key", plaintext="old")
    refs = [first.spec_json["secret_ref"]]
    for value in ("second", "third"):
        rotated = await service.rotate_credential(ACTOR, credential_id=first.id, plaintext=value)
        refs.append(rotated.spec_json["secret_ref"])
    other = await service.create_credential(ACTOR, name="key-extra", plaintext="other")
    disabled = await service.disable_credential(ACTOR, credential_id=first.id)
    assert disabled.spec_json["revoked"] is True
    for ref in refs:
        with pytest.raises(SecretProviderError, match="revoked"):
            await resolver.resolve(ref, tenant_id=ACTOR.tenant_id)
    assert await resolver.resolve(other.spec_json["secret_ref"], tenant_id=ACTOR.tenant_id) == "other"
