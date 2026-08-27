"""TASK-001 AgentDefinition spec model + repository 验收测试。

- BE-S-02（integration）：AgentDefinition 以引用（model_ref / runtime_profile_ref /
  capabilities）表达模型与运行态，spec_json 不内嵌 persona/model/capability；
  DRAFT→PUBLISHED 生命周期；resolve() 经真实 Store 解析引用的 RuntimeProfile。
- BE-E-02（integration）：重复 (tenant, id, version) 创建 → 领域版本冲突异常。
- RULE-fluxion-resource-001（Spec verifier）：版本化生命周期（published 版本不可变、
  版本可回溯）/ tenant 隔离 / SQLite 与 PostgreSQL 同一契约断言（Postgres 由
  FLUXION_REQUIRE_POSTGRES_CONTRACT=1 门控，模式同 tests/contract/test_registry_store.py）。
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from typing import Any

import pytest

from fluxion.agents import (
    AgentDefinition,
    AgentDefinitionRepository,
    AgentSpecValidationError,
    AgentVersionConflictError,
)
from fluxion.registry import ChannelRegistryStore, PostgreSQLRegistryStore, SQLiteRegistryStore
from fluxion.resources import ResourceDefinition, ResourceKind, ResourceStatus


# ---------------------------------------------------------------------------
# Store 工厂：契约断言参数化（SQLite 恒有；PostgreSQL 门控）
# ---------------------------------------------------------------------------


def _sqlite_factory() -> ChannelRegistryStore:
    return SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")


def _postgres_factory() -> ChannelRegistryStore:
    dsn = os.environ.get(
        "FLUXION_POSTGRES_DSN",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/fluxion_test",
    )
    return PostgreSQLRegistryStore(dsn, reset_on_initialize=True)


def _store_params() -> list[Any]:
    params: list[Any] = [pytest.param(_sqlite_factory, id="sqlite")]
    if os.environ.get("FLUXION_REQUIRE_POSTGRES_CONTRACT") == "1":
        params.append(pytest.param(_postgres_factory, id="postgres"))
    return params


@pytest.fixture(params=_store_params())
async def store(request: pytest.FixtureRequest) -> AsyncGenerator[ChannelRegistryStore, None]:
    instance = request.param()
    await instance.initialize()
    try:
        yield instance
    finally:
        await instance.close()


@pytest.fixture
def repository(store: ChannelRegistryStore) -> AgentDefinitionRepository:
    return AgentDefinitionRepository(store)


def _agent_spec(
    *,
    profile_id: str = "profile-1",
    owner: str = "builder-1",
) -> dict[str, object]:
    return {
        "name": "Support Agent",
        "description": "客服助手",
        "system_prompt": "You are a support agent.",
        "owner": owner,
        "visibility": "tenant",
        "lifecycle": "draft",
        "model_ref": {"id": "provider-1", "version": "1"},
        "runtime_profile_ref": {"id": profile_id, "version": "1"},
        "capabilities": [
            {"capability_ref": "skill-1", "version_pin": "3", "type": "skill"},
            {"capability_ref": "mcp-1", "version_pin": "2", "type": "mcp"},
        ],
        "instructions": "Answer concisely.",
    }


async def _seed_runtime_profile(
    store: ChannelRegistryStore, *, tenant_id: str, profile_id: str = "profile-1"
) -> ResourceDefinition:
    definition = ResourceDefinition(
        tenant_id=tenant_id,
        kind=ResourceKind.RUNTIME_PROFILE,
        id=profile_id,
        version="1",
        status=ResourceStatus.DRAFT,
        # TASK-A104 收缩后的合法 mechanics 形状（store 层虽不按 kind 校验，
        # fixture 不得依赖该实现细节——见 review P2）。
        spec_json={"request_timeout_ms": 30_000, "max_retries": 1},
    )
    created = await store.put(definition)
    return await store.publish(
        ResourceKind.RUNTIME_PROFILE, profile_id, tenant_id=tenant_id, version="1"
    ) or created


async def _seed_model_provider(
    store: ChannelRegistryStore, *, tenant_id: str, provider_id: str = "provider-1"
) -> None:
    await store.put(
        ResourceDefinition(
            tenant_id=tenant_id,
            kind=ResourceKind.PLUGIN,
            id=provider_id,
            version="1",
            status=ResourceStatus.DRAFT,
            spec_json={
                "plugin_type": "model_provider",
                "protocol": "openai_compatible",
                "base_url": "https://api.example.com/v1",
                "model": "deepseek-chat",
            },
        )
    )


# ---------------------------------------------------------------------------
# BE-S-02：引用而非内嵌 + 生命周期 + 引用解析
# ---------------------------------------------------------------------------


async def test_be_s_02_agent_spec_references_profile_without_persona(
    store: ChannelRegistryStore, repository: AgentDefinitionRepository
) -> None:
    await _seed_model_provider(store, tenant_id="t1")
    await _seed_runtime_profile(store, tenant_id="t1")

    created = await repository.create(
        tenant_id="t1", resource_id="agent-1", spec=_agent_spec()
    )

    # 引用而非复制：persona/model/capability 语义全部经 *_ref / capabilities 表达，
    # agent 自身 spec_json 不得内嵌 RuntimeProfile 的产品语义字段。
    forbidden_keys = {
        "prompt",
        "model_policy",
        "persona",
        "allowed_skills",
        "allowed_mcps",
        "allowed_tools",
        "plugin_bindings",
        "guardrail_policy",
    }
    assert forbidden_keys.isdisjoint(created.spec_json.keys())
    assert created.spec_json["model_ref"] == {"id": "provider-1", "version": "1"}
    assert created.spec_json["runtime_profile_ref"] == {"id": "profile-1", "version": "1"}
    assert created.status is ResourceStatus.DRAFT
    assert created.kind is ResourceKind.AGENT_DEFINITION

    published = await repository.publish(
        tenant_id="t1", resource_id="agent-1", version="1"
    )
    assert published.status is ResourceStatus.PUBLISHED

    # 解析合并：agent 持引用、profile 持 mechanics，经真实 Store 各自取回。
    agent, profile = await repository.resolve(tenant_id="t1", resource_id="agent-1")
    assert agent.id == "agent-1"
    assert profile is not None
    assert profile.id == "profile-1"
    assert profile.tenant_id == "t1"


async def test_be_s_02_resolve_without_profile_ref_returns_agent_only(
    store: ChannelRegistryStore, repository: AgentDefinitionRepository
) -> None:
    await _seed_model_provider(store, tenant_id="t1")
    spec = _agent_spec()
    spec["runtime_profile_ref"] = None
    await repository.create(tenant_id="t1", resource_id="agent-bare", spec=spec)

    agent, profile = await repository.resolve(tenant_id="t1", resource_id="agent-bare")
    assert agent.id == "agent-bare"
    assert profile is None


# ---------------------------------------------------------------------------
# BE-E-02：版本冲突领域异常
# ---------------------------------------------------------------------------


async def test_be_e_02_duplicate_version_conflict(
    store: ChannelRegistryStore, repository: AgentDefinitionRepository
) -> None:
    await _seed_model_provider(store, tenant_id="t1")
    await repository.create(tenant_id="t1", resource_id="agent-1", spec=_agent_spec())

    with pytest.raises(AgentVersionConflictError):
        await repository.create(
            tenant_id="t1", resource_id="agent-1", spec=_agent_spec(owner="builder-2")
        )


async def test_invalid_spec_rejected_by_typed_model(
    repository: AgentDefinitionRepository,
) -> None:
    spec = _agent_spec()
    del spec["model_ref"]
    with pytest.raises(AgentSpecValidationError):
        await repository.create(tenant_id="t1", resource_id="agent-bad", spec=spec)


# ---------------------------------------------------------------------------
# RULE-fluxion-resource-001（Spec verifier）：版本化 + tenant 隔离
# ---------------------------------------------------------------------------


async def test_rule_resource_001_versioned_lifecycle(
    store: ChannelRegistryStore, repository: AgentDefinitionRepository
) -> None:
    await _seed_model_provider(store, tenant_id="t1")
    spec_v1 = _agent_spec()
    spec_v1["name"] = "Agent v1"
    await repository.create(tenant_id="t1", resource_id="agent-v", spec=spec_v1, version="1")
    await repository.publish(tenant_id="t1", resource_id="agent-v", version="1")

    spec_v2 = _agent_spec()
    spec_v2["name"] = "Agent v2"
    await repository.create(tenant_id="t1", resource_id="agent-v", spec=spec_v2, version="2")
    await repository.publish(tenant_id="t1", resource_id="agent-v", version="2")

    versions, total = await repository.list_versions(tenant_id="t1", resource_id="agent-v")
    assert total == 2
    assert {item.version for item in versions} == {"1", "2"}

    v1 = await repository.get(tenant_id="t1", resource_id="agent-v", version="1")
    assert v1.spec_json["name"] == "Agent v1"
    assert v1.status is ResourceStatus.PUBLISHED


async def test_rule_resource_001_tenant_isolation(
    store: ChannelRegistryStore, repository: AgentDefinitionRepository
) -> None:
    await _seed_model_provider(store, tenant_id="t1")
    await _seed_model_provider(store, tenant_id="t2")

    await repository.create(
        tenant_id="t1", resource_id="agent-shared", spec=_agent_spec(owner="owner-t1")
    )
    # 同 resource_id 在另一租户可独立创建，不构成冲突。
    await repository.create(
        tenant_id="t2", resource_id="agent-shared", spec=_agent_spec(owner="owner-t2")
    )

    t1 = await repository.get(tenant_id="t1", resource_id="agent-shared")
    t2 = await repository.get(tenant_id="t2", resource_id="agent-shared")
    assert t1.spec_json["owner"] == "owner-t1"
    assert t2.spec_json["owner"] == "owner-t2"
    assert t1.tenant_id == "t1" and t2.tenant_id == "t2"


def test_agent_definition_model_fields_match_prd_4_2() -> None:
    spec = AgentDefinition.model_validate(_agent_spec())
    assert spec.name == "Support Agent"
    assert spec.owner == "builder-1"
    assert spec.visibility.value == "tenant"
    assert spec.lifecycle is ResourceStatus.DRAFT
    assert spec.model_ref.id == "provider-1"
    assert spec.capabilities[0].type.value == "skill"
    assert spec.capabilities[1].type.value == "mcp"
    assert spec.memory_policy_ref is None
    assert spec.personalization_policy_ref is None
    assert spec.workflow_ref is None
