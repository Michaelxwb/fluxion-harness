from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest

from fluxion.registry import RegistryStore, SQLiteRegistryStore
from fluxion.resources import (
    ExactResourceVersion,
    ResourceBinding,
    ResourceDefinition,
    ResourceKind,
    ResourceStatus,
    SubjectType,
)
from fluxion.runtime.agent import AgentRuntime
from fluxion.runtime.context import RequestContext, RuntimeContext
from fluxion.runtime.memory import InMemorySessionMemoryStore
from fluxion.services.context_resolver import ContextResolver, ContextResolverSnapshotBuilder


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


async def seed_model_definition(
    store: RegistryStore,
    *,
    tenant_id: str = "tenant-a",
    model_id: str | None = None,
    model_name: str = "default",
    provider_id: str = "test",
    provider_version: str = "1",
    version: str = "1",
) -> ResourceDefinition:
    """发布 fixture ModelDefinition（ADR-A008 三层链：agent.model_policy →
    model_definition → model_provider）。幂等：同版本已存在直接复用。"""
    resolved_id = model_id or f"model.{provider_id}"
    provider = await store.get(
        ResourceKind.MODEL_PROVIDER,
        provider_id,
        tenant_id=tenant_id,
        version=provider_version,
    )
    if provider is None:
        await publish_resource(
            store,
            tenant_id=tenant_id,
            kind=ResourceKind.MODEL_PROVIDER,
            resource_id=provider_id,
            version=provider_version,
            spec={
                "protocol": "openai-compatible",
                "base_url": f"https://{provider_id}.example.invalid/v1",
                "credential_ref": f"secret://{tenant_id}/{provider_id}",
                "default_model": model_name,
            },
        )
    existing = await store.get(
        ResourceKind.MODEL_DEFINITION,
        resolved_id,
        tenant_id=tenant_id,
        version=version,
    )
    if existing is not None:
        return existing
    return await publish_resource(
        store,
        tenant_id=tenant_id,
        kind=ResourceKind.MODEL_DEFINITION,
        resource_id=resolved_id,
        version=version,
        spec={
            "name": model_name,
            "provider_ref": {"id": provider_id, "version": provider_version},
        },
    )


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
    allowed_mcps 已由 capabilities（AgentCapabilityReference dump 列表）取代。"""
    from fluxion.agents.definitions import AgentDefinition, AgentModelPolicy

    await publish_resource(
        store,
        tenant_id=tenant_id,
        kind=ResourceKind.RUNTIME_PROFILE,
        resource_id=runtime_profile_id,
        version=version,
        spec={"request_timeout_ms": 30_000, "max_retries": 1},
    )
    model = await seed_model_definition(store, tenant_id=tenant_id, provider_id="test")
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
            model_policy=AgentModelPolicy(
                primary_model_ref=ExactResourceVersion(id=model.id, version=model.version)
            ),
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
    model_name: str = "default",
    instructions: str = "",
    capabilities: list[dict[str, object]] | None = None,
) -> ResourceDefinition:
    """独立发布一个 AgentDefinition（默认与 fixture profile 同名以便回退解析）。

    ADR-A008：同时确保 provider 对应的 fixture ModelDefinition 存在，
    agent.model_policy 指向它。"""
    from fluxion.agents.definitions import AgentDefinition, AgentModelPolicy

    model = await seed_model_definition(
        store, tenant_id=tenant_id, provider_id=provider_id, model_name=model_name
    )
    # RULE-02 三维齐备：无 tenant policy 时 Tool/MCP fail-closed；fixture 与 dev
    # 自举同语义，播种默认 deny-only 策略（不设 allow-list、不 deny）。
    await seed_tenant_policy(store, tenant_id=tenant_id)
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
            model_policy=AgentModelPolicy(
                primary_model_ref=ExactResourceVersion(id=model.id, version=model.version)
            ),
            instructions=instructions,
            capabilities=list(capabilities or []),
        ).model_dump(mode="json"),
    )


async def seed_tenant_policy(
    store: RegistryStore,
    *,
    tenant_id: str = "tenant-a",
    policy_id: str = "tenant-default",
    allowed_tools: list[str] | None = None,
    denied_tools: list[str] | None = None,
) -> ResourceDefinition:
    """发布 fixture tenant Policy + tenant binding（RULE-02 三维齐备）。

    默认 deny-only（allowed/denied 均空 = 除 denied 外全部放行）；无任何
    tenant policy 时 Tool/MCP fail-closed（design/02 §3 三维真值表）。
    """
    existing = await store.get(ResourceKind.POLICY, policy_id, tenant_id=tenant_id)
    if existing is not None:
        return existing
    published = await publish_resource(
        store,
        tenant_id=tenant_id,
        kind=ResourceKind.POLICY,
        resource_id=policy_id,
        version="1",
        spec={
            "name": policy_id,
            "allowed_tools": list(allowed_tools or []),
            "denied_tools": list(denied_tools or []),
        },
    )
    bindings = await store.list_bindings(
        subject_type="tenant",
        subject_id=tenant_id,
        tenant_id=tenant_id,
        resource_type=ResourceKind.POLICY,
    )
    if not any(binding.resource_id == policy_id for binding in bindings):
        await store.put_binding(
            ResourceBinding(
                binding_id=f"binding-policy-{tenant_id}-{policy_id}",
                tenant_id=tenant_id,
                subject_type="tenant",
                subject_id=tenant_id,
                resource_type=ResourceKind.POLICY,
                resource_id=policy_id,
            )
        )
    return published


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


def minimal_tool_context(effective_permissions: dict[str, object]) -> RuntimeContext:
    """构造带指定 effective_permissions 的 RuntimeContext（ToolRuntime.call 隔离测试）。"""
    from fluxion.resources import ExecutionSnapshot, ModelPolicy

    return RuntimeContext(
        request=RequestContext(tenant_id="tenant-a", user_id="user-a", session_id="s"),
        snapshot=ExecutionSnapshot(
            execution_id="exec-1",
            tenant_id="tenant-a",
            user_id="user-a",
            runtime_profile_id="assistant",
            runtime_profile_version="1",
            model_resolution=ModelPolicy(),
            trace_id="trace-1",
            effective_permissions=effective_permissions,
        ),
    )


async def runtime_context() -> tuple[RuntimeContext, AgentRuntime]:
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    await store.initialize()
    await seed_runtime_profile(store)
    runtime = AgentRuntime(
        snapshot_builder=ContextResolverSnapshotBuilder(ContextResolver(store)),
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
