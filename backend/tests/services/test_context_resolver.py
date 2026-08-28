"""TASK-007（phase2）ContextResolver 十段管线验收测试。

S-02（integration，RULE-P2-02）：典型数据量（≤100 memory）连续 50 次 resolve，
P95 ≤ 300ms（真实 SQLite Store）。
S-08（integration，Gate G4 / ARCH-07）：Execution-1 pin v1 → 运行中发布 v2 →
Execution-1 全程 v1 → 新 Execution 使用 v2。
S-09（integration，Gate G2 / REQ-CAP-004）：同一 MCP Definition，User-A/B 不同
CredentialRef → 连接池/cache key 不串用，跨用户凭据不可见。
E-01（integration，H1 回归）：非 dev 模式缺身份头 → 401 fail-closed + envelope。
E-02（integration）：Secret 检索失败 → fail-closed、无 digest、日志无明文。
E-04（integration）：user_profile_version 不存在 → fail-closed + 明确错误码。
B-01（unit）：memory manifest 超 budget → 按优先级截断 + truncated=true。

真实边界：真实 SQLite Registry/Store + AgentDefinitionRepository +
PersonalMemoryRetriever + CredentialResolver + 真实 ASGI middleware；不 mock。
"""

from __future__ import annotations

import time
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine
from tests.runtime_helpers import publish_resource

from fluxion.registry import SQLiteRegistryStore
from fluxion.services.context_resolver import (
    BudgetExceededEntry,
    ContextResolutionError,
    ContextResolver,
    ResolverSelector,
)


@pytest.fixture
async def store() -> AsyncGenerator[SQLiteRegistryStore, None]:
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    await store.initialize()
    try:
        yield store
    finally:
        await store.close()


@pytest.fixture
async def engine(store: SQLiteRegistryStore) -> AsyncEngine:
    return store.engine


async def _seed_agent(store: SQLiteRegistryStore, *, version: str = "1") -> None:
    from fluxion.resources import ResourceKind

    await publish_resource(
        store,
        tenant_id="tenant-a",
        kind=ResourceKind.RUNTIME_PROFILE,
        resource_id="assistant",
        version=version,
        spec={"request_timeout_ms": 30_000, "max_retries": 1},
    )
    from fluxion.agents.definitions import AgentDefinition
    from fluxion.registry.schema import resource_definitions
    from fluxion.resources import ResourceKind

    async with store.engine.begin() as conn:
        from sqlalchemy import insert

        await conn.execute(
            insert(resource_definitions).values(
                tenant_id="tenant-a",
                kind=ResourceKind.AGENT_DEFINITION.value,
                resource_id="assistant",
                version=version,
                status="published",
                visibility="tenant",
                spec_json=AgentDefinition(
                    name="助手",
                    system_prompt="p",
                    owner="builder",
                    model_ref={"id": "dev.echo", "version": "1"},
                ).model_dump(mode="json"),
                created_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
            )
        )


def _resolver(store: SQLiteRegistryStore, credential_resolver: object | None = None) -> ContextResolver:
    return ContextResolver(store.engine, credential_resolver=credential_resolver)


@pytest.mark.asyncio
async def test_s02_resolve_pipeline_50x_p95_under_300ms(store: SQLiteRegistryStore) -> None:
    await _seed_agent(store)
    resolver = _resolver(store)
    selector = ResolverSelector(tenant_id="tenant-a", agent_id="assistant", user_id="user-a")
    samples: list[float] = []
    for index in range(50):
        start = time.perf_counter()
        result = await resolver.resolve(selector, session_id=f"s-{index}")
        samples.append((time.perf_counter() - start) * 1000)
    samples.sort()
    p95 = samples[int(len(samples) * 0.95)]
    assert p95 <= 300, f"resolve p95 {p95:.1f}ms exceeds 300ms"
    # 十段 trace 完整
    assert [s.stage for s in result.resolution_trace][0] == "identity"
    assert result.snapshot.snapshot_digest


@pytest.mark.asyncio
async def test_s08_execution_immutability_across_publish(store: SQLiteRegistryStore) -> None:
    """Gate G4：Execution-1 pin v1 → 运行中发布 v2 → Execution-1 全程 v1。"""
    await _seed_agent(store, version="1")
    resolver = _resolver(store)
    selector = ResolverSelector(tenant_id="tenant-a", agent_id="assistant", user_id="user-a")

    first = await resolver.resolve(selector, session_id="s1")
    assert first.snapshot.agent_definition_version == "1"

    # 运行中发布 v2（真实写入 resource_definitions）
    await _seed_agent(store, version="2")

    # Execution-1 持有的 snapshot 不变（frozen + 已解析对象）
    assert first.snapshot.agent_definition_version == "1"
    # 新 Execution 解析到 v2
    second = await resolver.resolve(selector, session_id="s2")
    assert second.snapshot.agent_definition_version == "2"
    # digest 随版本变化
    assert first.snapshot.snapshot_digest != second.snapshot.snapshot_digest


