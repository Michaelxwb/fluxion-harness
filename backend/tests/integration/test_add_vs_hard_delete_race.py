"""ADR-SNAPSHOT-001 REVIEW-A：add_active_reference 与 hard_delete 并发竞态。

真实边界（契约声明）：真实 store（sqlite+aiosqlite）；文件级 SQLite + WAL +
busy_timeout（F5）双 store 真实写竞争，非 mock。

不变量：add_active_reference 与 hard_delete 并发结束后，`active_references` 不得
存在指向已 hard_delete 版本的孤儿引用。修复后两种合法结局：
- add 先完成并插入引用 → hard_delete 被 active_reference_blocked / GC re-check
  拦下（版本保留，引用指向仍存在的版本）；
- hard_delete 先完成（物理删除） → add 的父行校验读到父版本缺失而失败
  （NotFoundError），或 SQLite 写快照冲突被锁失败。
最终若版本已物理删除，则 active_references 中不得有指向它的行。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from datetime import timedelta

import pytest
from tests.runtime_helpers import publish_resource

from fluxion.registry import SQLiteRegistryStore
from fluxion.registry.store import (
    DeleteResult,
    NotFoundError,
    PublicationCommand,
    PublicationOperation,
    RegistryStore,
)
from fluxion.resources import ResourceKind

_TENANT = "tenant-a"
_KIND = ResourceKind.WORKFLOW
_RESOURCE = "wf-checkout"


@pytest.fixture
async def file_store_pair(tmp_path) -> AsyncGenerator[tuple[RegistryStore, RegistryStore], None]:
    """文件级 SQLite + WAL + busy_timeout（F5）：双 store 真实写竞争。"""
    dsn = f"sqlite+aiosqlite:///{tmp_path / 'add_vs_hd_race.db'}"
    store1 = SQLiteRegistryStore(dsn)
    store2 = SQLiteRegistryStore(dsn)
    await store1.initialize()
    await store2.initialize()
    try:
        yield store1, store2
    finally:
        await store1.close()
        await store2.close()


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
            actor_id="race-tester",
            request_id=f"req-{version}",
            trace_id=f"trace-{version}",
            approval_id=approval_id,
        )
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


async def _version_exists(store: RegistryStore, version: str) -> bool:
    try:
        await store.recall_pinned(_KIND, _RESOURCE, tenant_id=_TENANT, version=version)
        return True
    except NotFoundError:
        return False


async def test_add_active_reference_concurrent_with_hard_delete_no_orphan(
    file_store_pair: tuple[RegistryStore, RegistryStore],
) -> None:
    store1, store2 = file_store_pair
    version = "v8"
    await publish_resource(
        store1, tenant_id=_TENANT, kind=_KIND, resource_id=_RESOURCE, version=version,
        spec={"name": "checkout"},
    )
    await _tombstone(store1, version)

    async def _add() -> str:
        try:
            await store2.add_active_reference(
                tenant_id=_TENANT,
                kind=_KIND,
                resource_id=_RESOURCE,
                version=version,
                ref_type="execution",
                ref_id="exec-race",
            )
            return "added"
        except BaseException as exc:  # noqa: BLE001 并发竞态中 add 失败是可接受结局
            return f"add-failed:{type(exc).__name__}:{exc}"

    async def _delete() -> str:
        try:
            await _hard_delete(store1, version, retention_period=timedelta(0))
            return "deleted"
        except BaseException as exc:  # noqa: BLE001 被 guard 拦下同样是合法结局
            return f"delete-failed:{type(exc).__name__}:{exc}"

    add_outcome, delete_outcome = await asyncio.gather(_add(), _delete())

    # 不变量：版本已物理删除 → active_references 不得残留指向它的孤儿引用。
    if not await _version_exists(store1, version):
        refs = await store1.check_active_references(
            tenant_id=_TENANT, kind=_KIND, resource_id=_RESOURCE, version=version
        )
        assert refs == [], (
            f"orphan active_references point to hard-deleted version {version}: {refs}"
        )
        # 版本已删除，add 与 delete 不可能都成功（否则上述 refs 非空即孤儿）。
        assert add_outcome != "added", (
            f"add_active_reference succeeded for hard-deleted version: {add_outcome=} {delete_outcome=}"
        )
    else:
        # 版本保留：add 成功且引用指向仍存在的版本是合法结局；delete 必须被 guard 拦下。
        assert (
            "active_reference_blocked" in delete_outcome
            or "gc_safety_check_failed" in delete_outcome
        ), f"unexpected delete outcome while version retained: {delete_outcome=}"

    # 双向自检：若 delete 成功则 add 必失败（父行已删 → NotFoundError 或写锁冲突）。
    if delete_outcome == "deleted":
        assert add_outcome != "added"
    # 若 add 成功则 delete 必被 guard 拦下（版本保留）。
    if add_outcome == "added":
        assert "active_reference_blocked" in delete_outcome or "gc_safety_check_failed" in delete_outcome
        assert await _version_exists(store1, version)
