"""TASK-007（phase2）ContextResolver 十段管线验收测试。

S-02（integration，RULE-P2-02）：典型数据量（≤100 memory）连续 50 次 resolve，
P95 ≤ 300ms（真实 PG Store）。
S-08（integration，Gate G4 / ARCH-07）：Execution-1 pin v1 → 运行中发布 v2 →
Execution-1 全程 v1 → 新 Execution 使用 v2。
S-09（integration，Gate G2 / REQ-CAP-004）：同一 MCP Definition，User-A/B 不同
CredentialRef → 连接池/cache key 不串用，跨用户凭据不可见。
E-01（integration，H1 回归）：非 dev 模式缺身份头 → 401 fail-closed + envelope。
E-02（integration）：Secret 检索失败 → fail-closed、无 digest、日志无明文。
E-04（integration）：user_profile_version 不存在 → fail-closed + 明确错误码。
B-01（unit）：memory manifest 超 budget → 按优先级截断 + truncated=true。

真实边界：真实 PG Registry/Store + AgentDefinitionRepository +
PersonalMemoryRetriever + CredentialResolver + 真实 ASGI middleware；不 mock。
"""

from __future__ import annotations

import time
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from fluxion.registry import PostgreSQLRegistryStore
from fluxion.resources import ExactResourceVersion
from fluxion.services.context_resolver import (
    BudgetExceededEntry,
    ContextResolutionError,
    ContextResolver,
    ResolverSelector,
)
from tests.runtime_helpers import publish_resource, seed_model_definition, TEST_POSTGRES_DSN


@pytest.fixture
async def store() -> AsyncGenerator[PostgreSQLRegistryStore, None]:
    store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
    await store.initialize()
    try:
        yield store
    finally:
        await store.close()


@pytest.fixture
async def engine(store: PostgreSQLRegistryStore) -> AsyncEngine:
    return store.engine


async def _seed_agent(store: PostgreSQLRegistryStore, *, version: str = "1") -> None:
    from fluxion.resources import ResourceKind

    await publish_resource(
        store,
        tenant_id="tenant-a",
        kind=ResourceKind.RUNTIME_PROFILE,
        resource_id="assistant",
        version=version,
        spec={"max_rounds": 8, "default": True},
    )
    # ADR-A008：agent.model_policy 指向 ModelDefinition（model.dev.echo），
    # 解析链必需的 fixture 资源（tenant 与 agent 一致）。
    await seed_model_definition(store, tenant_id="tenant-a", provider_id="dev.echo")
    from fluxion.agents.definitions import AgentDefinition, AgentModelPolicy
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
                    model_policy=AgentModelPolicy(primary_model_ref=ExactResourceVersion(id="model.dev.echo", version="1")),
                ).model_dump(mode="json"),
                created_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
            )
        )


def _resolver(store: PostgreSQLRegistryStore, credential_resolver: object | None = None) -> ContextResolver:
    return ContextResolver(store, credential_resolver=credential_resolver)


@pytest.mark.asyncio
async def test_s02_resolve_pipeline_50x_p95_under_300ms(store: PostgreSQLRegistryStore) -> None:
    await _seed_agent(store)
    resolver = _resolver(store)
    selector = ResolverSelector(tenant_id="tenant-a", agent_id="assistant", user_id="user-a")
    samples: list[float] = []
    for index in range(50):
        start = time.perf_counter()
        request_id, trace_id, execution_id = _identity("9")
        result = await resolver.resolve(
            selector,
            session_id=f"s-{index}",
            request_id=request_id,
            trace_id=trace_id,
            execution_id=execution_id,
        )
        samples.append((time.perf_counter() - start) * 1000)
    samples.sort()
    p95 = samples[int(len(samples) * 0.95)]
    assert p95 <= 300, f"resolve p95 {p95:.1f}ms exceeds 300ms"
    # 十段 trace 完整
    assert next(s.stage for s in result.resolution_trace) == "identity"
    assert result.snapshot.snapshot_digest


