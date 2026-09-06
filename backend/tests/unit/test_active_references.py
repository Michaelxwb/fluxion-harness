"""ADR-SNAPSHOT-001 TASK-001：active_references 表 + add/release/check API（B-01）。

真实边界（契约声明）：真实 `active_references` 表（PostgreSQL +
metadata.create_all，非 mock）+ 真实 Registry SQL 路径。

RED 约定（cf-task:start #7）：`add_active_reference` 等模块函数未实现 →
collection ImportError，即真实 RED。
"""

from __future__ import annotations

from tests.runtime_helpers import TEST_POSTGRES_DSN

from collections.abc import AsyncGenerator

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from fluxion.registry.resource_sqlalchemy import (
    add_active_reference,
    check_active_references,
    get,
    publish,
    put,
    release_active_reference,
    release_active_references_for_ref,
)
from fluxion.registry.schema import metadata
from fluxion.registry.store import VersionConflictError
from fluxion.resources import ResourceDefinition, ResourceKind, ResourceStatus

_TENANT = "tenant-a"
_KIND = ResourceKind.WORKFLOW
_RESOURCE = "wf-checkout"
_VERSION = "v3"


async def _references_engine() -> AsyncGenerator[AsyncEngine, None]:
    engine = create_async_engine(TEST_POSTGRES_DSN)
    async with engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
    # 隔离：清掉本文件固定 scope 的引用行（共享 PG 跨 run 残留；精确到
    # tenant/resource，不影响其他测试的引用行）。
    from fluxion.registry.schema import active_references

    async with engine.begin() as connection:
        await connection.execute(
            active_references.delete()
            .where(active_references.c.tenant_id == _TENANT)
            .where(active_references.c.resource_id == _RESOURCE)
        )
    # REVIEW-A：add_active_reference 现在校验父版本存在（防悬空引用），先经真实
    # put/publish 路径 seed 一条 PUBLISHED 父行（DRAFT 不可 publish 到引用坐标）。
    # 幂等：共享 PG 跨 run 复用已存在的父行（put 转译 IntegrityError 为
    # VersionConflictError，即已存在，跳过）。
    try:
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
    except (IntegrityError, VersionConflictError):
        # 跨 run 复用：已 PUBLISHED 直接跳过；残留 DRAFT（上次崩溃窗口）则补 publish。
        existing = await get(engine, _KIND, _RESOURCE, tenant_id=_TENANT, version=_VERSION)
        if existing is not None and existing.status is ResourceStatus.DRAFT:
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


async def test_b01_release_by_ref_clears_only_that_ref() -> None:
    """TASK-007 terminal GC：按 ref_id 释放该 run 的全部引用（同 ref_type），不影响其它 ref。"""
    async for engine in _references_engine():
        await add_active_reference(
            engine,
            tenant_id=_TENANT,
            kind=_KIND,
            resource_id=_RESOURCE,
            version=_VERSION,
            ref_type="execution",
            ref_id="run-1",
        )
        await add_active_reference(
            engine,
            tenant_id=_TENANT,
            kind=_KIND,
            resource_id=_RESOURCE,
            version=_VERSION,
            ref_type="workflow",
            ref_id="run-1",
        )
        await add_active_reference(
            engine,
            tenant_id=_TENANT,
            kind=_KIND,
            resource_id=_RESOURCE,
            version=_VERSION,
            ref_type="execution",
            ref_id="run-2",
        )
        await release_active_references_for_ref(
            engine,
            tenant_id=_TENANT,
            ref_type="execution",
            ref_id="run-1",
        )
        remaining = await check_active_references(
            engine,
            tenant_id=_TENANT,
            kind=_KIND,
            resource_id=_RESOURCE,
            version=_VERSION,
        )
        # run-1 的 workflow 引用不受 ref_type=execution 释放影响；run-2 完全不动
        assert {(r.ref_type, r.ref_id) for r in remaining} == {
            ("execution", "run-2"),
            ("workflow", "run-1"),
        }

        # 幂等：再次按已无行的坐标释放是 no-op
        await release_active_references_for_ref(
            engine,
            tenant_id=_TENANT,
            ref_type="execution",
            ref_id="run-1",
        )
        assert len(
            await check_active_references(
                engine,
                tenant_id=_TENANT,
                kind=_KIND,
                resource_id=_RESOURCE,
                version=_VERSION,
            )
        ) == 2
