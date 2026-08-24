from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest

from fluxion.registry import RegistryStore, RegistryStoreError, SQLiteRegistryStore
from fluxion.resources import (
    ResourceBinding,
    ResourceDefinition,
    ResourceKind,
    ResourceStatus,
    SubjectType,
    TenantResourceCache,
)


@pytest.fixture
async def store() -> AsyncGenerator[RegistryStore, None]:
    registry = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    await registry.initialize()
    try:
        yield registry
    finally:
        await registry.close()


def _definition(tenant_id: str) -> ResourceDefinition:
    return ResourceDefinition(
        kind=ResourceKind.SKILL,
        id="shared-skill",
        tenant_id=tenant_id,
        version="1",
        status=ResourceStatus.DRAFT,
        spec_json={"name": "shared-skill", "capability": "review"},
    )


@pytest.mark.asyncio
async def test_E_R07_cross_tenant_private_resource_not_read_from_store_or_cache(
    store: RegistryStore,
) -> None:
    cache = TenantResourceCache(ttl_seconds=30)
    tenant_b_resource = await store.put(_definition("tenant-b"))
    tenant_b_resource = await store.publish(
        ResourceKind.SKILL,
        "shared-skill",
        tenant_id="tenant-b",
        version="1",
    )
    cache.set(tenant_b_resource)

    from_store = await store.get(ResourceKind.SKILL, "shared-skill", tenant_id="tenant-a")
    from_cache = cache.get("tenant-a", ResourceKind.SKILL, "shared-skill", "1")

    assert tenant_b_resource.tenant_id == "tenant-b"
    assert from_store is None
    assert from_cache is None
    assert cache.get("tenant-b", ResourceKind.SKILL, "shared-skill", "1") is not None


@pytest.mark.asyncio
async def test_S_R07_non_draft_put_requires_published_at(store: RegistryStore) -> None:
    with pytest.raises(RegistryStoreError, match="requires published_at"):
        await store.put(_definition("tenant-a").model_copy(update={"status": ResourceStatus.PUBLISHED}))

    with_timestamp = _definition("tenant-a").model_copy(
        update={"status": ResourceStatus.PUBLISHED, "published_at": datetime.now(UTC)}
    )
    published = await store.put(with_timestamp)
    assert published.status is ResourceStatus.PUBLISHED
    assert published.published_at is not None


@pytest.mark.asyncio
async def test_S_R07_binding_write_bumps_revision(store: RegistryStore) -> None:
    await store.put(_definition("tenant-a"))
    await store.publish(ResourceKind.SKILL, "shared-skill", tenant_id="tenant-a", version="1")
    revision_before = await store.read_revision(tenant_id="tenant-a")

    binding = ResourceBinding(
        binding_id="binding-a",
        tenant_id="tenant-a",
        subject_type=SubjectType.USER,
        subject_id="user-a",
        resource_type=ResourceKind.SKILL,
        resource_id="shared-skill",
    )
    await store.put_binding(binding)
    assert await store.read_revision(tenant_id="tenant-a") == revision_before + 1

    await store.disable_binding("binding-a", tenant_id="tenant-a")
    assert await store.read_revision(tenant_id="tenant-a") == revision_before + 2
