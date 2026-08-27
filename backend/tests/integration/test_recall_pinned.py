"""ADR-SNAPSHOT-001 TASK-002：recall_pinned pinned 版本恢复（E-01）。

真实边界（契约声明）：真实 store（sqlite+aiosqlite，`sqlite_store` fixture）+
真实治理路径（commit_publication TOMBSTONE：audit + publish_record + outbox）。

RED 约定（cf-task:start #7）：`recall_pinned` 与 `PublicationOperation.TOMBSTONE`
未实现 → ImportError，即真实 RED。
"""

from __future__ import annotations

import pytest

from fluxion.registry.store import (
    NotFoundError,
    PublicationCommand,
    PublicationOperation,
    RegistryStore,
    RegistryStoreError,
)
from fluxion.resources import ResourceKind, ResourceStatus
from tests.runtime_helpers import publish_resource, sqlite_store

_TENANT = "tenant-a"
_KIND = ResourceKind.WORKFLOW
_RESOURCE = "wf-checkout"


async def _tombstone(
    store: RegistryStore,
    version: str,
    *,
    approval_id: str = "approval-tomb-1",
) -> None:
    await store.commit_publication(
        PublicationCommand(
            publish_id=f"pub-tomb-{version}",
            event_id=f"evt-tomb-{version}",
            tenant_id=_TENANT,
            kind=_KIND,
            resource_id=_RESOURCE,
            version=version,
            operation=PublicationOperation.TOMBSTONE,
            actor_id="e01-tester",
            request_id="req-e01",
            trace_id="trace-e01",
            approval_id=approval_id,
        )
    )


async def test_e01_recall_pinned_rejects_latest_selector(sqlite_store: RegistryStore) -> None:
    await publish_resource(
        sqlite_store,
        tenant_id=_TENANT,
        kind=_KIND,
        resource_id=_RESOURCE,
        version="v1",
        spec={"name": "checkout"},
    )
    # rule 6：resume 永不 resolve latest——recall_pinned 拒绝一切 LATEST 回退形态
    for selector in ("latest", "LATEST", " latest-published "):
        with pytest.raises(RegistryStoreError, match="latest"):
            await sqlite_store.recall_pinned(
                _KIND,
                _RESOURCE,
                tenant_id=_TENANT,
                version=selector,
            )


async def test_e01_tombstoned_pinned_version_still_recallable(
    sqlite_store: RegistryStore,
) -> None:
    published = await publish_resource(
        sqlite_store,
        tenant_id=_TENANT,
        kind=_KIND,
        resource_id=_RESOURCE,
        version="v1",
        spec={"name": "checkout", "steps": 3},
    )
    await _tombstone(sqlite_store, "v1")

    recalled = await sqlite_store.recall_pinned(
        _KIND,
        _RESOURCE,
        tenant_id=_TENANT,
        version="v1",
    )
    # 恢复语义：TOMBSTONE 版本仍可 recall，spec_json 原样保留（immutable payload）
    assert recalled.version == "v1"
    assert recalled.status is ResourceStatus.TOMBSTONE
    assert recalled.spec_json == published.spec_json

    # 治理落账：tombstone 操作进 audit（action == "tombstone"）
    audits, _ = await sqlite_store.list_audit(tenant_id=_TENANT, offset=0, limit=10)
    assert any(audit.action == "tombstone" for audit in audits)


async def test_e01_missing_version_not_found(sqlite_store: RegistryStore) -> None:
    await publish_resource(
        sqlite_store,
        tenant_id=_TENANT,
        kind=_KIND,
        resource_id=_RESOURCE,
        version="v1",
        spec={"name": "checkout"},
    )
    with pytest.raises(NotFoundError):
        await sqlite_store.recall_pinned(
            _KIND,
            _RESOURCE,
            tenant_id=_TENANT,
            version="v9",
        )
    # 跨租户同样 NotFound（rule 16 tenant scope）
    with pytest.raises(NotFoundError):
        await sqlite_store.recall_pinned(
            _KIND,
            _RESOURCE,
            tenant_id="tenant-b",
            version="v1",
        )
