"""ADR-SNAPSHOT-001 TASK-003：hard_delete 三重 guard + GC safety（S-02/S-03/S-04/E-02）。

真实边界（契约声明）：真实 store（sqlite+aiosqlite）；E-02 并发用文件级 SQLite +
WAL + busy_timeout（F5）双 store 真实写竞争，非 mock。

RED 约定（cf-task:start #7）：`hard_delete` 未实现 → AttributeError，即真实 RED。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from datetime import timedelta

import pytest

from fluxion.registry import SQLiteRegistryStore
from fluxion.registry.store import (
    DeleteResult,
    NotFoundError,
    PublicationCommand,
    PublicationOperation,
    RegistryStore,
    RegistryStoreError,
)
from fluxion.runtime.resolver import ResourceResolver, ResourceVersionNotFoundError
from fluxion.resources import ResourceKind, ResourceStatus
from tests.runtime_helpers import publish_resource, sqlite_store

_TENANT = "tenant-a"
_KIND = ResourceKind.WORKFLOW
_RESOURCE = "wf-checkout"


async def _tombstone(store: RegistryStore, version: str, *, approval_id: str = "ap-tomb") -> None:
    await store.commit_publication(
        PublicationCommand(
            publish_id=f"pub-tomb-{version}",
            event_id=f"evt-tomb-{version}",
            tenant_id=_TENANT,
            kind=_KIND,
            resource_id=_RESOURCE,
            version=version,
            operation=PublicationOperation.TOMBSTONE,
            actor_id="guard-tester",
            request_id=f"req-{version}",
            trace_id=f"trace-{version}",
            approval_id=approval_id,
        )
    )


async def _deprecate(store: RegistryStore, version: str) -> None:
    await store.commit_publication(
        PublicationCommand(
            publish_id=f"pub-dep-{version}",
            event_id=f"evt-dep-{version}",
            tenant_id=_TENANT,
            kind=_KIND,
            resource_id=_RESOURCE,
            version=version,
            operation=PublicationOperation.DEPRECATE,
            actor_id="guard-tester",
            request_id=f"req-dep-{version}",
            trace_id=f"trace-dep-{version}",
        )
    )


async def _add_ref(store: RegistryStore, version: str, ref_id: str) -> None:
    await store.add_active_reference(
        tenant_id=_TENANT,
        kind=_KIND,
        resource_id=_RESOURCE,
        version=version,
        ref_type="execution",
        ref_id=ref_id,
    )


async def _hard_delete(
    store: RegistryStore,
    version: str,
    *,
    approval_id: str = "ap-hd",
    retention_period: timedelta = timedelta(days=30),
) -> DeleteResult:
    return await store.hard_delete(
        _KIND,
        _RESOURCE,
        tenant_id=_TENANT,
        version=version,
        approval_id=approval_id,
        retention_period=retention_period,
    )


# --- S-02：active 引用时 hard-delete 拒绝，行保留 ---


async def test_s02_active_reference_blocks_hard_delete(sqlite_store: RegistryStore) -> None:
    await publish_resource(
        sqlite_store, tenant_id=_TENANT, kind=_KIND, resource_id=_RESOURCE, version="v3",
        spec={"name": "checkout"},
    )
    await _tombstone(sqlite_store, "v3")
    await _add_ref(sqlite_store, "v3", "exec-001")

    with pytest.raises(RegistryStoreError, match="active_reference_blocked"):
        await _hard_delete(sqlite_store, "v3", retention_period=timedelta(0))

    # 行保留：仍 TOMBSTONE、spec_json 不动
    retained = await sqlite_store.recall_pinned(
        _KIND, _RESOURCE, tenant_id=_TENANT, version="v3"
    )
    assert retained.status is ResourceStatus.TOMBSTONE


# --- S-03：guard 顺序 + 全过物理删除 ---


async def test_s03_active_ref_guard_precedes_retention(sqlite_store: RegistryStore) -> None:
    # active_ref>0 且 retention 已过 → 仍 active_reference_blocked（证明 active_ref 优先）
    await publish_resource(
        sqlite_store, tenant_id=_TENANT, kind=_KIND, resource_id=_RESOURCE, version="v4",
        spec={"name": "checkout"},
    )
    await _tombstone(sqlite_store, "v4")
    await _add_ref(sqlite_store, "v4", "exec-001")
    with pytest.raises(RegistryStoreError, match="active_reference_blocked"):
        await _hard_delete(sqlite_store, "v4", retention_period=timedelta(0))


async def test_s03_retention_not_elapsed_blocks(sqlite_store: RegistryStore) -> None:
    await publish_resource(
        sqlite_store, tenant_id=_TENANT, kind=_KIND, resource_id=_RESOURCE, version="v4b",
        spec={"name": "checkout"},
    )
    await _tombstone(sqlite_store, "v4b")
    # active_ref=0 但 retention 未过 → retention_period_not_elapsed
    with pytest.raises(RegistryStoreError, match="retention_period_not_elapsed"):
        await _hard_delete(sqlite_store, "v4b", retention_period=timedelta(days=1))


async def test_s03_all_guards_pass_physical_delete(sqlite_store: RegistryStore) -> None:
    await publish_resource(
        sqlite_store, tenant_id=_TENANT, kind=_KIND, resource_id=_RESOURCE, version="v4c",
        spec={"name": "checkout"},
    )
    await _tombstone(sqlite_store, "v4c")
    # active_ref=0、retention=0（已过）、GC 通过 → 物理删除
    result = await _hard_delete(sqlite_store, "v4c", retention_period=timedelta(0))
    assert result.version == "v4c"

    with pytest.raises(NotFoundError):
        await sqlite_store.recall_pinned(_KIND, _RESOURCE, tenant_id=_TENANT, version="v4c")


# --- S-04：TOMBSTONE 保留 spec_json、resolver 不解析、active_ref 阻断 ---


async def test_s04_tombstone_retains_payload_resolver_skips_active_ref_blocks(
    sqlite_store: RegistryStore,
) -> None:
    published = await publish_resource(
        sqlite_store, tenant_id=_TENANT, kind=_KIND, resource_id=_RESOURCE, version="v5",
        spec={"name": "checkout", "steps": 3},
    )
    # PUBLISHED→DEPRECATED→TOMBSTONE 完整链
    await _deprecate(sqlite_store, "v5")
    await _tombstone(sqlite_store, "v5")

    recalled = await sqlite_store.recall_pinned(_KIND, _RESOURCE, tenant_id=_TENANT, version="v5")
    assert recalled.status is ResourceStatus.TOMBSTONE
    assert recalled.spec_json == published.spec_json

    # resolver 不解析 TOMBSTONE（PUBLISHED-only check）
    resolver = ResourceResolver(sqlite_store)
    with pytest.raises(ResourceVersionNotFoundError):
        await resolver.resolve_resource(_TENANT, _KIND, _RESOURCE, selector="v5")

    # active_ref>0 时不可 hard-delete
    await _add_ref(sqlite_store, "v5", "exec-001")
    with pytest.raises(RegistryStoreError, match="active_reference_blocked"):
        await _hard_delete(sqlite_store, "v5", retention_period=timedelta(0))


# --- E-02：重复删除幂等 + 并发 race 失败方 gc_safety_check_failed ---


async def test_e02_repeat_hard_delete_is_idempotent(sqlite_store: RegistryStore) -> None:
    await publish_resource(
        sqlite_store, tenant_id=_TENANT, kind=_KIND, resource_id=_RESOURCE, version="v6",
        spec={"name": "checkout"},
    )
    await _tombstone(sqlite_store, "v6")
    await _hard_delete(sqlite_store, "v6", retention_period=timedelta(0))

    # 第二次重复删除：幂等 NotFound（行已不存在），不产生重复治理
    with pytest.raises(NotFoundError):
        await _hard_delete(sqlite_store, "v6", retention_period=timedelta(0))

    audits, _ = await sqlite_store.list_audit(tenant_id=_TENANT, offset=0, limit=100)
    hard_delete_audits = [a for a in audits if a.action == "hard_delete"]
    assert len(hard_delete_audits) == 1


@pytest.fixture
async def file_store_pair(tmp_path) -> AsyncGenerator[tuple[RegistryStore, RegistryStore], None]:
    """文件级 SQLite + WAL + busy_timeout（F5）：双 store 真实写竞争（E-02）。"""
    dsn = f"sqlite+aiosqlite:///{tmp_path / 'hd_race.db'}"
    store1 = SQLiteRegistryStore(dsn)
    store2 = SQLiteRegistryStore(dsn)
    await store1.initialize()
    await store2.initialize()
    try:
        yield store1, store2
    finally:
        await store1.close()
        await store2.close()


async def test_e02_concurrent_hard_delete_loser_gc_safety_check_failed(
    file_store_pair: tuple[RegistryStore, RegistryStore],
) -> None:
    store1, store2 = file_store_pair
    await publish_resource(
        store1, tenant_id=_TENANT, kind=_KIND, resource_id=_RESOURCE, version="v7",
        spec={"name": "checkout"},
    )
    await _tombstone(store1, "v7")

    results = await asyncio.gather(
        _hard_delete(store1, "v7", approval_id="ap-1", retention_period=timedelta(0)),
        _hard_delete(store2, "v7", approval_id="ap-2", retention_period=timedelta(0)),
        return_exceptions=True,
    )
    successes = [r for r in results if not isinstance(r, BaseException)]
    failures = [r for r in results if isinstance(r, BaseException)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], RegistryStoreError)
    assert "gc_safety_check_failed" in str(failures[0])

    # 无孤儿/重复：行已物理删除、hard_delete 治理恰好 1 条
    with pytest.raises(NotFoundError):
        await store1.recall_pinned(_KIND, _RESOURCE, tenant_id=_TENANT, version="v7")
    audits, _ = await store1.list_audit(tenant_id=_TENANT, offset=0, limit=100)
    assert len([a for a in audits if a.action == "hard_delete"]) == 1
