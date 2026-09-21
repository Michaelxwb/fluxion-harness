"""投递路由归一化与租户/用户隔离（B-105 / RULE-im-001）。

真实边界：真实 PostgreSQL task.delivery_route partial unique。
"""

from __future__ import annotations

import asyncio
import uuid

import sqlalchemy as sa
from conftest import TenantContext
from helpers import sample_route
from muad_agent_worker.application.delivery_routes import upsert_delivery_route
from muad_contracts import DeliveryRouteInput

DELETE_TENANT = sa.text("DELETE FROM task.delivery_route WHERE tenant_id = :tenant_id")
COUNT_TENANT = sa.text("SELECT count(*) FROM task.delivery_route WHERE tenant_id = :tenant_id")


async def _upsert(
    tenant: TenantContext,
    *,
    platform_user_id: uuid.UUID,
    route: DeliveryRouteInput,
    tenant_id: str | None = None,
) -> uuid.UUID:
    async with tenant.session_factory() as session:
        async with session.begin():
            return await upsert_delivery_route(
                session,
                tenant_id=tenant_id or tenant.tenant_id,
                platform_user_id=platform_user_id,
                route=route,
            )


async def _count(tenant: TenantContext, tenant_id: str | None = None) -> int:
    async with tenant.session_factory() as session:
        return (
            await session.execute(COUNT_TENANT, {"tenant_id": tenant_id or tenant.tenant_id})
        ).scalar()


async def _drop(tenant: TenantContext, tenant_id: str) -> None:
    async with tenant.session_factory() as session:
        await session.execute(DELETE_TENANT, {"tenant_id": tenant_id})
        await session.commit()


async def test_same_route_is_reused(tenant: TenantContext) -> None:
    """同租户 + 同平台用户 + 同路由 → 复用同一行。"""
    user = uuid.uuid4()
    first = await _upsert(tenant, platform_user_id=user, route=sample_route())
    second = await _upsert(tenant, platform_user_id=user, route=sample_route())

    assert first == second
    assert await _count(tenant) == 1


async def test_concurrent_same_route_yields_single_row(tenant: TenantContext) -> None:
    """并发提交同一路由只落一行，且都拿到同一个 id。"""
    user = uuid.uuid4()
    ids = await asyncio.gather(
        *(
            _upsert(tenant, platform_user_id=user, route=sample_route())
            for _ in range(4)
        )
    )

    assert len(set(ids)) == 1, f"并发 upsert 返回了多个 id: {ids}"
    assert await _count(tenant) == 1


async def test_different_tenant_does_not_reuse(tenant: TenantContext) -> None:
    """跨租户不复用同一条路由。"""
    user = uuid.uuid4()
    other = f"{tenant.tenant_id}-other"
    try:
        mine = await _upsert(tenant, platform_user_id=user, route=sample_route())
        theirs = await _upsert(
            tenant, platform_user_id=user, route=sample_route(), tenant_id=other
        )
        assert mine != theirs
        assert await _count(tenant) == 1
        assert await _count(tenant, other) == 1
    finally:
        await _drop(tenant, other)


async def test_different_recipient_does_not_reuse(tenant: TenantContext) -> None:
    """接收方不同不复用。"""
    user = uuid.uuid4()
    first = await _upsert(tenant, platform_user_id=user, route=sample_route("wotv-001"))
    second = await _upsert(tenant, platform_user_id=user, route=sample_route("wotv-002"))

    assert first != second
    assert await _count(tenant) == 2


async def test_different_platform_user_does_not_reuse(tenant: TenantContext) -> None:
    """平台用户不同不复用：归一化元组必须包含 platform_user 身份。"""
    first = await _upsert(tenant, platform_user_id=uuid.uuid4(), route=sample_route())
    second = await _upsert(tenant, platform_user_id=uuid.uuid4(), route=sample_route())

    assert first != second, "不同平台用户复用了同一条投递路由"
    assert await _count(tenant) == 2


async def test_soft_deleted_route_can_be_recreated(tenant: TenantContext) -> None:
    """软删除后 partial unique 不再命中，可重建同一路由。"""
    user = uuid.uuid4()
    first = await _upsert(tenant, platform_user_id=user, route=sample_route())

    async with tenant.session_factory() as session:
        await session.execute(
            sa.text("UPDATE task.delivery_route SET is_deleted = true WHERE id = :id"),
            {"id": first},
        )
        await session.commit()

    second = await _upsert(tenant, platform_user_id=user, route=sample_route())
    assert second != first


async def test_route_row_carries_no_secret_or_pod_fields(tenant: TenantContext) -> None:
    """路由行不含 Bot Secret，也不含 Pod/lease 信息。"""
    route_id = await _upsert(tenant, platform_user_id=uuid.uuid4(), route=sample_route())

    async with tenant.session_factory() as session:
        row = (
            await session.execute(
                sa.text("SELECT * FROM task.delivery_route WHERE id = :id"), {"id": route_id}
            )
        ).mappings().one()

    forbidden = {"secret", "bot_secret", "token", "password", "credential", "pod", "lease_owner"}
    assert not (set(row.keys()) & forbidden), f"路由表出现禁止字段: {set(row.keys()) & forbidden}"
    assert "secret" not in str(row["route_json"]).lower()
