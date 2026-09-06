"""TASK-003: 服务端分页与搜索验收（S-03/E-02）。

真实边界：真实 Console HTTP API（ASGITransport）→ 真实
ConsoleApplicationService → 真实 PostgreSQLRegistryStore（ADR-A007 单库）。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient

from fluxion.api.console import create_app
from fluxion.registry import PostgreSQLRegistryStore
from fluxion.resources import ResourceKind
from fluxion.services.console_app import ConsoleApplicationService
from tests.runtime_helpers import publish_resource, resource_definition, TEST_POSTGRES_DSN


async def _make_stack(dsn: str) -> tuple[AsyncClient, object]:
    store: object = PostgreSQLRegistryStore(dsn)
    await store.initialize()  # type: ignore[union-attr]
    service = ConsoleApplicationService(store)  # type: ignore[arg-type]
    await service.initialize()
    client = AsyncClient(
        transport=ASGITransport(app=create_app(service)),
        base_url="http://testserver",
    )
    return client, service


async def _seed_150(store: object, tenant_id: str) -> None:
    """150 个逻辑 TOOL 资源；前 10 个追加 v2 draft（当前版本选择回归）；
    res-130 命名含 needle（第一页外搜索命中）；含中文与特殊字符名。"""
    for index in range(1, 151):
        resource_id = f"res-{index:03d}"
        if index == 130:
            name = "needle-deep-record"
        elif index == 42:
            name = "中文资源-alpha"
        elif index == 77:
            name = "100%_coverage\\test"
        else:
            name = f"普通资源-{index:03d}"
        await publish_resource(
            store,  # type: ignore[arg-type]
            tenant_id=tenant_id,
            kind=ResourceKind.TOOL,
            resource_id=resource_id,
            version="1",
            spec={"name": name},
        )
    for index in range(1, 11):
        await store.put(  # type: ignore[union-attr]
            resource_definition(
                tenant_id=tenant_id,
                kind=ResourceKind.TOOL,
                resource_id=f"res-{index:03d}",
                version="2",
                spec={"name": f"普通资源-{index:03d}-v2草稿"},
            )
        )


def _headers(tenant_id: str) -> dict[str, str]:
    return {
        "X-Tenant-ID": tenant_id,
        "X-Actor-ID": "admin-a",
        "X-Request-ID": f"req-{uuid.uuid4().hex[:8]}",
    }


@pytest.fixture
async def stack() -> AsyncGenerator[tuple[AsyncClient, str]]:
    tenant_id = f"tenant-s03-{uuid.uuid4().hex[:8]}"
    client, service = await _make_stack(TEST_POSTGRES_DSN)
    await _seed_150(service._store, tenant_id)
    try:
        yield client, tenant_id
    finally:
        await client.aclose()
        await service.close()


class TestS03ServerPaginationAndSearch:
    async def test_page_6_holds_items_101_to_120(self, stack: tuple[AsyncClient, str]) -> None:
        """S-03：每页 20 条时第 6 页为第 101~120 条，total=150。"""
        client, tenant_id = stack
        response = await client.get(
            "/api/v1/resources?resource_type=tool&page=6&page_size=20",
            headers=_headers(tenant_id),
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["total"] == 150
        assert [item["resource_id"] for item in data["items"]] == [
            f"res-{index:03d}" for index in range(101, 121)
        ]

    async def test_last_page_holds_final_10(self, stack: tuple[AsyncClient, str]) -> None:
        """S-03：第 8 页为末 10 条。"""
        client, tenant_id = stack
        response = await client.get(
            "/api/v1/resources?resource_type=tool&page=8&page_size=20",
            headers=_headers(tenant_id),
        )
        data = response.json()["data"]
        assert data["total"] == 150
        assert [item["resource_id"] for item in data["items"]] == [
            f"res-{index:03d}" for index in range(141, 151)
        ]

    async def test_keyword_hits_record_beyond_first_page(
        self, stack: tuple[AsyncClient, str]
    ) -> None:
        """S-03：搜索命中原第一页之外的记录（res-130）。"""
        client, tenant_id = stack
        response = await client.get(
            "/api/v1/resources?resource_type=tool&keyword=needle",
            headers=_headers(tenant_id),
        )
        data = response.json()["data"]
        assert data["total"] == 1
        assert data["items"][0]["resource_id"] == "res-130"

    async def test_keyword_case_insensitive(self, stack: tuple[AsyncClient, str]) -> None:
        """S-03：keyword 大小写不敏感。"""
        client, tenant_id = stack
        response = await client.get(
            "/api/v1/resources?resource_type=tool&keyword=NEEDLE",
            headers=_headers(tenant_id),
        )
        assert response.json()["data"]["total"] == 1

    async def test_current_version_selected_before_filter(
        self, stack: tuple[AsyncClient, str]
    ) -> None:
        """S-03：先选当前版本再过滤——v2 草稿名可被搜到，旧 v1 名不再命中。"""
        client, tenant_id = stack
        response = await client.get(
            "/api/v1/resources?resource_type=tool&keyword=v2草稿",
            headers=_headers(tenant_id),
        )
        assert response.json()["data"]["total"] == 10


class TestE02PaginationBoundaries:
    async def test_out_of_range_page_empty_with_correct_total(
        self, stack: tuple[AsyncClient, str]
    ) -> None:
        """E-02：越界页为空但 total 仍正确。"""
        client, tenant_id = stack
        response = await client.get(
            "/api/v1/resources?resource_type=tool&page=99&page_size=20",
            headers=_headers(tenant_id),
        )
        data = response.json()["data"]
        assert data["items"] == []
        assert data["total"] == 150

    async def test_chinese_keyword(self, stack: tuple[AsyncClient, str]) -> None:
        """E-02：中文搜索。"""
        client, tenant_id = stack
        response = await client.get(
            "/api/v1/resources?resource_type=tool&keyword=中文资源",
            headers=_headers(tenant_id),
        )
        data = response.json()["data"]
        assert data["total"] == 1
        assert data["items"][0]["resource_id"] == "res-042"

    async def test_special_chars_literal_match(self, stack: tuple[AsyncClient, str]) -> None:
        """E-02：%, _, \\ 按字面匹配（LIKE 转义），不做通配。"""
        client, tenant_id = stack
        from urllib.parse import quote

        response = await client.get(
            f"/api/v1/resources?resource_type=tool&keyword={quote('100%_coverage\\test')}",
            headers=_headers(tenant_id),
        )
        data = response.json()["data"]
        assert data["total"] == 1
        assert data["items"][0]["resource_id"] == "res-077"

    async def test_cross_tenant_isolation(self, stack: tuple[AsyncClient, str]) -> None:
        """E-02：跨 tenant 不可见（同 resource_id 不同租户）。"""
        client, _tenant_id = stack
        other = f"tenant-s03-{uuid.uuid4().hex[:8]}"
        response = await client.get(
            "/api/v1/resources?resource_type=tool&keyword=needle",
            headers=_headers(other),
        )
        assert response.json()["data"]["total"] == 0

    async def test_empty_store_total_zero(self) -> None:
        """E-02：空数据 total=0 且 items 为空。"""
        client, service = await _make_stack(TEST_POSTGRES_DSN)
        try:
            response = await client.get(
                "/api/v1/resources?resource_type=tool",
                headers=_headers("tenant-empty"),
            )
            data = response.json()["data"]
            assert data["total"] == 0
            assert data["items"] == []
        finally:
            await client.aclose()
            await service.close()


def _trace_record(
    trace_id: str, execution_id: str, tenant_id: str, *, error: str | None
) -> TraceRecord:
    from fluxion.resources import ExecutionSnapshot
    from fluxion.runtime.context import TraceEvent
    from fluxion.runtime.tracing import TraceRecord as Record

    snapshot = ExecutionSnapshot(
        execution_id=execution_id,
        tenant_id=tenant_id,
        user_id="user-a",
        runtime_profile_id="runtime-main",
        runtime_profile_version="1",
        model_resolution={
            "routes": [
                {
                    "provider_ref": {"id": "dev.echo", "version": "1"},
                    "model_ref": {"id": "model.dev.echo", "version": "1"},
                    "model": "echo",
                }
            ]
        },
        trace_id=trace_id,
    )
    return Record(
        trace_id=trace_id,
        execution_id=execution_id,
        tenant_id=tenant_id,
        runtime_profile_id="runtime-main",
        runtime_profile_version="1",
        snapshot=snapshot,
        events=(
            TraceEvent(
                name="model.response",
                tenant_id=tenant_id,
                execution_id=execution_id,
                trace_id=trace_id,
                attributes={},
            ),
        ),
        latency_ms=5.0,
        error=error,
    )


@pytest.fixture
async def runs_stack() -> AsyncGenerator[tuple[AsyncClient, str]]:
    """真实 Console API + 真实 PostgresTraceStore 的 runs 列表栈。"""
    from sqlalchemy.ext.asyncio import create_async_engine

    from fluxion.repositories.trace_store import PostgresTraceStore

    tenant_id = f"tenant-runs-{uuid.uuid4().hex[:8]}"
    engine = create_async_engine(TEST_POSTGRES_DSN)
    trace_store: object = PostgresTraceStore(engine=engine)
    await trace_store.initialize()  # type: ignore[union-attr]
    registry: object = PostgreSQLRegistryStore(TEST_POSTGRES_DSN)
    await registry.initialize()  # type: ignore[union-attr]
    for index in range(1, 26):
        execution_id = f"exec-{index:03d}-{tenant_id[-4:]}"
        await trace_store.append(  # type: ignore[union-attr]
            _trace_record(
                f"trace-{index:03d}-{tenant_id[-4:]}",
                execution_id,
                tenant_id,
                error="boom" if index % 2 == 0 else None,
            )
        )
    service = ConsoleApplicationService(registry, trace_store=trace_store)  # type: ignore[arg-type]
    await service.initialize()
    client = AsyncClient(
        transport=ASGITransport(app=create_app(service)),
        base_url="http://testserver",
    )
    try:
        yield client, tenant_id
    finally:
        await client.aclose()
        await service.close()
        await engine.dispose()


class TestRunsServerFilter:
    async def test_status_filter_failed(self, runs_stack: tuple[AsyncClient, str]) -> None:
        """Runs 状态服务端过滤：failed 12 条（25 条中偶数）。"""
        client, tenant_id = runs_stack
        response = await client.get(
            "/api/v1/runs?status=failed&page=1&page_size=20",
            headers=_headers(tenant_id),
        )
        data = response.json()["data"]
        assert data["total"] == 12
        assert all(item["status"] == "failed" for item in data["items"])

    async def test_keyword_search_full_results(
        self, runs_stack: tuple[AsyncClient, str]
    ) -> None:
        """Runs keyword 全量搜索：命中 execution_id 子串，不过滤即不可见页。"""
        client, tenant_id = runs_stack
        suffix = tenant_id[-4:]
        response = await client.get(
            f"/api/v1/runs?keyword=exec-025-{suffix}",
            headers=_headers(tenant_id),
        )
        data = response.json()["data"]
        assert data["total"] == 1
        assert data["items"][0]["execution_id"] == f"exec-025-{suffix}"

    async def test_pagination_with_filter(self, runs_stack: tuple[AsyncClient, str]) -> None:
        """Runs 过滤与分页同一集合：succeeded 13 条，第 2 页（10/页）剩 3 条。"""
        client, tenant_id = runs_stack
        response = await client.get(
            "/api/v1/runs?status=succeeded&page=2&page_size=10",
            headers=_headers(tenant_id),
        )
        data = response.json()["data"]
        assert data["total"] == 13
        assert len(data["items"]) == 3
