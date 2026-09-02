"""RuntimeProfileService（TASK-009）：RuntimeProfile 的版本化创建/发布/确保。

从 RuntimeApplicationService 拆出 profile CRUD，使编排服务只负责 execution；
Console 操作 AgentDefinition、Runtime 经自举路径确保同名默认 profile/agent。
"""

from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from fluxion.registry import (
    PublicationCommand,
    PublicationOperation,
    RegistryStore,
)
from fluxion.resources import (
    ExactResourceVersion,
    ResourceBinding,
    ResourceDefinition,
    ResourceKind,
    ResourceStatus,
    ResourceVisibility,
)
from fluxion.runtime.hot_reload import ConfigChangeEvent
from fluxion.services.runtime_contracts import (
    CreateRuntimeProfileRequest,
    PublishRuntimeProfileRequest,
)
from fluxion.services.runtime_utils import _runtime_profile_spec


class RuntimeProfileService:
    """RuntimeProfile 版本化资源管理（TASK-009：ProfileService 拆分）。"""

    def __init__(
        self,
        store: RegistryStore,
        *,
        on_config_changed: Callable[[ConfigChangeEvent], None] | None = None,
    ) -> None:
        self._store = store
        self._on_config_changed = on_config_changed

    async def create_runtime_profile(
        self, request: CreateRuntimeProfileRequest
    ) -> ResourceDefinition:
        definition = ResourceDefinition(
            kind=ResourceKind.RUNTIME_PROFILE,
            id=request.runtime_profile_id,
            tenant_id=request.tenant_id,
            version=request.version,
            status=ResourceStatus.DRAFT,
            spec_json=_runtime_profile_spec(request),
        )
        return await self._store.put(definition)

    async def publish_runtime_profile(
        self, request: PublishRuntimeProfileRequest
    ) -> ResourceDefinition:
        # A8/契约§7：走治理事务（commit_publication）——审计 + publish_record +
        # outbox + bump_revision 原子化，与 Console 一致。系统发起（run --bootstrap
        # / SDK ensure），actor 归属 system:bootstrap。
        commit = await self._store.commit_publication(
            PublicationCommand(
                publish_id=f"pub_{uuid4().hex}",
                event_id=f"evt_{uuid4().hex}",
                tenant_id=request.tenant_id,
                kind=ResourceKind.RUNTIME_PROFILE,
                resource_id=request.runtime_profile_id,
                version=request.version,
                operation=PublicationOperation.PUBLISH,
                actor_id="system:bootstrap",
                request_id=f"bootstrap_{uuid4().hex}",
                trace_id="bootstrap",
            )
        )
        event = ConfigChangeEvent(
            tenant_id=request.tenant_id,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id=request.runtime_profile_id,
            version=request.version,
            revision=commit.revision,
        )
        if request.notify_runtime and self._on_config_changed is not None:
            self._on_config_changed(event)
        return commit.resource

    async def ensure_runtime_profile(
        self, request: CreateRuntimeProfileRequest
    ) -> ResourceDefinition:
        existing = await self._store.get(
            ResourceKind.RUNTIME_PROFILE,
            request.runtime_profile_id,
            tenant_id=request.tenant_id,
            version=request.version,
        )
        if existing is None:
            existing = await self.create_runtime_profile(request)
        # TASK-A104：自举路径同步确保同名默认 AgentDefinition（persona/model 的
        # SoT），使 `run --bootstrap` / dev bundle 开箱可跑；已存在则不覆盖。
        await _ensure_default_agent(self._store, request)
        # RULE-02（TASK-003 返工）：无 tenant policy 时 Tool/MCP fail-closed；
        # 自举播种默认 deny-only 策略（不设 allow-list、不 deny）保住 dev 开箱
        # 可用——生产租户按需显式配置自己的 Policy。
        await _ensure_default_tenant_policy(self._store, request.tenant_id)
        if existing.status is ResourceStatus.PUBLISHED:
            return existing
        return await self.publish_runtime_profile(
            PublishRuntimeProfileRequest(
                tenant_id=request.tenant_id,
                runtime_profile_id=request.runtime_profile_id,
                version=request.version,
            )
        )


