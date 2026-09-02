"""ADR-A008 三层解析（TASK-002 返工）：AgentDefinition.model_policy →
ModelDefinition → ProviderDefinition。

B-S-01：快照冻结 provider exact version；ModelDefinition.name 进 ModelPolicy.model
（进 ModelRequest.model）；引用缺失 fail-closed，不回退 legacy 直引。
"""

from __future__ import annotations

import pytest

from fluxion.registry import RegistryStore
from fluxion.resources import (
    ExecutionSnapshot,
    ResourceDefinition,
    ResourceKind,
    ResourceStatus,
)
from fluxion.runtime.context import RequestContext
from fluxion.services.context_resolver import (
    ContextResolutionError,
    ContextResolver,
    ContextResolverSnapshotBuilder,
)
from tests.runtime_helpers import publish_resource, seed_runtime_profile


async def _seed_agent(
    store: RegistryStore,
    *,
    agent_id: str,
    primary_model_ref: dict[str, str],
    fallback_model_refs: list[dict[str, str]] | None = None,
    model_timeout_ms: int = 60_000,
    model_deadline_ms: int = 120_000,
) -> None:
    await publish_resource(
        store,
        tenant_id="tenant-a",
        kind=ResourceKind.AGENT_DEFINITION,
        resource_id=agent_id,
        version="1",
        spec={
            "name": agent_id,
            "system_prompt": "x",
            "owner": "admin",
            "model_policy": {
                "primary_model_ref": primary_model_ref,
                "fallback_model_refs": fallback_model_refs or [],
                "model_timeout_ms": model_timeout_ms,
                "model_deadline_ms": model_deadline_ms,
            },
            "runtime_profile_ref": {"id": "assistant", "version": "1"},
            "capabilities": [],
        },
    )


async def _build_snapshot(
    store: RegistryStore, agent_id: str
) -> ExecutionSnapshot:
    return await ContextResolverSnapshotBuilder(ContextResolver(store)).build(
        RequestContext(
            tenant_id="tenant-a",
            user_id="user-a",
            runtime_profile_id="assistant",
            agent_definition_id=agent_id,
            session_id="session-a",
        )
    )


@pytest.mark.asyncio
async def test_B_S01_model_policy_resolves_model_definition_to_provider(
    sqlite_store: RegistryStore,
) -> None:
    await seed_runtime_profile(sqlite_store)
    await publish_resource(
        sqlite_store,
        tenant_id="tenant-a",
        kind=ResourceKind.MODEL_PROVIDER,
        resource_id="prov-deepseek",
        version="1",
        spec={
            "protocol": "openai-compatible",
            "base_url": "https://api.deepseek.com",
            "credential_ref": "secret://tenant-a/openai",
            "default_model": "deepseek-chat",
        },
    )
    await publish_resource(
        sqlite_store,
        tenant_id="tenant-a",
        kind=ResourceKind.MODEL_DEFINITION,
        resource_id="deepseek-chat",
        version="1",
        spec={"name": "deepseek-chat", "provider_ref": {"id": "prov-deepseek", "version": "1"}},
    )
    await _seed_agent(
        sqlite_store,
        agent_id="model-policy-agent",
        primary_model_ref={"id": "deepseek-chat", "version": "1"},
    )

    snapshot = await _build_snapshot(sqlite_store, "model-policy-agent")

    # provider_ref 经 ModelDefinition 解析（exact version 冻结）
    assert snapshot.model_resolution.routes[0].provider_ref.id == "prov-deepseek"
    assert snapshot.model_resolution.routes[0].provider_ref.version == "1"
    # ModelDefinition.name 与 provider pin 在同一条 route 中冻结。
    assert snapshot.model_resolution.routes[0].model == "deepseek-chat"
    assert snapshot.model_resolution.model_timeout_ms == 60_000
    assert snapshot.model_resolution.model_deadline_ms == 120_000
    # 运行期 provider pin 来自解析结果（而非 legacy model_ref）
    assert snapshot.plugin_versions == {"prov-deepseek": "1"}