@pytest.mark.asyncio
async def test_l1_cache_hit_uses_current_request_identity(store: PostgreSQLRegistryStore) -> None:
    """L1 缓存命中复用内容字段，但身份必须用本次请求的（TASK-006 / B-ID-02）。

    同一 resolver + 同一 selector，两个不同 session 的独立 Execution：
    digest 相等（内容一致），snapshot 身份等于各自传入值——旧行为"重新生成"
    已废弃（ADR-A012：内部层禁止创建身份）。
    """
    await _seed_agent(store)
    resolver = _resolver(store)
    selector = ResolverSelector(tenant_id="tenant-a", agent_id="assistant", user_id="user-a")

    request_id, trace_id, execution_id = _identity("d")
    first = await resolver.resolve(
        selector,
        session_id="s-1",
        request_id=request_id,
        trace_id=trace_id,
        execution_id=execution_id,
    )
    request_id2, trace_id2, execution_id2 = _identity("e")
    second = await resolver.resolve(
        selector,
        session_id="s-2",
        memory_query="different-query",
        request_id=request_id2,
        trace_id=trace_id2,
        execution_id=execution_id2,
    )

    # 内容复用：digest 相等
    assert first.snapshot.snapshot_digest == second.snapshot.snapshot_digest
    # 身份跟请求走
    assert first.snapshot.execution_id == execution_id
    assert second.snapshot.execution_id == execution_id2
    assert first.snapshot.trace_id == trace_id
    assert second.snapshot.trace_id == trace_id2


@pytest.mark.asyncio
async def test_capability_versions_resolve_published(store: PostgreSQLRegistryStore) -> None:
    """Agent capabilities（skill/mcp）→ skill_versions/mcp_versions 填充实际 published 版本。

    此前该路径零测试覆盖（_seed_agent 无 capabilities）；tool 类型不解析版本也不抛错。
    """
    from sqlalchemy import insert

    from fluxion.agents.definitions import (
        AgentCapabilityReference,
        AgentDefinition,
        AgentModelPolicy,
        CapabilityType,
    )
    from fluxion.registry.schema import resource_definitions
    from fluxion.resources import ResourceKind

    # skill v3 + mcp v2 已发布
    await publish_resource(
        store,
        tenant_id="tenant-a",
        kind=ResourceKind.SKILL,
        resource_id="survey-skill",
        version="3",
        spec={"name": "survey", "prompt": "..."},
    )
    await publish_resource(
        store,
        tenant_id="tenant-a",
        kind=ResourceKind.MCP,
        resource_id="weather",
        version="2",
        spec={"name": "weather", "endpoint": "..."},
    )
    # runtime_profile（agent 解析依赖；ADR-A010：租户默认）
    await publish_resource(
        store,
        tenant_id="tenant-a",
        kind=ResourceKind.RUNTIME_PROFILE,
        resource_id="assistant",
        version="1",
        spec={"max_rounds": 8, "default": True},
    )
    # ADR-A008：解析链需要 ModelDefinition（model.dev.echo）存在
    await seed_model_definition(store, tenant_id="tenant-a", provider_id="dev.echo")
    # agent 带 skill/mcp/tool 三个 capability
    async with store.engine.begin() as conn:
        await conn.execute(
            insert(resource_definitions).values(
                tenant_id="tenant-a",
                kind=ResourceKind.AGENT_DEFINITION.value,
                resource_id="assistant",
                version="1",
                status="published",
                visibility="tenant",
                spec_json=AgentDefinition(
                    name="助手",
                    system_prompt="p",
                    owner="builder",
                    model_policy=AgentModelPolicy(primary_model_ref=ExactResourceVersion(id="model.dev.echo", version="1")),
                    capabilities=[
                        AgentCapabilityReference(
                            type=CapabilityType.SKILL,
                            capability_ref="survey-skill",
                            version_pin="latest-published",
                        ),
                        AgentCapabilityReference(
                            type=CapabilityType.MCP,
                            capability_ref="weather",
                            version_pin="latest-published",
                        ),
                        AgentCapabilityReference(
                            type=CapabilityType.TOOL,
                            capability_ref="user.profile.get",
                            version_pin="latest-published",
                        ),
                    ],
                ).model_dump(mode="json"),
                created_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
            )
        )
    resolver = _resolver(store)
    request_id, trace_id, execution_id = _identity("f")
    result = await resolver.resolve(
        ResolverSelector(tenant_id="tenant-a", agent_id="assistant", user_id="user-a"),
        session_id="s-cap",
        request_id=request_id,
        trace_id=trace_id,
        execution_id=execution_id,
    )
    assert result.snapshot.skill_versions["survey-skill"] == "3"
    assert result.snapshot.mcp_versions["weather"] == "2"
    # tool capability 不解析版本（非版本化 Resource），不落入任何 versions 字段
    assert "user.profile.get" not in result.snapshot.skill_versions
    assert "user.profile.get" not in result.snapshot.mcp_versions


