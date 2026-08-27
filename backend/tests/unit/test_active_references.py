"""ADR-SNAPSHOT-001 TASK-001：active_references 表 + add/release/check API（B-01）。

真实边界（契约声明）：真实 `active_references` 表（sqlite+aiosqlite +
metadata.create_all，非 mock）+ 真实 Registry SQL 路径。

RED 约定（cf-task:start #7）：`add_active_reference` 等模块函数未实现 →
collection ImportError，即真实 RED。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from fluxion.registry.resource_sqlalchemy import (
    add_active_reference,
    check_active_references,
    publish,
    put,
    release_active_reference,
)
from fluxion.registry.schema import metadata
from fluxion.resources import ResourceDefinition, ResourceKind, ResourceStatus

_TENANT = "tenant-a"
_KIND = ResourceKind.WORKFLOW
_RESOURCE = "wf-checkout"
_VERSION = "v3"


async def _references_engine() -> AsyncGenerator[AsyncEngine, None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
    # REVIEW-A：add_active_reference 现在校验父版本存在（防悬空引用），先经真实
    # put/publish 路径 seed 一条 PUBLISHED 父行（DRAFT 不可 publish 到引用坐标）。
    await put(
        engine,
        ResourceDefinition(
            tenant_id=_TENANT,
            kind=_KIND,
            id=_RESOURCE,
            version=_VERSION,
            status=ResourceStatus.DRAFT,
            spec_json={},
        ),
    )
    await publish(engine, _KIND, _RESOURCE, tenant_id=_TENANT, version=_VERSION)
    try:
        yield engine
    finally:
        await engine.dispose()


async def _add(
    engine: AsyncEngine,
    ref_id: str,
    *,
    ref_type: str = "execution",
    tenant_id: str = _TENANT,
) -> None:
    await add_active_reference(
        engine,
        tenant_id=tenant_id,
        kind=_KIND,
        resource_id=_RESOURCE,
        version=_VERSION,
        ref_type=ref_type,
        ref_id=ref_id,
    )


async def test_b01_add_then_check_returns_reference() -> None:
    async for engine in _references_engine():
        await _add(engine, "exec-001", ref_type="execution")

        refs = await check_active_references(
            engine,
            tenant_id=_TENANT,
            kind=_KIND,
            resource_id=_RESOURCE,
            version=_VERSION,
        )

        # ref_count > 0 → 卸载/hard-delete 语义上拒绝 active_reference_blocked 的数据基础
        assert len(refs) == 1
        ref = refs[0]
        assert ref.ref_type == "execution"
        assert ref.ref_id == "exec-001"
        assert ref.created_at is not None

        # ref_type 过滤路径（idx_active_reference_scope 服务）
        assert (
            await check_active_references(
                engine,
                tenant_id=_TENANT,
                kind=_KIND,
                resource_id=_RESOURCE,
                version=_VERSION,
                ref_type="workflow",
            )
            == []
        )

        # tenant scope（rule 16）：跨租户 check 为空
        assert (
            await check_active_references(
                engine,
                tenant_id="tenant-b",
                kind=_KIND,
                resource_id=_RESOURCE,
                version=_VERSION,
            )
            == []
        )


async def test_b01_release_then_check_empty() -> None:
    async for engine in _references_engine():
        await _add(engine, "exec-001")
        await release_active_reference(
            engine,
            tenant_id=_TENANT,
            kind=_KIND,
            resource_id=_RESOURCE,
            version=_VERSION,
            ref_type="execution",
            ref_id="exec-001",
        )

        # ref_count=0 → hard-delete 放行的数据基础
        assert (
            await check_active_references(
                engine,
                tenant_id=_TENANT,
                kind=_KIND,
                resource_id=_RESOURCE,
                version=_VERSION,
            )
            == []
        )


async def test_b01_duplicate_add_is_idempotent_single_row() -> None:
    async for engine in _references_engine():
        await _add(engine, "exec-001")
        # 重复引用（同 PK）幂等：不抛 IntegrityError
        await _add(engine, "exec-001")

        refs = await check_active_references(
            engine,
            tenant_id=_TENANT,
            kind=_KIND,
            resource_id=_RESOURCE,
            version=_VERSION,
        )
        assert len(refs) == 1
        assert refs[0].ref_id == "exec-001"


async def test_b01_release_missing_is_noop() -> None:
    async for engine in _references_engine():
        # 不存在的引用 release：no-op，不抛错
        await release_active_reference(
            engine,
            tenant_id=_TENANT,
            kind=_KIND,
            resource_id=_RESOURCE,
            version=_VERSION,
            ref_type="execution",
            ref_id="never-added",
        )
        assert (
            await check_active_references(
                engine,
                tenant_id=_TENANT,
                kind=_KIND,
                resource_id=_RESOURCE,
                version=_VERSION,
            )
            == []
        )