@pytest.mark.asyncio
async def test_B_S01_fallback_chain_freezes_exact_provider_versions(
    sqlite_store: RegistryStore,
) -> None:
    """fallback_model_refs 经 ModelDefinition 解析为 provider exact version
    （不降级 latest-published）；主 + 回退 provider 全部进 plugin_versions。"""
    await seed_runtime_profile(sqlite_store)
    for provider_id, version in (("prov-a", "1"), ("prov-b", "2")):
        await publish_resource(
            sqlite_store,
            tenant_id="tenant-a",
            kind=ResourceKind.MODEL_PROVIDER,
            resource_id=provider_id,
                version=version,
                spec={
                    "protocol": "openai-compatible",
                    "base_url": f"https://{provider_id}.example.com",
                    "credential_ref": "secret://tenant-a/openai",
                    "default_model": "m",
            },
        )
    await publish_resource(
        sqlite_store,
        tenant_id="tenant-a",
        kind=ResourceKind.MODEL_DEFINITION,
        resource_id="model-a",
        version="1",
        spec={"name": "deepseek-chat", "provider_ref": {"id": "prov-a", "version": "1"}},
    )
    await publish_resource(
        sqlite_store,
        tenant_id="tenant-a",
        kind=ResourceKind.MODEL_DEFINITION,
        resource_id="model-b",
        version="1",
        spec={"name": "backup-chat", "provider_ref": {"id": "prov-b", "version": "2"}},
    )
    await _seed_agent(
        sqlite_store,
        agent_id="failover-agent",
        primary_model_ref={"id": "model-a", "version": "1"},
        fallback_model_refs=[{"id": "model-b", "version": "1"}],
    )

    snapshot = await _build_snapshot(sqlite_store, "failover-agent")

    assert [route.model_dump(mode="python") for route in snapshot.model_resolution.routes] == [
        {
            "provider_ref": {"id": "prov-a", "version": "1"},
            "model": "deepseek-chat",
        },
        {
            "provider_ref": {"id": "prov-b", "version": "2"},
            "model": "backup-chat",
        },
    ]
    assert snapshot.plugin_versions == {"prov-a": "1", "prov-b": "2"}


@pytest.mark.asyncio
async def test_B_S01_missing_model_definition_fails_closed(
    sqlite_store: RegistryStore,
) -> None:
    """ADR-A008 失败模式：primary ModelDefinition 缺失 → fail-closed，
    不静默回退 legacy 直引。"""
    await seed_runtime_profile(sqlite_store)
    await _seed_agent(
        sqlite_store,
        agent_id="broken-model-agent",
        primary_model_ref={"id": "missing-model", "version": "1"},
    )

    with pytest.raises(ContextResolutionError) as exc_info:
        await _build_snapshot(sqlite_store, "broken-model-agent")
    assert exc_info.value.code == "model_definition_not_found"


@pytest.mark.asyncio
async def test_B_S01_missing_fallback_model_definition_fails_closed(
    sqlite_store: RegistryStore,
) -> None:
    """回退链引用缺失同样 fail-closed（不丢版本、不静默跳过）。"""
    await seed_runtime_profile(sqlite_store)
    await publish_resource(
        sqlite_store,
        tenant_id="tenant-a",
        kind=ResourceKind.MODEL_PROVIDER,
        resource_id="prov-a",
        version="1",
        spec={
            "protocol": "openai-compatible",
            "base_url": "https://prov-a.example.com",
            "credential_ref": "secret://tenant-a/prov-a",
        },
    )
    await publish_resource(
        sqlite_store,
        tenant_id="tenant-a",
        kind=ResourceKind.MODEL_DEFINITION,
        resource_id="model-a",
        version="1",
        spec={"name": "deepseek-chat", "provider_ref": {"id": "prov-a", "version": "1"}},
    )
    await _seed_agent(
        sqlite_store,
        agent_id="broken-fallback-agent",
        primary_model_ref={"id": "model-a", "version": "1"},
        fallback_model_refs=[{"id": "missing-fallback", "version": "1"}],
    )

    with pytest.raises(ContextResolutionError) as exc_info:
        await _build_snapshot(sqlite_store, "broken-fallback-agent")
    assert exc_info.value.code == "model_definition_not_found"


@pytest.mark.asyncio
async def test_B_S01_draft_model_definition_fails_closed(
    sqlite_store: RegistryStore,
) -> None:
    """ExecutionSnapshot 只能解析已发布的 exact ModelDefinition。"""
    await seed_runtime_profile(sqlite_store)
    await sqlite_store.put(
        ResourceDefinition(
            tenant_id="tenant-a",
            kind=ResourceKind.MODEL_DEFINITION,
            id="draft-model",
            version="1",
            status=ResourceStatus.DRAFT,
            spec_json={
                "name": "draft-model",
                "provider_ref": {"id": "prov-a", "version": "1"},
            },
        )
    )
    await _seed_agent(
        sqlite_store,
        agent_id="draft-model-agent",
        primary_model_ref={"id": "draft-model", "version": "1"},
    )

    with pytest.raises(ContextResolutionError) as exc_info:
        await _build_snapshot(sqlite_store, "draft-model-agent")
    assert exc_info.value.code == "model_definition_not_published"


@pytest.mark.asyncio
async def test_B_S01_missing_provider_definition_fails_closed(
    sqlite_store: RegistryStore,
) -> None:
    """ModelDefinition 指向缺失 ProviderDefinition 时不得生成快照。"""
    await seed_runtime_profile(sqlite_store)
    await publish_resource(
        sqlite_store,
        tenant_id="tenant-a",
        kind=ResourceKind.MODEL_DEFINITION,
        resource_id="orphan-model",
        version="1",
        spec={"name": "orphan", "provider_ref": {"id": "ghost-provider", "version": "1"}},
    )
    await _seed_agent(
        sqlite_store,
        agent_id="orphan-model-agent",
        primary_model_ref={"id": "orphan-model", "version": "1"},
    )

    with pytest.raises(ContextResolutionError) as exc_info:
        await _build_snapshot(sqlite_store, "orphan-model-agent")
    assert exc_info.value.code == "model_provider_not_found"