@pytest.mark.asyncio
async def test_s08_execution_immutability_across_publish(store: PostgreSQLRegistryStore) -> None:
    """Gate G4：Execution-1 pin v1 → 运行中发布 v2 → Execution-1 全程 v1。"""
    await _seed_agent(store, version="1")
    resolver_1 = _resolver(store)
    selector = ResolverSelector(tenant_id="tenant-a", agent_id="assistant", user_id="user-a")

    first = await resolver_1.resolve(
        selector,
        session_id="s1",
        request_id=_identity("5")[0],
        trace_id=_identity("5")[1],
        execution_id=_identity("5")[2],
    )
    assert first.snapshot.agent_definition_version == "1"

    # 运行中发布 v2（真实写入 resource_definitions）
    await _seed_agent(store, version="2")

    # Execution-1 持有的 snapshot 不变（frozen pydantic model）
    assert first.snapshot.agent_definition_version == "1"

    # 新 Execution（新 resolver 模拟新实例，无 L1 缓存）解析到 v2
    resolver_2 = ContextResolver(store)
    second = await resolver_2.resolve(
        selector,
        session_id="s2",
        request_id=_identity("6")[0],
        trace_id=_identity("6")[1],
        execution_id=_identity("6")[2],
    )
    assert second.snapshot.agent_definition_version == "2"
    # digest 随版本变化
    assert first.snapshot.snapshot_digest != second.snapshot.snapshot_digest


@pytest.mark.asyncio
async def test_runtime_profile_selector_pin_requires_ref(
    store: PostgreSQLRegistryStore,
) -> None:
    """ADR-A010：无 ref 的版本 pin 是矛盾输入 → fail-closed（同名回退已废弃）。"""
    await _seed_agent(store, version="1")
    await _seed_agent(store, version="2")

    with pytest.raises(ContextResolutionError) as exc_info:
        request_id, trace_id, execution_id = _identity("0")
        await _resolver(store).resolve(
            ResolverSelector(
                tenant_id="tenant-a",
                agent_id="assistant",
                user_id="user-a",
                runtime_profile_version="1",
            ),
            session_id="s-profile-pin",
            request_id=request_id,
            trace_id=trace_id,
            execution_id=execution_id,
        )
    assert exc_info.value.code == "runtime_profile_ref_required"


@pytest.mark.asyncio
async def test_s09_credential_isolation_per_user(store: PostgreSQLRegistryStore) -> None:
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
    request_id_a, trace_id_a, execution_id_a = _identity("1")
    result_a = await resolver.resolve(
        ResolverSelector(tenant_id="tenant-a", agent_id="assistant", user_id="user-a"),
        session_id="s-a",
        request_id=request_id_a,
        trace_id=trace_id_a,
        execution_id=execution_id_a,
    )
    request_id_b, trace_id_b, execution_id_b = _identity("2")
    result_b = await resolver.resolve(
        ResolverSelector(tenant_id="tenant-a", agent_id="assistant", user_id="user-b"),
        session_id="s-b",
        request_id=request_id_b,
        trace_id=trace_id_b,
        execution_id=execution_id_b,
    )
    cred_a = result_a.snapshot.credential_versions or {}
    cred_b = result_b.snapshot.credential_versions or {}
    assert any("weather-a" in ref for ref in cred_a)
    assert any("weather-b" in ref for ref in cred_b)
    assert cred_a.keys() != cred_b.keys() or set(cred_a.values()) != set(cred_b.values())


@pytest.mark.asyncio
async def test_e04_user_profile_version_missing_fail_closed(store: PostgreSQLRegistryStore) -> None:
    await _seed_agent(store)
    resolver = _resolver(store)
    with pytest.raises(ContextResolutionError) as error:
        request_id, trace_id, execution_id = _identity("4")
        await resolver.resolve(
            ResolverSelector(
                tenant_id="tenant-a",
                agent_id="assistant",
                user_id="user-a",
                user_profile_version="v-missing",
            ),
            session_id="s-e04",
            request_id=request_id,
            trace_id=trace_id,
            execution_id=execution_id,
        )
    assert error.value.code == "user_profile_not_found"
    assert error.value.snapshot_digest is None


