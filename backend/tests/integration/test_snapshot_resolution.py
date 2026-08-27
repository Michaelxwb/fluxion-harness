from __future__ import annotations

import pytest
from tests.runtime_helpers import (
    bind_skill_to_user,
    publish_resource,
    seed_runtime_profile,
    seed_skill,
)

from fluxion.registry import RegistryStore
from fluxion.resources import ResourceKind
from fluxion.runtime import RequestContext
from fluxion.runtime.resolver import (
    ExecutionSnapshotBuilder,
    ResourceResolver,
    ResourceVersionNotFoundError,
)


def _skill_capability(resource_id: str, version_pin: str) -> dict[str, object]:
    return {"capability_ref": resource_id, "version_pin": version_pin, "type": "skill"}


def _mcp_capability(resource_id: str, version_pin: str) -> dict[str, object]:
    return {"capability_ref": resource_id, "version_pin": version_pin, "type": "mcp"}


@pytest.mark.asyncio
async def test_E_R02_missing_dependency_version_is_rejected_without_version_swap(
    sqlite_store: RegistryStore,
) -> None:
    await seed_runtime_profile(
        sqlite_store, capabilities=[_skill_capability("search", "9")]
    )
    await seed_skill(sqlite_store, version="1")
    await bind_skill_to_user(sqlite_store, selector="9")

    builder = ExecutionSnapshotBuilder(ResourceResolver(sqlite_store))
    with pytest.raises(ResourceVersionNotFoundError) as error:
        await builder.build(
            RequestContext(
                tenant_id="tenant-a",
                user_id="user-a",
                runtime_profile_id="assistant",
                session_id="session-a",
            )
        )

    assert error.value.resource_id == "search"
    assert error.value.selector == "9"


@pytest.mark.asyncio
async def test_M3_snapshot_carries_mcp_plugin_and_policy_versions(
    sqlite_store: RegistryStore,
) -> None:
    # TASK-A104 语义迁移：profile 只承载 mechanics；MCP/Skill 版本来自 AgentDefinition
    # capabilities；plugin/policy 引用不再经 profile（failover/provider 编排在
    # Phase 2 Model policy / Policy 域回收），快照对应字段为空集合。
    await seed_runtime_profile(
        sqlite_store, capabilities=[_mcp_capability("weather", "1")]
    )
    await publish_resource(
        sqlite_store,
        tenant_id="tenant-a",
        kind=ResourceKind.MCP,
        resource_id="weather",
        version="1",
        spec={"transport": "stdio"},
    )

    snapshot = await ExecutionSnapshotBuilder(ResourceResolver(sqlite_store)).build(
        RequestContext(
            tenant_id="tenant-a",
            user_id="user-a",
            runtime_profile_id="assistant",
            session_id="session-a",
        )
    )

    assert snapshot.mcp_versions == {"weather": "1"}
    # 主 provider 版本 pin 现来自 fixture agent 的 model_ref（id=test）。
    assert snapshot.plugin_versions == {"test": "1"}
    assert snapshot.policy_version is None


@pytest.mark.asyncio
async def test_S_R18_unbound_agent_skills_survive_and_binding_grants_are_added(
    sqlite_store: RegistryStore,
) -> None:
    await seed_runtime_profile(
        sqlite_store,
        capabilities=[
            _skill_capability("search", "1"),
            _skill_capability("weather", "1"),
        ],
    )
    await seed_skill(sqlite_store, skill_id="search", version="1")
    await seed_skill(sqlite_store, skill_id="weather", version="1")
    # binding pins "search" to a version, and grants a skill absent from the agent
    await bind_skill_to_user(sqlite_store, skill_id="search", selector="1")
    await seed_skill(sqlite_store, skill_id="granted", version="2")
    await bind_skill_to_user(sqlite_store, skill_id="granted", selector="2")

    snapshot = await ExecutionSnapshotBuilder(ResourceResolver(sqlite_store)).build(
        RequestContext(
            tenant_id="tenant-a",
            user_id="user-a",
            runtime_profile_id="assistant",
            session_id="session-a",
        )
    )

    # weather@1 is unbound but stays; "granted" enters via binding
    assert snapshot.skill_versions == {"search": "1", "weather": "1", "granted": "2"}
