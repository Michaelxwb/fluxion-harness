from __future__ import annotations

import pytest
from tests.runtime_helpers import seed_runtime_profile

from fluxion.registry import RegistryReadStore, RegistryStore, RegistryStoreError
from fluxion.registry.store import AuditRecord
from fluxion.resources import ResourceBinding, ResourceDefinition, ResourceKind, TenantResourceCache
from fluxion.runtime.resolver import (
    RegistryUnavailableError,
    ResolverPolicy,
    ResourceResolver,
)


class FailingReadStore:
    def __init__(self, delegate: RegistryStore) -> None:
        self._delegate = delegate
        self.fail_reads = False

    async def initialize(self) -> None:
        await self._delegate.initialize()

    async def close(self) -> None:
        await self._delegate.close()

    async def put(self, definition: ResourceDefinition) -> ResourceDefinition:
        return await self._delegate.put(definition)

    async def get(
        self,
        kind: ResourceKind,
        resource_id: str,
        *,
        tenant_id: str,
        version: str | None = None,
    ) -> ResourceDefinition | None:
        if self.fail_reads:
            raise RegistryStoreError("registry unavailable")
        return await self._delegate.get(kind, resource_id, tenant_id=tenant_id, version=version)

    async def publish(
        self,
        kind: ResourceKind,
        resource_id: str,
        *,
        tenant_id: str,
        version: str,
    ) -> ResourceDefinition:
        return await self._delegate.publish(kind, resource_id, tenant_id=tenant_id, version=version)

    async def put_binding(self, binding: ResourceBinding) -> ResourceBinding:
        return await self._delegate.put_binding(binding)

    async def list_bindings(
        self,
        *,
        subject_type: str,
        subject_id: str,
        tenant_id: str,
        resource_type: ResourceKind | None = None,
    ) -> list[ResourceBinding]:
        if self.fail_reads:
            raise RegistryStoreError("registry unavailable")
        return await self._delegate.list_bindings(
            subject_type=subject_type,
            subject_id=subject_id,
            tenant_id=tenant_id,
            resource_type=resource_type,
        )

    async def update_draft(self, definition: ResourceDefinition) -> ResourceDefinition:
        return await self._delegate.update_draft(definition)

    async def append_audit(self, record: AuditRecord) -> None:
        await self._delegate.append_audit(record)

    async def read_revision(self, *, tenant_id: str) -> int:
        if self.fail_reads:
            raise RegistryStoreError("registry unavailable")
        return await self._delegate.read_revision(tenant_id=tenant_id)

    async def bump_revision(self, *, tenant_id: str) -> int:
        return await self._delegate.bump_revision(tenant_id=tenant_id)

    async def disable_binding(self, binding_id: str, *, tenant_id: str) -> None:
        await self._delegate.disable_binding(binding_id, tenant_id=tenant_id)


@pytest.mark.asyncio
async def test_E_R01_registry_unavailable_degrades_only_with_safe_stale_cache(
    sqlite_store: RegistryStore,
) -> None:
    await seed_runtime_profile(sqlite_store)
    failing_store = FailingReadStore(sqlite_store)
    cache = TenantResourceCache(ttl_seconds=60)
    resolver = ResourceResolver(
        failing_store,
        cache=cache,
        policy=ResolverPolicy(allow_stale_non_sensitive=True),
    )
    strict_resolver = ResourceResolver(
        failing_store,
        cache=TenantResourceCache(ttl_seconds=60),
        policy=ResolverPolicy(allow_stale_non_sensitive=False),
    )

    warmed = await resolver.resolve_resource(
        "tenant-a",
        ResourceKind.RUNTIME_PROFILE,
        "assistant",
        selector="latest-published",
    )
    failing_store.fail_reads = True
    stale = await resolver.resolve_resource(
        "tenant-a",
        ResourceKind.RUNTIME_PROFILE,
        "assistant",
        selector="latest-published",
    )

    assert stale.version == warmed.version == "1"
    assert resolver.degraded_read_count == 1
    with pytest.raises(RegistryUnavailableError):
        await strict_resolver.resolve_resource(
            "tenant-a",
            ResourceKind.RUNTIME_PROFILE,
            "assistant",
            selector="latest-published",
        )


def test_E_R01_failing_store_matches_read_protocol(sqlite_store: RegistryStore) -> None:
    wrapper = FailingReadStore(sqlite_store)
    assert isinstance(wrapper, RegistryReadStore)
    assert not isinstance(wrapper, RegistryStore)
