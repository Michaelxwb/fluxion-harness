"""ADR-SNAPSHOT-001 TASK-004：deprecated 语义形式化 + pinned recall 不受 deprecated 影响（S-01）。

真实边界（契约声明）：真实 store（sqlite+aiosqlite）+ 真实 ResourceResolver
（`resolver.py:144` PUBLISHED-only check）+ 真实 recall_pinned（TASK-002 落地，
仅拒 DRAFT/missing/LATEST，DEPRECATED/TOMBSTONE 可 recall）。

RED 口径声明（cf-task:start #7）：本任务为行为补测——resolver 只 resolve PUBLISHED
与 recall_pinned 不限状态分别已由既有实现（`resolver.py:144`）与 TASK-002 满足，
无真实 RED；按 green-before 记录原因，不伪造失败。
"""

from __future__ import annotations

import pytest

from fluxion.registry import RegistryStore
from fluxion.registry.store import (
    PublicationCommand,
    PublicationOperation,
)
from fluxion.resources import ResourceDefinition, ResourceKind, ResourceStatus
from fluxion.runtime.resolver import ResourceResolver, ResourceVersionNotFoundError
from tests.runtime_helpers import publish_resource, sqlite_store

_TENANT = "tenant-a"
_KIND = ResourceKind.WORKFLOW
_RESOURCE = "wf-checkout"


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
            actor_id="dep-tester",
            request_id=f"req-dep-{version}",
            trace_id=f"trace-dep-{version}",
        )
    )


async def _tombstone(store: RegistryStore, version: str) -> None:
    await store.commit_publication(
        PublicationCommand(
            publish_id=f"pub-tomb-{version}",
            event_id=f"evt-tomb-{version}",
            tenant_id=_TENANT,
            kind=_KIND,
            resource_id=_RESOURCE,
            version=version,
            operation=PublicationOperation.TOMBSTONE,
            actor_id="dep-tester",
            request_id=f"req-tomb-{version}",
            trace_id=f"trace-tomb-{version}",
            approval_id="ap-tomb",
        )
    )


# --- S-01：deprecated 不影响在飞 Execution 的 pinned recall；新解析只返回 v2 ---


async def test_s01_deprecated_excluded_from_latest_but_pinned_recall_unaffected(
    sqlite_store: RegistryStore,
) -> None:
    v1 = await publish_resource(
        sqlite_store, tenant_id=_TENANT, kind=_KIND, resource_id=_RESOURCE, version="v1",
        spec={"name": "checkout", "steps": 2},
    )
    await _deprecate(sqlite_store, "v1")
    await publish_resource(
        sqlite_store, tenant_id=_TENANT, kind=_KIND, resource_id=_RESOURCE, version="v2",
        spec={"name": "checkout", "steps": 3},
    )

    resolver = ResourceResolver(sqlite_store)

    # 新解析只返回 v2（唯一 PUBLISHED；rule 2：deprecated 阻止新解析）
    resolved = await resolver.resolve_resource(_TENANT, _KIND, _RESOURCE)
    assert resolved.version == "v2"
    assert resolved.status is ResourceStatus.PUBLISHED

    # 在飞 Execution 按 snapshot pinned v1 recall 成功，不受 deprecated 影响
    pinned_v1: ResourceDefinition = await sqlite_store.recall_pinned(
        _KIND, _RESOURCE, tenant_id=_TENANT, version="v1"
    )
    assert pinned_v1.version == "v1"
    assert pinned_v1.status is ResourceStatus.DEPRECATED
    assert pinned_v1.spec_json == v1.spec_json

    # 显式按 v1 解析被拒（resolver 不解析 DEPRECATED）
    with pytest.raises(ResourceVersionNotFoundError):
        await resolver.resolve_resource(_TENANT, _KIND, _RESOURCE, selector="v1")


# --- S-01 补测：resolver 显式不解析 DEPRECATED / TOMBSTONE ---


async def test_s01_resolver_rejects_deprecated_and_tombstone_explicit(
    sqlite_store: RegistryStore,
) -> None:
    await publish_resource(
        sqlite_store, tenant_id=_TENANT, kind=_KIND, resource_id=_RESOURCE, version="v3",
        spec={"name": "checkout"},
    )
    await _deprecate(sqlite_store, "v3")
    await publish_resource(
        sqlite_store, tenant_id=_TENANT, kind=_KIND, resource_id=_RESOURCE, version="v4",
        spec={"name": "checkout"},
    )
    await _deprecate(sqlite_store, "v4")
    await _tombstone(sqlite_store, "v4")

    resolver = ResourceResolver(sqlite_store)

    # DEPRECATED 显式版本不被 resolver 解析
    with pytest.raises(ResourceVersionNotFoundError):
        await resolver.resolve_resource(_TENANT, _KIND, _RESOURCE, selector="v3")

    # TOMBSTONE 显式版本不被 resolver 解析（补测新状态）
    with pytest.raises(ResourceVersionNotFoundError):
        await resolver.resolve_resource(_TENANT, _KIND, _RESOURCE, selector="v4")

    # 无 PUBLISHED 版本时 latest-published 解析为 NotFound
    with pytest.raises(ResourceVersionNotFoundError):
        await resolver.resolve_resource(_TENANT, _KIND, _RESOURCE)
