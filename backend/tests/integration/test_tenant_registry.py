from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from fluxion.registry import (
    PostgreSQLRegistryStore,
    RegistryStore,
    RegistryStoreError,
    SQLiteRegistryStore,
)
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
async def test_S_F05_sqlite_wal_and_busy_timeout_enabled(tmp_path) -> None:
    """F5：SQLite 文件库启用 WAL + 5s busy_timeout，缓解 dev 并发写 "database is locked"。"""
    dsn = f"sqlite+aiosqlite:///{tmp_path / 'fluxion-f5.db'}"
    store = SQLiteRegistryStore(dsn)
    await store.initialize()
    try:
        async with store.engine.connect() as connection:
            journal = (await connection.execute(text("PRAGMA journal_mode"))).scalar()
            busy = (await connection.execute(text("PRAGMA busy_timeout"))).scalar()
    finally:
        await store.close()
    assert journal == "wal"
    assert busy == 5000


@pytest.mark.asyncio
async def test_S_A13_postgres_serving_initialize_is_noop() -> None:
    """A13/ADR-004：PG serving 路径（reset=False）initialize() 为 no-op——不在运行
    路径跑 create_all（否则与 scripts/init_db.py 形成双事实源）。schema 由
    scripts/init_db.py 建。用不可达 DSN 证明：gate 生效时不触达
    DB；若 gate 缺失，create_all 会尝试连接不可达 PG 而失败，本断言即破。"""
    unreachable = "postgresql+asyncpg://nobody:nobody@127.0.0.1:1/fluxion_test"
    serving = PostgreSQLRegistryStore(unreachable, reset_on_initialize=False)
    await serving.initialize()  # no-op：早返回，不触达 DB
    await serving.close()


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
async def test_S_R07_put_rejects_non_draft_status(store: RegistryStore) -> None:
    # F6：put() 只接受 DRAFT；PUBLISHED 必须经 publish()/commit_publication() 路径
    # 过渡（CAS + 审计 + outbox），直插 PUBLISHED 行绕过 publish 治理。此前该路径
    # 仅要求 published_at 非空即可直插——现在即便带 published_at 也拒绝。
    with pytest.raises(RegistryStoreError, match="only accepts DRAFT"):
        await store.put(_definition("tenant-a").model_copy(update={"status": ResourceStatus.PUBLISHED}))

    with_timestamp = _definition("tenant-a").model_copy(
        update={"status": ResourceStatus.PUBLISHED, "published_at": datetime.now(UTC)}
    )
    with pytest.raises(RegistryStoreError, match="only accepts DRAFT"):
        await store.put(with_timestamp)


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
