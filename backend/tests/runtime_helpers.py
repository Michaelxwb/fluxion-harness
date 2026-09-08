from __future__ import annotations

import os
from collections.abc import AsyncGenerator

import pytest

from fluxion.registry import PostgreSQLRegistryStore, RegistryStore
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


TEST_POSTGRES_DSN = os.environ.get(
    "FLUXION_POSTGRES_DSN",
    "postgresql+asyncpg://mmuser:mmuser@localhost:5432/fluxion_test",
)
"""测试 PG DSN（ADR-A007）：`FLUXION_POSTGRES_DSN` 可覆盖，默认本地 mmuser 库。"""


@pytest.fixture
async def pg_store() -> AsyncGenerator[RegistryStore, None]:
    """PG 测试夹具（ADR-A007）：连本地 `fluxion_test`，每次重建隔离。

    前置：本地 PG 常驻（`FLUXION_POSTGRES_DSN` 可覆盖，默认 mmuser 本地库）。
    """
    store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
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
    """TASK-A104 后的 seeding：profile 只含 mechanics；产品语义落在
    AgentDefinition。ADR-A010 后同名回退废弃——profile 标记为租户默认
    （default=true），无 ref 的 agent 经默认链解析。旧签名的 allowed_skills/
    allowed_mcps 已由 capabilities（AgentCapabilityReference dump 列表）取代。"""
    from fluxion.agents.definitions import AgentDefinition, AgentModelPolicy

    await publish_resource(
        store,
        tenant_id=tenant_id,
        kind=ResourceKind.RUNTIME_PROFILE,
        resource_id=runtime_profile_id,
        version=version,
        spec={"max_rounds": 8, "default": True},
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
    """独立发布一个 AgentDefinition（ADR-A010 后不再依赖同名 profile 回退；
    调用方需确保租户存在 default=true 的 RuntimeProfile 或 platform-default）。

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
    store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
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
            agent_definition_id="assistant",
            session_id="session-a",
        )
    )
    return context, runtime


async def hook_test_service(bus=None):  # type: ignore[no-untyped-def]
    """真实 service（dev bundle＋PG）：profile/agent/授权齐备，可执行 time.now。

    由 test_hooks._hook_test_service 提升为共享 harness（hook-plugin-remediation
    TASK-002 起多任务复用）。bus 非空时用它作为服务事件总线；entry_points 插件
    安装走 initialize 内 discover→load 真实路径（调用方先 monkeypatch
    discover_hook_plugins）。调用方负责 `await service.close()`。
    """
    from fluxion.kernel.events import TypedEventBus
    from fluxion.services.runtime_app import (
        CreateRuntimeProfileRequest,
        PublishRuntimeProfileRequest,
        RuntimeApplicationService,
    )

    store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
    service = RuntimeApplicationService.create_dev_bundle(
        store, event_bus=bus or TypedEventBus()
    )
    await service.initialize()
    try:
        await service.create_runtime_profile(
            CreateRuntimeProfileRequest(
                tenant_id="tenant-a",
                runtime_profile_id="assistant",
                version="1",
                default=True,
            )
        )
        await seed_agent_definition(
            store,
            provider_id="dev.echo",
            capabilities=[{"capability_ref": "time.now", "version_pin": "1", "type": "tool"}],
        )
        await store.add_capability_grant(
            tenant_id="tenant-a",
            platform_user_id="user-a",
            capability_ref="time.now",
            capability_kind="tool",
            granted_scope="invoke",
            version_pin="1",
        )
        await service.publish_runtime_profile(
            PublishRuntimeProfileRequest(
                tenant_id="tenant-a",
                runtime_profile_id="assistant",
                version="1",
            )
        )
        return service, store
    except BaseException:
        await service.close()
        raise


def hook_run_request(**overrides: object):  # type: ignore[no-untyped-def]
    """hook 测试默认 RunRuntimeRequest（tenant-a/user-a/assistant/time.now）。"""
    from fluxion.services.runtime_app import RunRuntimeRequest, ToolCallRequest

    params: dict[str, object] = {
        "tenant_id": "tenant-a",
        "user_id": "user-a",
        "agent_definition_id": "assistant",
        "runtime_profile_id": "assistant",
        "session_id": "session-hook",
        "input_message": "hook",
        "tool_calls": [ToolCallRequest(tool_id="time.now", arguments={})],
    }
    params.update(overrides)
    return RunRuntimeRequest(**params)  # type: ignore[arg-type]
