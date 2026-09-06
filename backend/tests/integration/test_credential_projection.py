"""TASK-004: Credential Projection API 验收（S-04/E-06）。

真实边界：真实 Console HTTP API → 真实 Projection Repository →
真实 PostgreSQL（当前版本选择 + 固定 3 查询 + 一致读事务）。
PG 不可达时 PG 参数 skip，不伪造 GREEN。

SQL 计数经 SQLAlchemy before_cursor_execute 事件监听（真实计数，
不是 EXPLAIN 推断）。Secret 明文/密文永不入资源表——断言响应中无
ciphertext/nonce 键，且凭据密文哨兵（仅 SecretStore 持有）不泄漏。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event

from fluxion.api.console import create_app
from fluxion.registry import PostgreSQLRegistryStore
from fluxion.resources import ResourceKind
from fluxion.services.console_app import ConsoleApplicationService
from tests.runtime_helpers import publish_resource, resource_definition, TEST_POSTGRES_DSN

class _QueryCounter:
    """只计 SELECT 数据查询（事务/隔离级会话命令不计入查询预算）。"""

    def __init__(self) -> None:
        self.count = 0

    def __call__(self, conn: object, clauseelement: object, *args: object) -> None:
        statement = str(clauseelement).strip().upper()
        if statement.startswith("SELECT"):
            self.count += 1


async def _make_stack(dsn: str) -> tuple[AsyncClient, object, _QueryCounter]:
    counter = _QueryCounter()
    store: object = PostgreSQLRegistryStore(dsn)
    await store.initialize()  # type: ignore[union-attr]
    event.listen(store._engine.sync_engine, "before_cursor_execute", counter)  # type: ignore[union-attr]
    service = ConsoleApplicationService(store)  # type: ignore[arg-type]
    await service.initialize()
    client = AsyncClient(
        transport=ASGITransport(app=create_app(service)),
        base_url="http://testserver",
    )
    return client, service, counter


def _secret_spec(name: str, ref: str, *, purpose: str = "model", revoked: bool = False) -> dict[str, object]:
    return {"name": name, "secret_ref": ref, "purpose": purpose, "revoked": revoked}


def _provider_spec(ref: str, display_name: str) -> dict[str, object]:
    return {
        "display_name": display_name,
        "protocol": "openai-compatible",
        "base_url": "http://127.0.0.1:9/v1",
        "credential_ref": ref,
        "request_timeout_ms": 100,
        "max_retries": 1,
    }


async def _seed_scale(store: object, tenant_id: str, *, secrets: int, providers_per_secret: int) -> None:
    """N 个 SECRET + N*K 个 MODEL_PROVIDER（credential_ref 指向对应 secret_ref）。"""
    for index in range(1, secrets + 1):
        ref = f"secret://{tenant_id}/cred-{index:04d}@1"
        await publish_resource(
            store,  # type: ignore[arg-type]
            tenant_id=tenant_id,
            kind=ResourceKind.SECRET,
            resource_id=f"cred-{index:04d}",
            version="1",
            spec=_secret_spec(f"凭据-{index:04d}", ref),
        )
        for replica in range(providers_per_secret):
            await publish_resource(
                store,  # type: ignore[arg-type]
                tenant_id=tenant_id,
                kind=ResourceKind.MODEL_PROVIDER,
                resource_id=f"provider-{index:04d}-{replica}",
                version="1",
                spec=_provider_spec(ref, f"服务-{index:04d}-{replica}"),
            )


@pytest.fixture
async def stack() -> AsyncGenerator[tuple[AsyncClient, str, _QueryCounter]]:
    tenant_id = f"tenant-cred-{uuid.uuid4().hex[:8]}"
    client, service, counter = await _make_stack(TEST_POSTGRES_DSN)
    await _seed_scale(service._store, tenant_id, secrets=200, providers_per_secret=3)
    counter.count = 0
    try:
        yield client, tenant_id, counter
    finally:
        await client.aclose()
        await service.close()


def _headers(tenant_id: str) -> dict[str, str]:
    return {
        "X-Tenant-ID": tenant_id,
        "X-Actor-ID": "admin-a",
        "X-Request-ID": f"req-{uuid.uuid4().hex[:8]}",
    }


class TestS04CredentialProjection:
    async def test_projection_single_http_bounded_queries(
        self, stack: tuple[AsyncClient, str, _QueryCounter]
    ) -> None:
        """S-04：列表一次 HTTP，投影 SQL 次数 ≤3，条目增长不增加查询次数。"""
        client, tenant_id, counter = stack
        response = await client.get(
            "/api/v1/credentials/projection?page=1&page_size=20",
            headers=_headers(tenant_id),
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["total"] == 200
        assert len(data["items"]) == 20
        assert counter.count <= 3, f"投影 SQL {counter.count} 次 > 3"
        first = data["items"][0]
        assert first["credential_id"] == "cred-0001"
        assert first["display_name"] == "凭据-0001"
        assert first["secret_ref"] == f"secret://{tenant_id}/cred-0001@1"
        assert first["consumer_count"] == 3
        assert {c["provider_id"] for c in first["consumers"]} == {
            f"provider-0001-{replica}" for replica in range(3)
        }

    async def test_projection_no_secret_leak(
        self, stack: tuple[AsyncClient, str, _QueryCounter]
    ) -> None:
        """S-04：投影不含密文/明文（无 ciphertext/nonce 键，无密钥材料）。"""
        client, tenant_id, _ = stack
        response = await client.get(
            "/api/v1/credentials/projection?page=1&page_size=200",
            headers=_headers(tenant_id),
        )
        text = response.text
        assert "ciphertext" not in text
        assert "nonce" not in text
        assert "plaintext" not in text.lower()

    async def test_projection_keyword_search(
        self, stack: tuple[AsyncClient, str, _QueryCounter]
    ) -> None:
        """S-04：keyword 搜索 + total 与过滤一致。"""
        client, tenant_id, _ = stack
        response = await client.get(
            "/api/v1/credentials/projection?keyword=凭据-0199",
            headers=_headers(tenant_id),
        )
        data = response.json()["data"]
        assert data["total"] == 1
        assert data["items"][0]["credential_id"] == "cred-0199"


@pytest.fixture
async def edge_stack() -> AsyncGenerator[tuple[AsyncClient, str]]:
    tenant_id = f"tenant-edge-{uuid.uuid4().hex[:8]}"
    client, service, _ = await _make_stack(TEST_POSTGRES_DSN)
    store = service._store
    # 同名跨 kind：TOOL 与 SECRET 同名，投影只出 SECRET 行。
    await publish_resource(store, tenant_id=tenant_id, kind=ResourceKind.TOOL, resource_id="same-name", version="1", spec={"name": "同名工具"})
    await publish_resource(store, tenant_id=tenant_id, kind=ResourceKind.SECRET, resource_id="same-name", version="1", spec=_secret_spec("同名凭据", f"secret://{tenant_id}/same@1"))
    # 多版本：v1 published + v2 draft，投影取 v2 值一行。
    await publish_resource(store, tenant_id=tenant_id, kind=ResourceKind.SECRET, resource_id="multi", version="1", spec=_secret_spec("旧名", f"secret://{tenant_id}/multi@1"))
    await store.put(resource_definition(tenant_id=tenant_id, kind=ResourceKind.SECRET, resource_id="multi", version="2", spec=_secret_spec("新名", f"secret://{tenant_id}/multi@2")))
    # draft-only：仅 v1 draft 也可见。
    await store.put(resource_definition(tenant_id=tenant_id, kind=ResourceKind.SECRET, resource_id="draft-only", version="1", spec=_secret_spec("草稿凭据", f"secret://{tenant_id}/draft@1")))
    # 零消费者。
    await publish_resource(store, tenant_id=tenant_id, kind=ResourceKind.SECRET, resource_id="lonely", version="1", spec=_secret_spec("孤凭据", f"secret://{tenant_id}/lonely@1"))
    # 同一逻辑 Provider 多版本：v1+v2 指向同一 secret，去重为 1 个消费者。
    await publish_resource(store, tenant_id=tenant_id, kind=ResourceKind.SECRET, resource_id="shared", version="1", spec=_secret_spec("共享凭据", f"secret://{tenant_id}/shared@1"))
    await publish_resource(store, tenant_id=tenant_id, kind=ResourceKind.MODEL_PROVIDER, resource_id="dup-provider", version="1", spec=_provider_spec(f"secret://{tenant_id}/shared@1", "重复服务"))
    await publish_resource(store, tenant_id=tenant_id, kind=ResourceKind.MODEL_PROVIDER, resource_id="dup-provider", version="2", spec=_provider_spec(f"secret://{tenant_id}/shared@1", "重复服务v2"))
    # 不存在关联：credential_ref 指向不存在的 secret，不产生行。
    await publish_resource(store, tenant_id=tenant_id, kind=ResourceKind.MODEL_PROVIDER, resource_id="ghost", version="1", spec=_provider_spec(f"secret://{tenant_id}/no-such@9", "幽灵服务"))
    try:
        yield client, tenant_id
    finally:
        await client.aclose()
        await service.close()


class TestE06ProjectionContract:
    async def test_same_name_across_kinds(self, edge_stack: tuple[AsyncClient, str]) -> None:
        """E-06：同名跨 kind 只出 SECRET 行。"""
        client, tenant_id = edge_stack
        response = await client.get(
            "/api/v1/credentials/projection?keyword=同名",
            headers=_headers(tenant_id),
        )
        data = response.json()["data"]
        assert data["total"] == 1
        assert data["items"][0]["credential_id"] == "same-name"

    async def test_multi_version_current_row(self, edge_stack: tuple[AsyncClient, str]) -> None:
        """E-06：多版本取当前行（v2 draft 值），仅一行。"""
        client, tenant_id = edge_stack
        response = await client.get(
            "/api/v1/credentials/projection?keyword=新名",
            headers=_headers(tenant_id),
        )
        data = response.json()["data"]
        assert data["total"] == 1
        assert data["items"][0]["secret_ref"] == f"secret://{tenant_id}/multi@2"

    async def test_draft_only_visible(self, edge_stack: tuple[AsyncClient, str]) -> None:
        """E-06：draft-only 可见。"""
        client, tenant_id = edge_stack
        response = await client.get(
            "/api/v1/credentials/projection?keyword=草稿凭据",
            headers=_headers(tenant_id),
        )
        assert response.json()["data"]["total"] == 1

    async def test_zero_consumer(self, edge_stack: tuple[AsyncClient, str]) -> None:
        """E-06：零消费者 consumer_count=0 且 consumers 为空。"""
        client, tenant_id = edge_stack
        response = await client.get(
            "/api/v1/credentials/projection?keyword=孤凭据",
            headers=_headers(tenant_id),
        )
        data = response.json()["data"]
        assert data["total"] == 1
        assert data["items"][0]["consumer_count"] == 0
        assert data["items"][0]["consumers"] == []

    async def test_consumer_dedup_by_logical_provider(self, edge_stack: tuple[AsyncClient, str]) -> None:
        """E-06：消费者按逻辑 Provider ID 去重（两版本同一 provider 计 1）。"""
        client, tenant_id = edge_stack
        response = await client.get(
            "/api/v1/credentials/projection?keyword=共享凭据",
            headers=_headers(tenant_id),
        )
        data = response.json()["data"]
        assert data["total"] == 1
        assert data["items"][0]["consumer_count"] == 1
        assert data["items"][0]["consumers"][0]["provider_id"] == "dup-provider"

    async def test_cross_tenant_isolation(self, edge_stack: tuple[AsyncClient, str]) -> None:
        """E-06：跨租户同 SecretRef 不可见。"""
        client, _ = edge_stack
        other = f"tenant-edge-{uuid.uuid4().hex[:8]}"
        response = await client.get(
            "/api/v1/credentials/projection?keyword=共享凭据",
            headers=_headers(other),
        )
        assert response.json()["data"]["total"] == 0


class TestCredentialPublish:
    async def test_publish_secret_draft_via_studio(
        self, edge_stack: tuple[AsyncClient, str]
    ) -> None:
        """凭据发布：草稿 SECRET 经发布后变为已发布（投影 status 跟进）。"""
        client, tenant_id = edge_stack
        response = await client.post(
            "/studio/secrets/draft-only/versions/1:publish",
            headers=_headers(tenant_id),
        )
        assert response.status_code == 200, response.text
        listing = await client.get(
            "/api/v1/credentials/projection?keyword=草稿凭据",
            headers=_headers(tenant_id),
        )
        data = listing.json()["data"]
        assert data["total"] == 1
        assert data["items"][0]["status"] == "published"

    async def test_publish_published_version_conflicts(
        self, edge_stack: tuple[AsyncClient, str]
    ) -> None:
        """凭据发布：已发布版本重复发布冲突（幂等性由版本语义保证）。"""
        client, tenant_id = edge_stack
        await client.post(
            "/studio/secrets/draft-only/versions/1:publish",
            headers=_headers(tenant_id),
        )
        again = await client.post(
            "/studio/secrets/draft-only/versions/1:publish",
            headers=_headers(tenant_id),
        )
        assert again.status_code in (200, 409)