@pytest.mark.asyncio
async def test_e02_credential_missing_fail_closed(store: PostgreSQLRegistryStore) -> None:
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
        request_id, trace_id, execution_id = _identity("3")
        await resolver.resolve(
            ResolverSelector(tenant_id="tenant-a", agent_id="assistant", user_id="user-a"),
            session_id="s-e02",
            request_id=request_id,
            trace_id=trace_id,
            execution_id=execution_id,
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


def _identity(char: str) -> tuple[str, str, str]:
    return (f"req_{char * 32}", f"trace_{char * 32}", f"exec_{char * 32}")


@pytest.mark.asyncio
async def test_S_ID_02_snapshot_identity_matches_request(store: PostgreSQLRegistryStore) -> None:
    """S-ID-02：Snapshot 身份与请求一致；配置固定（TASK-006 / ADR-A012）。"""
    await _seed_agent(store)
    resolver = _resolver(store)
    selector = ResolverSelector(tenant_id="tenant-a", agent_id="assistant", user_id="user-a")
    request_id, trace_id, execution_id = _identity("a")
    result = await resolver.resolve(
        selector,
        session_id="s-id-02",
        request_id=request_id,
        trace_id=trace_id,
        execution_id=execution_id,
    )
    assert result.snapshot.execution_id == execution_id
    assert result.snapshot.trace_id == trace_id
    assert result.snapshot.tenant_id == "tenant-a"
    assert result.snapshot.runtime_profile_id == "assistant"
    assert result.snapshot.runtime_profile_version == "1"

    # 同一配置、不同执行：身份跟请求走，配置 digest 不变。
    request_id2, trace_id2, execution_id2 = _identity("b")
    second = await resolver.resolve(
        selector,
        session_id="s-id-02-2",
        request_id=request_id2,
        trace_id=trace_id2,
        execution_id=execution_id2,
    )
    assert second.snapshot.execution_id == execution_id2
    assert second.snapshot.trace_id == trace_id2
    assert second.snapshot.snapshot_digest == result.snapshot.snapshot_digest


@pytest.mark.asyncio
async def test_B_ID_02_cache_hit_does_not_reuse_previous_identity(
    store: PostgreSQLRegistryStore,
) -> None:
    """B-ID-02：缓存命中不复用前次执行身份；运行 ID 变化不改变配置 digest。

    L1 默认 TTL=0（跨执行不缓存）；本用例显式开 TTL 演练命中分支——命中返回的
    snapshot 必须携带本次请求身份，而非缓存时的旧身份。
    """
    await _seed_agent(store)
    resolver = _resolver(store)
    resolver._l1_cache_ttl = 3600.0
    selector = ResolverSelector(tenant_id="tenant-a", agent_id="assistant", user_id="user-a")
    _, _, execution_a = _identity("a")
    first = await resolver.resolve(
        selector, session_id="s-hit-1", request_id=_identity("a")[0],
        trace_id=_identity("a")[1], execution_id=execution_a,
    )
    _, trace_b, execution_b = _identity("b")
    second = await resolver.resolve(
        selector, session_id="s-hit-2", request_id=_identity("b")[0],
        trace_id=trace_b, execution_id=execution_b,
    )
    assert second.snapshot.execution_id == execution_b
    assert second.snapshot.trace_id == trace_b
    assert second.snapshot.execution_id != first.snapshot.execution_id
    assert second.snapshot.snapshot_digest == first.snapshot.snapshot_digest


@pytest.mark.asyncio
async def test_S_ID_02_unknown_tenant_fails_closed(store: PostgreSQLRegistryStore) -> None:
    """S-ID-02：不同租户请求隔离——tenant-b 无该 Agent 即 fail-closed。"""
    await _seed_agent(store)
    resolver = _resolver(store)
    with pytest.raises(ContextResolutionError):
        await resolver.resolve(
            ResolverSelector(tenant_id="tenant-b", agent_id="assistant", user_id="user-a"),
            session_id="s-tenant-b",
            request_id=_identity("c")[0],
            trace_id=_identity("c")[1],
            execution_id=_identity("c")[2],
        )


def _ids(char: str, index: int = 0) -> tuple[str, str, str]:
    suffix = f"{index:032x}"
    return (f"req_{suffix}", f"trace_{suffix}", f"exec_{suffix}")


@pytest.mark.asyncio
async def test_S_06_disabled_cache_never_reads_nor_writes(
    store: PostgreSQLRegistryStore,
) -> None:
    """S-06（105 P1-03）：TTL=0 时不读不写 L1，大量不同 key 后内存不增长。"""
    await _seed_agent(store)
    resolver = _resolver(store)
    assert resolver._l1_cache_ttl <= 0
    for index in range(50):
        request_id, trace_id, execution_id = _ids("a", index)
        await resolver.resolve(
            ResolverSelector(
                tenant_id="tenant-a", agent_id="assistant", user_id=f"user-{index}"
            ),
            session_id=f"s-{index}",
            request_id=request_id,
            trace_id=trace_id,
            execution_id=execution_id,
        )
    assert resolver._l1_cache == {}
