from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest

from fluxion.registry import RegistryStore, SQLiteRegistryStore
from fluxion.resources import (
    ResourceBinding,
    ResourceDefinition,
    ResourceKind,
    ResourceStatus,
    SubjectType,
)
from fluxion.runtime.agent import AgentRuntime
from fluxion.runtime.context import RequestContext, RuntimeContext
from fluxion.runtime.memory import InMemorySessionMemoryStore
from fluxion.runtime.resolver import ExecutionSnapshotBuilder, ResourceResolver


@pytest.fixture
async def sqlite_store() -> AsyncGenerator[RegistryStore, None]:
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    await store.initialize()
    try:
        yield store
    finally:
        await store.close()


def resource_definition(
    *,
    tenant_id: str,
    kind: ResourceKind,
    resource_id: str,
    version: str,
    spec: dict[str, object],
) -> ResourceDefinition:
    return ResourceDefinition(
        tenant_id=tenant_id,
        kind=kind,
        id=resource_id,
        version=version,
        status=ResourceStatus.DRAFT,
        spec_json=spec,
    )


async def publish_resource(
    store: RegistryStore,
    *,
    tenant_id: str,
    kind: ResourceKind,
    resource_id: str,
    version: str,
    spec: dict[str, object],
) -> ResourceDefinition:
    await store.put(
        resource_definition(
            tenant_id=tenant_id,
            kind=kind,
            resource_id=resource_id,
            version=version,
            spec=spec,
        )
    )
    return await store.publish(kind, resource_id, tenant_id=tenant_id, version=version)


async def seed_runtime_profile(
    store: RegistryStore,
    *,
    tenant_id: str = "tenant-a",
    runtime_profile_id: str = "assistant",
    version: str = "1",
    system_prompt: str = "保持严谨",
    agent_version: str | None = None,
    capabilities: list[dict[str, object]] | None = None,
) -> ResourceDefinition:
    """TASK-A104 后的 seeding：profile 只含 mechanics；产品语义落在同名
    AgentDefinition（resolver 缺省回退按同名解析）。旧签名的 allowed_skills/
    allowed_mcps 已由 capabilities（CapabilityBinding dump 列表）取代。"""
    from fluxion.agents.definitions import AgentDefinition

    await publish_resource(
        store,
        tenant_id=tenant_id,
        kind=ResourceKind.RUNTIME_PROFILE,
        resource_id=runtime_profile_id,
        version=version,
        spec={"request_timeout_ms": 30_000, "max_retries": 1},
    )
    return await publish_resource(
        store,
        tenant_id=tenant_id,
        kind=ResourceKind.AGENT_DEFINITION,
        resource_id=runtime_profile_id,
        version=agent_version or version,
        spec=AgentDefinition(
            name=runtime_profile_id,
            description="fixture agent",
            system_prompt=system_prompt,
            owner="fixture",
            model_ref={"id": "test", "version": "1"},
            capabilities=list(capabilities or []),
        ).model_dump(mode="json"),
    )


async def seed_agent_definition(
    store: RegistryStore,
    *,
    tenant_id: str = "tenant-a",
    agent_id: str = "assistant",
    version: str = "1",
    system_prompt: str = "保持严谨",
    owner: str = "fixture",
    provider_id: str = "test",
    instructions: str = "",
    capabilities: list[dict[str, object]] | None = None,
) -> ResourceDefinition:
    """独立发布一个 AgentDefinition（默认与 fixture profile 同名以便回退解析）。"""
    from fluxion.agents.definitions import AgentDefinition

    # 幂等：重复 seeding（多轮 benchmark / 并发 fixture）直接复用现有发布版。
    existing = await store.get(
        ResourceKind.AGENT_DEFINITION,
        agent_id,
        tenant_id=tenant_id,
        version=version,
    )
    if existing is not None:
        return existing
    return await publish_resource(
        store,
        tenant_id=tenant_id,
        kind=ResourceKind.AGENT_DEFINITION,
        resource_id=agent_id,
        version=version,
        spec=AgentDefinition(
            name=agent_id,
            description="fixture agent",
            system_prompt=system_prompt,
            owner=owner,
            model_ref={"id": provider_id, "version": "1"},
            instructions=instructions,
            capabilities=list(capabilities or []),
        ).model_dump(mode="json"),
    )


async def seed_skill(
    store: RegistryStore,
    *,
    tenant_id: str = "tenant-a",
    skill_id: str = "search",
    version: str = "1",
    capability_id: str = "cap.search",
) -> ResourceDefinition:
    return await publish_resource(
        store,
        tenant_id=tenant_id,
        kind=ResourceKind.SKILL,
        resource_id=skill_id,
        version=version,
        spec={
            "name": skill_id,
            "description": "fixture skill",
            "capability_id": capability_id,
            "parameters": {},
        },
    )


async def bind_skill_to_user(
    store: RegistryStore,
    *,
    tenant_id: str = "tenant-a",
    user_id: str = "user-a",
    skill_id: str = "search",
    selector: str = "latest-published",
) -> ResourceBinding:
    binding = ResourceBinding(
        binding_id=f"binding-{tenant_id}-{user_id}-{skill_id}",
        tenant_id=tenant_id,
        subject_type=SubjectType.USER,
        subject_id=user_id,
        resource_type=ResourceKind.SKILL,
        resource_id=skill_id,
        resource_version_selector=selector,
        config_json={"enabled": True},
        enabled=True,
    )
    return await store.put_binding(binding)


async def runtime_context() -> tuple[RuntimeContext, AgentRuntime]:
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    await store.initialize()
    await seed_runtime_profile(store)
    resolver = ResourceResolver(store)
    runtime = AgentRuntime(
        snapshot_builder=ExecutionSnapshotBuilder(resolver),
        memory_store=InMemorySessionMemoryStore(),
    )
    context = await runtime.start_execution(
        RequestContext(
            tenant_id="tenant-a",
            user_id="user-a",
            runtime_profile_id="assistant",
            session_id="session-a",
        )
    )
    return context, runtime
