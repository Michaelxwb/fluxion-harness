"""TASK-002（golden-path-closure）RuntimeProfile 租户默认解析链验收测试（ADR-A010）。

B-E-01（integration）：Agent.runtime_profile_ref 未配置且无租户默认、无
platform-default → fail-closed `runtime_profile_default_missing`（区分于同名
查找失败的 runtime_profile_not_found）。
默认链（integration）：未配置 ref → 租户 default=true 的 published RuntimeProfile
→ platform-default；显式 ref 优先于默认链。

真实边界：真实 PG RegistryStore + ContextResolver 十段管线；不 mock。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest
from sqlalchemy import insert

from fluxion.agents.definitions import AgentDefinition, AgentModelPolicy
from fluxion.registry import PostgreSQLRegistryStore
from fluxion.registry.schema import resource_definitions
from fluxion.resources import ExactResourceVersion, ResourceKind
from fluxion.services.context_resolver import (
    ContextResolutionError,
    ContextResolver,
    ResolverSelector,
)
from tests.runtime_helpers import publish_resource, resource_definition, seed_model_definition, TEST_POSTGRES_DSN

TENANT = "tenant-a"


@pytest.fixture
async def store() -> AsyncGenerator[PostgreSQLRegistryStore, None]:
    store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
    await store.initialize()
    try:
        yield store
    finally:
        await store.close()


async def _seed_agent(store: PostgreSQLRegistryStore, *, agent_id: str = "agent-x") -> None:
    """无 runtime_profile_ref 的 Agent（B-E-01/默认链的被测对象）。"""
    await seed_model_definition(store, tenant_id=TENANT, provider_id="dev.echo")
    async with store.engine.begin() as conn:
        await conn.execute(
            insert(resource_definitions).values(
                tenant_id=TENANT,
                kind=ResourceKind.AGENT_DEFINITION.value,
                resource_id=agent_id,
                version="1",
                status="published",
                visibility="tenant",
                spec_json=AgentDefinition(
                    name="助手",
                    system_prompt="p",
                    owner="builder",
                    model_policy=AgentModelPolicy(
                        primary_model_ref=ExactResourceVersion(id="model.dev.echo", version="1")
                    ),
                ).model_dump(mode="json"),
                created_at=datetime.now(UTC),
            )
        )


async def _seed_profile(
    store: PostgreSQLRegistryStore,
    *,
    resource_id: str,
    default: bool = False,
    version: str = "1",
) -> None:
    await publish_resource(
        store,
        tenant_id=TENANT,
        kind=ResourceKind.RUNTIME_PROFILE,
        resource_id=resource_id,
        version=version,
        spec={
            "max_rounds": 8,
            **({"default": True} if default else {}),
        },
    )


@pytest.mark.asyncio
async def test_be01_no_default_and_no_platform_default_fail_closed(
    store: PostgreSQLRegistryStore,
) -> None:
    """B-E-01：默认链断链 → 明确错误码 runtime_profile_default_missing。"""
    await _seed_agent(store)
    resolver = ContextResolver(store)
    with pytest.raises(ContextResolutionError) as exc_info:
        await resolver.resolve(
            ResolverSelector(tenant_id=TENANT, agent_id="agent-x", user_id="user-a"),
            session_id="s-1",
            request_id="req_cccccccccccccccccccccccccccccccc",
            trace_id="trace_cccccccccccccccccccccccccccccccc",
            execution_id="exec_cccccccccccccccccccccccccccccccc",
        )
    assert exc_info.value.code == "runtime_profile_default_missing"


@pytest.mark.asyncio
async def test_tenant_default_resolves_without_same_name_profile(
    store: PostgreSQLRegistryStore,
) -> None:
    """未配置 ref → 租户 default=true 的 published profile 生效（无同名 seed）。"""
    await _seed_agent(store)
    await _seed_profile(store, resource_id="tenant-standard", default=True)
    result = await ContextResolver(store).resolve(
        ResolverSelector(tenant_id=TENANT, agent_id="agent-x", user_id="user-a"),
        session_id="s-2",
            request_id="req_ffffffffffffffffffffffffffffffff",
            trace_id="trace_ffffffffffffffffffffffffffffffff",
            execution_id="exec_ffffffffffffffffffffffffffffffff",
    )
    assert result.snapshot.runtime_profile_id == "tenant-standard"


@pytest.mark.asyncio
async def test_platform_default_fallback_when_no_tenant_default(
    store: PostgreSQLRegistryStore,
) -> None:
    """无租户默认 → platform-default 显式回退。"""
    await _seed_agent(store)
    await _seed_profile(store, resource_id="platform-default")
    result = await ContextResolver(store).resolve(
        ResolverSelector(tenant_id=TENANT, agent_id="agent-x", user_id="user-a"),
        session_id="s-3",
            request_id="req_00000000000000000000000000000000",
            trace_id="trace_00000000000000000000000000000000",
            execution_id="exec_00000000000000000000000000000000",
    )
    assert result.snapshot.runtime_profile_id == "platform-default"


@pytest.mark.asyncio
async def test_explicit_ref_wins_over_default_chain(store: PostgreSQLRegistryStore) -> None:
    """显式 runtime_profile_ref 优先于租户默认（现状语义防回归）。"""
    await _seed_agent(store)
    await _seed_profile(store, resource_id="tenant-standard", default=True)
    await _seed_profile(store, resource_id="explicit-profile")
    async with store.engine.begin() as conn:
        from sqlalchemy import update

        await conn.execute(
            update(resource_definitions)
            .where(
                resource_definitions.c.tenant_id == TENANT,
                resource_definitions.c.kind == ResourceKind.AGENT_DEFINITION.value,
                resource_definitions.c.resource_id == "agent-x",
            )
            .values(
                spec_json=AgentDefinition(
                    name="助手",
                    system_prompt="p",
                    owner="builder",
                    runtime_profile_ref=ExactResourceVersion(id="explicit-profile", version="1"),
                    model_policy=AgentModelPolicy(
                        primary_model_ref=ExactResourceVersion(id="model.dev.echo", version="1")
                    ),
                ).model_dump(mode="json")
            )
        )
    result = await ContextResolver(store).resolve(
        ResolverSelector(tenant_id=TENANT, agent_id="agent-x", user_id="user-a"),
        session_id="s-4",
            request_id="req_11111111111111111111111111111111",
            trace_id="trace_11111111111111111111111111111111",
            execution_id="exec_11111111111111111111111111111111",
    )
    assert result.snapshot.runtime_profile_id == "explicit-profile"


@pytest.mark.asyncio
async def test_unpublished_tenant_default_not_used(store: PostgreSQLRegistryStore) -> None:
    """default=true 但仅 draft → 不作为默认（published 语义）。"""
    await _seed_agent(store)
    await store.put(
        resource_definition(
            tenant_id=TENANT,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="draft-default",
            version="1",
            spec={"max_rounds": 8, "default": True},
        )
    )
    with pytest.raises(ContextResolutionError) as exc_info:
        await ContextResolver(store).resolve(
            ResolverSelector(tenant_id=TENANT, agent_id="agent-x", user_id="user-a"),
            session_id="s-5",
            request_id="req_22222222222222222222222222222222",
            trace_id="trace_22222222222222222222222222222222",
            execution_id="exec_22222222222222222222222222222222",
        )
    assert exc_info.value.code == "runtime_profile_default_missing"