async def _ensure_default_agent(
    store: RegistryStore, request: CreateRuntimeProfileRequest
) -> None:
    """为自举的 RuntimeProfile 确保同名默认 AgentDefinition（TASK-A104）。

    persona/model 的 SoT 在 AgentDefinition；dev bundle / `run --bootstrap`
    需要开箱可跑的默认 Agent（provider=dev.echo）。已存在任何版本即不动。
    ADR-A008 三层链：先确保默认 ModelDefinition（model.dev-echo），agent
    model_policy 指向它。
    """
    agent = await store.get(
        ResourceKind.AGENT_DEFINITION,
        request.runtime_profile_id,
        tenant_id=request.tenant_id,
    )
    if agent is not None:
        return
    from fluxion.agents.definitions import AgentDefinition, AgentModelPolicy
    from fluxion.resources.contracts import ModelDefinition, ProviderDefinition

    model_id = "model.dev-echo"
    provider_id = "dev.echo"
    provider = await store.get(
        ResourceKind.MODEL_PROVIDER,
        provider_id,
        tenant_id=request.tenant_id,
        version="1",
    )
    if provider is None:
        provider_spec = ProviderDefinition(
            protocol="openai-compatible",
            base_url="https://dev-echo.invalid/v1",
            credential_ref=f"secret://{request.tenant_id}/dev-echo",
            default_model="echo",
        )
        provider_draft = ResourceDefinition(
            kind=ResourceKind.MODEL_PROVIDER,
            id=provider_id,
            tenant_id=request.tenant_id,
            version="1",
            status=ResourceStatus.DRAFT,
            visibility=ResourceVisibility.PRIVATE,
            spec_json=provider_spec.model_dump(mode="json"),
        )
        await store.put(provider_draft)
        await store.publish(
            ResourceKind.MODEL_PROVIDER,
            provider_id,
            tenant_id=request.tenant_id,
            version="1",
        )
    model_spec = ModelDefinition(
        name="echo",
        provider_ref=ExactResourceVersion(id=provider_id, version="1"),
    )
    model = await store.get(ResourceKind.MODEL_DEFINITION, model_id, tenant_id=request.tenant_id)
    if model is None:
        model_draft = ResourceDefinition(
            kind=ResourceKind.MODEL_DEFINITION,
            id=model_id,
            tenant_id=request.tenant_id,
            version=request.version,
            status=ResourceStatus.DRAFT,
            visibility=ResourceVisibility.PRIVATE,
            spec_json=model_spec.model_dump(mode="json"),
        )
        existing_model = await store.put(model_draft)
        await store.publish(
            ResourceKind.MODEL_DEFINITION,
            model_id,
            tenant_id=request.tenant_id,
            version=existing_model.version,
        )
        model_version = existing_model.version
    else:
        model_version = model.version
    spec = AgentDefinition(
        name=request.runtime_profile_id,
        description="由 runtime 自举生成的默认 Agent",
        system_prompt="保持严谨",
        owner="system:bootstrap",
        model_policy=AgentModelPolicy(
            primary_model_ref=ExactResourceVersion(id=model_id, version=model_version)
        ),
    )
    draft = ResourceDefinition(
        kind=ResourceKind.AGENT_DEFINITION,
        id=request.runtime_profile_id,
        tenant_id=request.tenant_id,
        version=request.version,
        status=ResourceStatus.DRAFT,
        visibility=ResourceVisibility.PRIVATE,
        spec_json=spec.model_dump(mode="json"),
    )
    existing_draft = await store.put(draft)
    await store.publish(
        ResourceKind.AGENT_DEFINITION,
        request.runtime_profile_id,
        tenant_id=request.tenant_id,
        version=existing_draft.version,
    )


_DEFAULT_POLICY_ID = "tenant-default"


async def _ensure_default_tenant_policy(store: RegistryStore, tenant_id: str) -> None:
    """确保租户存在默认 deny-only Policy + tenant binding（幂等）。

    RULE-02 三维 fail-closed 后，无任何 tenant policy 的租户 Tool/MCP 全部
    不可用；dev 自举播种「不设 allow-list、不 deny」的默认策略，使
    grant + agent 声明即用。生产租户可发布自己的 Policy 覆盖默认行为。
    """
    policy = await store.get(ResourceKind.POLICY, _DEFAULT_POLICY_ID, tenant_id=tenant_id)
    if policy is None:
        draft = ResourceDefinition(
            kind=ResourceKind.POLICY,
            id=_DEFAULT_POLICY_ID,
            tenant_id=tenant_id,
            version="1",
            status=ResourceStatus.DRAFT,
            visibility=ResourceVisibility.TENANT,
            spec_json={"name": "tenant-default", "allowed_tools": [], "denied_tools": []},
        )
        existing = await store.put(draft)
        await store.publish(
            ResourceKind.POLICY,
            _DEFAULT_POLICY_ID,
            tenant_id=tenant_id,
            version=existing.version,
        )
    bindings = await store.list_bindings(
        subject_type="tenant",
        subject_id=tenant_id,
        tenant_id=tenant_id,
        resource_type=ResourceKind.POLICY,
    )
    if not any(binding.resource_id == _DEFAULT_POLICY_ID for binding in bindings):
        await store.put_binding(
            ResourceBinding(
                binding_id=f"binding-policy-{tenant_id}-default",
                tenant_id=tenant_id,
                subject_type="tenant",
                subject_id=tenant_id,
                resource_type=ResourceKind.POLICY,
                resource_id=_DEFAULT_POLICY_ID,
                resource_version_selector="latest-published",
            )
        )