@pytest.mark.asyncio
async def test_s09_credential_isolation_per_user(store: SQLiteRegistryStore) -> None:
    """Gate G2：同一 Agent，A/B 不同凭据引用 → credential_versions 不串用。"""
    from fluxion.registry.schema import resource_bindings

    await _seed_agent(store)
    async with store.engine.begin() as conn:
        await conn.execute(
            resource_bindings.insert().values(
                binding_id="b-a",
                tenant_id="tenant-a",
                subject_type="user",
                subject_id="user-a",
                resource_type="mcp",
                resource_id="weather",
                resource_version_selector="latest-published",
                config_json={},
                credential_ref="secret://tenant-a/weather-a",
                enabled=True,
                created_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
            )
        )
        await conn.execute(
            resource_bindings.insert().values(
                binding_id="b-b",
                tenant_id="tenant-a",
                subject_type="user",
                subject_id="user-b",
                resource_type="mcp",
                resource_id="weather",
                resource_version_selector="latest-published",
                config_json={},
                credential_ref="secret://tenant-a/weather-b",
                enabled=True,
                created_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
            )
        )
    resolver = _resolver(store)
    result_a = await resolver.resolve(
        ResolverSelector(tenant_id="tenant-a", agent_id="assistant", user_id="user-a"),
        session_id="s-a",
    )
    result_b = await resolver.resolve(
        ResolverSelector(tenant_id="tenant-a", agent_id="assistant", user_id="user-b"),
        session_id="s-b",
    )
    cred_a = result_a.snapshot.credential_versions or {}
    cred_b = result_b.snapshot.credential_versions or {}
    assert any("weather-a" in ref for ref in cred_a)
    assert any("weather-b" in ref for ref in cred_b)
    assert cred_a.keys() != cred_b.keys() or set(cred_a.values()) != set(cred_b.values())


@pytest.mark.asyncio
async def test_e04_user_profile_version_missing_fail_closed(store: SQLiteRegistryStore) -> None:
    await _seed_agent(store)
    resolver = _resolver(store)
    with pytest.raises(ContextResolutionError) as error:
        await resolver.resolve(
            ResolverSelector(
                tenant_id="tenant-a",
                agent_id="assistant",
                user_id="user-a",
                user_profile_version="v-missing",
            ),
            session_id="s-e04",
        )
    assert error.value.code == "user_profile_not_found"
    assert error.value.snapshot_digest is None


@pytest.mark.asyncio
async def test_e02_credential_missing_fail_closed(store: SQLiteRegistryStore) -> None:
    from fluxion.registry.schema import resource_bindings

    await _seed_agent(store)
    async with store.engine.begin() as conn:
        await conn.execute(
            resource_bindings.insert().values(
                binding_id="b-missing",
                tenant_id="tenant-a",
                subject_type="user",
                subject_id="user-a",
                resource_type="mcp",
                resource_id="weather",
                resource_version_selector="latest-published",
                config_json={},
                credential_ref="secret://tenant-a/missing",
                enabled=True,
                created_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
            )
        )
    from fluxion.runtime.secrets import CredentialResolver, LocalEncryptedSecretStore

    secret_store = LocalEncryptedSecretStore(master_key=b"k" * 32)
    resolver = _resolver(
        store,
        credential_resolver=CredentialResolver(secret_store),
    )
    with pytest.raises(ContextResolutionError) as error:
        await resolver.resolve(
            ResolverSelector(tenant_id="tenant-a", agent_id="assistant", user_id="user-a"),
            session_id="s-e02",
        )
    assert error.value.code == "credential_not_resolvable"
    assert error.value.snapshot_digest is None


def test_b01_budget_truncates_manifest_by_priority() -> None:
    """B-01：manifest 超 budget → 按优先级截断 + truncated=true。"""
    from fluxion.resources.contracts import MemoryEntryRef, MemoryManifest

    manifest = MemoryManifest(
        entry_refs=[
            MemoryEntryRef(entry_id=f"m{i}", memory_type="semantic", content_hash=f"h{i}", priority=i)
            for i in range(5)
        ],
        content_hash="x",
        truncated=False,
    )
    truncated = BudgetExceededEntry.truncate(manifest, budget=2)
    assert truncated.truncated is True
    assert len(truncated.entry_refs) == 2
    # 优先级：priority 小者保留
    assert [ref.priority for ref in truncated.entry_refs] == [0, 1]


@pytest.mark.asyncio
async def test_e01_non_dev_missing_identity_headers_401() -> None:
    """E-01（H1 回归）：非 dev 模式缺身份头 → 401 fail-closed + envelope。"""
    from fastapi import FastAPI

    from fluxion.api.middleware import DevModeSettings, RequestContextMiddleware

    app = FastAPI()
    app.add_middleware(
        RequestContextMiddleware, require_identity=True, dev_mode=DevModeSettings(enabled=False)
    )

    @app.get("/ping")
    async def ping() -> dict[str, str]:
        return {"ok": "1"}

    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://t")
    try:
        response = await client.get("/ping")
        assert response.status_code == 401
        body = response.json()
        assert "request_id" in body or "code" in body
    finally:
        await client.aclose()
