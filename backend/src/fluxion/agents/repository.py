"""AgentDefinition 存取边界（TASK-A102）。

只负责存储契约：typed spec 校验（ADR-011 SoT）、put/get/list_versions/publish、
引用解析（resolve）。治理（audit / publish_record / outbox，A8 模式）由 service
层组合既有 ConsoleResourceOps 落地（TASK-004 Product API 接线），本层不绕过
也不重复实现。
"""

from __future__ import annotations

from pydantic import ValidationError

from fluxion.agents.definitions import AgentDefinition
from fluxion.registry import ChannelRegistryStore, VersionConflictError
from fluxion.resources import ResourceDefinition, ResourceKind, ResourceStatus


class AgentDomainError(RuntimeError):
    """Agent Domain 领域异常基类（API 层映射错误码，不静默吞）。"""


class AgentSpecValidationError(AgentDomainError):
    """spec 不满足 AgentDefinition typed model（API 层映射 422）。"""


class AgentVersionConflictError(AgentDomainError):
    """(tenant, resource_id, version) 已存在（API 层映射 409）。"""


class AgentDefinitionNotFoundError(AgentDomainError):
    """AgentDefinition 不存在（API 层映射 404）。"""


class AgentDefinitionRepository:
    """AGENT_DEFINITION 资源的 Registry 存取（复用 resource_definitions，无新表）。"""

    def __init__(self, store: ChannelRegistryStore) -> None:
        self._store = store

    async def create(
        self,
        *,
        tenant_id: str,
        resource_id: str,
        spec: dict[str, object],
        version: str = "1",
    ) -> ResourceDefinition:
        try:
            AgentDefinition.model_validate(spec)
        except ValidationError as exc:
            raise AgentSpecValidationError(str(exc)) from exc
        definition = ResourceDefinition(
            kind=ResourceKind.AGENT_DEFINITION,
            id=resource_id,
            tenant_id=tenant_id,
            version=version,
            status=ResourceStatus.DRAFT,
            spec_json=dict(spec),
        )
        try:
            return await self._store.put(definition)
        except VersionConflictError as exc:
            raise AgentVersionConflictError(
                f"agent definition {resource_id} version {version} already exists"
            ) from exc

    async def get(
        self,
        *,
        tenant_id: str,
        resource_id: str,
        version: str | None = None,
    ) -> ResourceDefinition:
        definition = await self._store.get(
            ResourceKind.AGENT_DEFINITION,
            resource_id,
            tenant_id=tenant_id,
            version=version,
        )
        if definition is None and version is None:
            # store 契约：version=None 只取 latest PUBLISHED。Agent Studio 需要
            # 读取尚无发布版本的 DRAFT，这里回退到最新版本（任意状态，最新在前）。
            versions, total = await self._store.list_versions(
                ResourceKind.AGENT_DEFINITION,
                resource_id,
                tenant_id=tenant_id,
                offset=0,
                limit=1,
            )
            if total > 0:
                definition = versions[0]
        if definition is None:
            raise AgentDefinitionNotFoundError(
                f"agent definition {resource_id} not found in tenant {tenant_id}"
            )
        return definition

    async def list_versions(
        self,
        *,
        tenant_id: str,
        resource_id: str,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[ResourceDefinition], int]:
        return await self._store.list_versions(
            ResourceKind.AGENT_DEFINITION,
            resource_id,
            tenant_id=tenant_id,
            offset=offset,
            limit=limit,
        )

    async def publish(
        self,
        *,
        tenant_id: str,
        resource_id: str,
        version: str,
    ) -> ResourceDefinition:
        existing = await self.get(tenant_id=tenant_id, resource_id=resource_id, version=version)
        # S_P13_05 同款约束：invalid spec 不得发布（发布前再过一次 typed model）。
        try:
            AgentDefinition.model_validate(existing.spec_json)
        except ValidationError as exc:
            raise AgentSpecValidationError(str(exc)) from exc
        if existing.status is ResourceStatus.PUBLISHED:
            raise AgentVersionConflictError(
                f"agent definition {resource_id} version {version} already published"
            )
        try:
            return await self._store.publish(
                ResourceKind.AGENT_DEFINITION,
                resource_id,
                tenant_id=tenant_id,
                version=version,
            )
        except VersionConflictError as exc:
            raise AgentVersionConflictError(str(exc)) from exc

    async def resolve(
        self,
        *,
        tenant_id: str,
        resource_id: str,
        version: str | None = None,
    ) -> tuple[ResourceDefinition, ResourceDefinition | None]:
        """解析 AgentDefinition 及其引用的 RuntimeProfile（引用合并的读取侧）。

        agent 持引用、profile 持 runtime mechanics，各自从真实 Store 取回；
        runtime_profile_ref 留空时 profile 为 None（租户默认在 service 层
        TASK-004/008 形式化）。
        """
        agent = await self.get(tenant_id=tenant_id, resource_id=resource_id, version=version)
        spec = AgentDefinition.model_validate(agent.spec_json)
        profile: ResourceDefinition | None = None
        if spec.runtime_profile_ref is not None:
            profile = await self._store.get(
                ResourceKind.RUNTIME_PROFILE,
                spec.runtime_profile_ref.id,
                tenant_id=tenant_id,
                version=spec.runtime_profile_ref.version,
            )
        return agent, profile
