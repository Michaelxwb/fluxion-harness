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
    allowed_skills: list[str] | None = None,
    allowed_mcps: list[str] | None = None,
    plugin_bindings: list[str] | None = None,
    guardrail_policy: str | None = None,
) -> ResourceDefinition:
    spec: dict[str, object] = {
        "prompt": "保持严谨",
        "model_policy": {"provider": "test", "model": "deterministic"},
        "allowed_skills": allowed_skills or [],
    }
    if allowed_mcps is not None:
        spec["allowed_mcps"] = allowed_mcps
    if plugin_bindings is not None:
        spec["plugin_bindings"] = plugin_bindings
    if guardrail_policy is not None:
        spec["guardrail_policy"] = guardrail_policy
    return await publish_resource(
        store,
        tenant_id=tenant_id,
        kind=ResourceKind.RUNTIME_PROFILE,
        resource_id=runtime_profile_id,
        version=version,
        spec=spec,
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
