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
    """
    agent = await store.get(
        ResourceKind.AGENT_DEFINITION,
        request.runtime_profile_id,
        tenant_id=request.tenant_id,
    )
    if agent is not None:
        return
    from fluxion.agents.definitions import AgentDefinition

    spec = AgentDefinition(
        name=request.runtime_profile_id,
        description="由 runtime 自举生成的默认 Agent",
        system_prompt="保持严谨",
        owner="system:bootstrap",
        model_ref={"id": "dev.echo", "version": "1"},
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
