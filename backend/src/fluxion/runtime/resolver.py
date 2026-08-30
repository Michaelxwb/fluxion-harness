from __future__ import annotations

from dataclasses import dataclass

from fluxion.agents.definitions import AgentDefinition, CapabilityType
from fluxion.registry import RegistryReadStore, RegistryStoreError
from fluxion.resources import (
    ExecutionSnapshot,
    ModelPolicy,
    ResourceBinding,
    ResourceDefinition,
    ResourceKind,
    ResourceStatus,
    RuntimeProfile,
    SkillDefinition,
    SubjectType,
    TenantResourceCache,
)
from fluxion.runtime.context import RequestContext

LATEST_PUBLISHED = "latest-published"
# 与 ModelPolicy.deadline_ms 默认一致（类属性在 pydantic v2 上不可直接读）。
_DEFAULT_DEADLINE_MS = 120_000


class RuntimeKernelError(RuntimeError):
    code = "runtime_kernel_error"


class ResourceCacheMissError(RuntimeKernelError):
    code = "resource_cache_miss"


class RegistryUnavailableError(RuntimeKernelError):
    code = "registry_unavailable"


class ResourceVersionNotFoundError(RuntimeKernelError):
    code = "resource_version_not_found"

    def __init__(
        self,
        *,
        tenant_id: str,
        kind: ResourceKind,
        resource_id: str,
        selector: str,
    ) -> None:
        self.tenant_id = tenant_id
        self.kind = kind
        self.resource_id = resource_id
        self.selector = selector
        super().__init__(f"{tenant_id}/{kind.value}/{resource_id}@{selector} not found")


@dataclass(frozen=True, slots=True)
class ResolverPolicy:
    allow_stale_non_sensitive: bool = False


@dataclass(frozen=True, slots=True)
class ResourceSelector:
    resource_id: str
    selector: str = LATEST_PUBLISHED


class ResourceResolver:
    def __init__(
        self,
        store: RegistryReadStore | None = None,
        *,
        cache: TenantResourceCache | None = None,
        policy: ResolverPolicy | None = None,
    ) -> None:
        self._store = store
        self._cache = cache or TenantResourceCache()
        self._policy = policy or ResolverPolicy()
        self._degraded_read_count = 0

    @classmethod
    def from_cache(cls, cache: TenantResourceCache | None = None) -> ResourceResolver:
        return cls(None, cache=cache)

    @property
    def degraded_read_count(self) -> int:
        return self._degraded_read_count

    def resolve_from_l1(
        self,
        *,
        tenant_id: str,
        kind: ResourceKind,
        resource_id: str,
        selector: str,
    ) -> ResourceDefinition:
        cached = self._cache.get(tenant_id, kind, resource_id, selector)
        if cached is None:
            raise ResourceCacheMissError(f"{tenant_id}/{kind.value}/{resource_id}@{selector}")
        return cached

    async def resolve_resource(
        self,
        tenant_id: str,
        kind: ResourceKind,
        resource_id: str,
        *,
        selector: str = LATEST_PUBLISHED,
    ) -> ResourceDefinition:
        if selector != LATEST_PUBLISHED:
            cached = self._cache.get(tenant_id, kind, resource_id, selector)
            if cached is not None:
                return cached
        return await self._resolve_from_store(tenant_id, kind, resource_id, selector)

    async def list_bindings(
        self,
        *,
        tenant_id: str,
        subject_type: SubjectType,
        subject_id: str,
        resource_type: ResourceKind | None = None,
    ) -> list[ResourceBinding]:
        if self._store is None:
            return []
        try:
            return await self._store.list_bindings(
                tenant_id=tenant_id,
                subject_type=subject_type.value,
                subject_id=subject_id,
                resource_type=resource_type,
            )
        except RegistryStoreError as exc:
            raise RegistryUnavailableError("registry unavailable while reading bindings") from exc

    async def _resolve_from_store(
        self,
        tenant_id: str,
        kind: ResourceKind,
        resource_id: str,
        selector: str,
    ) -> ResourceDefinition:
        if self._store is None:
            return self._stale_or_missing(tenant_id, kind, resource_id, selector)
        try:
            version = None if selector == LATEST_PUBLISHED else selector
            resource = await self._store.get(kind, resource_id, tenant_id=tenant_id, version=version)
        except RegistryStoreError as exc:
            return self._resolve_after_store_error(tenant_id, kind, resource_id, selector, exc)
        if resource is None or resource.status != ResourceStatus.PUBLISHED:
            raise ResourceVersionNotFoundError(
                tenant_id=tenant_id,
                kind=kind,
                resource_id=resource_id,
                selector=selector,
            )
        self._cache.set(resource)
        self._cache.set(resource, version_alias=selector)
        return resource

    def _resolve_after_store_error(
        self,
        tenant_id: str,
        kind: ResourceKind,
        resource_id: str,
        selector: str,
        exc: RegistryStoreError,
    ) -> ResourceDefinition:
        if self._policy.allow_stale_non_sensitive:
            cached = self._cache.get(tenant_id, kind, resource_id, selector)
            if cached is not None and _is_non_sensitive(cached):
                self._degraded_read_count += 1
                return cached
        raise RegistryUnavailableError("registry unavailable") from exc

    def _stale_or_missing(
        self,
        tenant_id: str,
        kind: ResourceKind,
        resource_id: str,
        selector: str,
    ) -> ResourceDefinition:
        cached = self._cache.get(tenant_id, kind, resource_id, selector)
        if cached is None:
            raise ResourceCacheMissError(f"{tenant_id}/{kind.value}/{resource_id}@{selector}")
        return cached


def _capability_selectors(
    agent: ResourceDefinition | None,
) -> dict[CapabilityType, list[ResourceSelector]]:
    """把 AgentDefinition.capabilities 按类型拆为解析用 selector 列表。"""
    grouped: dict[CapabilityType, list[ResourceSelector]] = {}
    if agent is None:
        return grouped
    spec = AgentDefinition.model_validate(agent.spec_json)
    for capability_type in CapabilityType:
        selectors = [
            _parse_selector(f"{binding.capability_ref}@{binding.version_pin}")
            for binding in spec.capabilities
            if binding.type is capability_type
        ]
        if selectors:
            grouped[capability_type] = selectors
    return grouped


class ExecutionSnapshotBuilder:
    def __init__(self, resolver: ResourceResolver) -> None:
        self._resolver = resolver

    async def build(self, request: RequestContext) -> ExecutionSnapshot:
        profile = await self._resolver.resolve_resource(
            request.tenant_id,
            ResourceKind.RUNTIME_PROFILE,
            request.runtime_profile_id,
            selector=request.runtime_profile_version_selector,
        )
        agent = await self._resolve_agent_definition(request)
        bindings = await self._effective_bindings(request)
        capabilities = _capability_selectors(agent)
        skills = await self._resolve_capabilities(
            request.tenant_id, capabilities.get(CapabilityType.SKILL, []), bindings
        )
        mcp_versions = await self._resolve_capability_versions(
            request.tenant_id, capabilities.get(CapabilityType.MCP, []), ResourceKind.MCP
        )
        # TOOL capability（含 builtin 工具）的准入由 capability dispatch 路径执行，
        # 不在此做版本解析——其 ref 多为非 PLUGIN 资源（如 builtin.*）。
        return self.build_from_resolved(
            request,
            runtime_profile=profile,
            agent_definition=agent,
            skills=skills,
            bindings=bindings,
            mcp_versions=mcp_versions,
        )

    async def _resolve_agent_definition(
        self, request: RequestContext
    ) -> ResourceDefinition | None:
        """解析本次执行的 AgentDefinition（persona/model/capability 的 SoT）。

        显式 agent_definition_id 必须存在；缺省回退与 runtime_profile_id 同名——
        一次性迁移产物即同名。回退未命中返回 None（纯 bindings 驱动执行）。
        """
        explicit = request.agent_definition_id or request.runtime_profile_id
        try:
            return await self._resolver.resolve_resource(
                request.tenant_id,
                ResourceKind.AGENT_DEFINITION,
                explicit,
                selector=request.agent_definition_version_selector,
            )
        except ResourceVersionNotFoundError:
            if request.agent_definition_id:
                raise
            return None

    def build_from_resolved(
        self,
        request: RequestContext,
        *,
        runtime_profile: ResourceDefinition,
        agent_definition: ResourceDefinition | None = None,
        skills: list[ResourceDefinition],
        bindings: list[ResourceBinding],
        mcp_versions: dict[str, str] | None = None,
    ) -> ExecutionSnapshot:
        # ADR-012：validate 产生新 frozen 实例，天然断开与缓存 spec_json 的引用共享。
        profile_model = RuntimeProfile.model_validate(runtime_profile.spec_json)
        agent_spec = (
            AgentDefinition.model_validate(agent_definition.spec_json)
            if agent_definition is not None
            else None
        )
        system_prompt = "" if agent_spec is None else agent_spec.system_prompt.strip()
        instructions = "" if agent_spec is None else agent_spec.instructions.strip()
        if instructions:
            system_prompt = f"{system_prompt}\n\n{instructions}".strip()
        provider = None if agent_spec is None else agent_spec.model_ref.id
        # 失败降级链属 runtime mechanics：从 executor_config.model_failover 取
        # （产品面不暴露；Phase 2 Model policy 域回收归属）。
        raw_failover = profile_model.executor_config.get("model_failover", [])
        failover = (
            [str(item) for item in raw_failover]
            if isinstance(raw_failover, list)
            else []
        )
        model_resolution = ModelPolicy(
            provider=provider,
            failover=failover,
            timeout_ms=profile_model.request_timeout_ms,
            max_rounds=profile_model.max_rounds,
            # deadline 下限沿用 ModelPolicy 默认值语义（timeout 过短时不缩短总截止）。
            deadline_ms=max(profile_model.request_timeout_ms * 2, _DEFAULT_DEADLINE_MS),
        )
        return ExecutionSnapshot(
            execution_id=request.execution_id,
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            runtime_profile_id=runtime_profile.id,
            runtime_profile_version=runtime_profile.version,
            agent_definition_id=None if agent_definition is None else agent_definition.id,
            agent_definition_version=(
                None if agent_definition is None else agent_definition.version
            ),
            model_resolution=model_resolution,
            trace_id=request.trace_id,
            system_prompt=system_prompt,
            skill_instructions=_skill_instructions(skills),
            skill_allowed_tools=_skill_allowed_tools(skills),
            skill_versions={skill.id: skill.version for skill in skills},
            mcp_versions=mcp_versions or {},
            # 主模型 provider 精确版本 pin 来自 AgentDefinition.model_ref
            # （TASK-A104 前来源=profile.plugin_bindings）。failover 链不自动
            # 入门槛：进程内注册实现优先；store-backed failover 归 Phase 2
            # Model policy 域统一处理。
            plugin_versions=(
                {} if agent_spec is None else {agent_spec.model_ref.id: agent_spec.model_ref.version}
            ),
            binding_versions={
                binding.binding_id: binding.resource_version_selector for binding in bindings
            },
        )
    async def _effective_bindings(self, request: RequestContext) -> list[ResourceBinding]:
        user_bindings = await self._resolver.list_bindings(
            tenant_id=request.tenant_id,
            subject_type=SubjectType.USER,
            subject_id=request.user_id,
            resource_type=ResourceKind.SKILL,
        )
        tenant_bindings = await self._resolver.list_bindings(
            tenant_id=request.tenant_id,
            subject_type=SubjectType.TENANT,
            subject_id=request.tenant_id,
            resource_type=ResourceKind.SKILL,
        )
        return [*tenant_bindings, *user_bindings]

    async def _resolve_capabilities(
        self,
        tenant_id: str,
        selectors: list[ResourceSelector],
        bindings: list[ResourceBinding],
    ) -> list[ResourceDefinition]:
        # Agent capability 是租户基线 allowlist；Binding 表达用户差异（grant 或
        # 版本覆盖）——语义与原 profile.allowed_skills 基线一致，来源迁移到 Agent。
        effective = _effective_skill_selectors(selectors, bindings)
        resolved: list[ResourceDefinition] = []
        for selector in effective:
            resource = await self._resolver.resolve_resource(
                tenant_id,
                ResourceKind.SKILL,
                selector.resource_id,
                selector=selector.selector,
            )
            resolved.append(resource)
        return resolved

    async def _resolve_capability_versions(
        self,
        tenant_id: str,
        selectors: list[ResourceSelector],
        kind: ResourceKind,
    ) -> dict[str, str]:
        versions: dict[str, str] = {}
        for selector in selectors:
            resource = await self._resolver.resolve_resource(
                tenant_id,
                kind,
                selector.resource_id,
                selector=selector.selector,
            )
            versions[selector.resource_id] = resource.version
        return versions


class _SkillSpecView(SkillDefinition):
    """resolver 侧 skill spec 读取视图（extra=ignore 容忍存量 spec 扩展字段）。

    LEGACY-02（Phase 6 TASK-004）：runtime 侧禁止 raw ``spec_json.get``——经
    定义模型类型化读取；publish 校验仍用严格 SkillDefinition（extra=forbid）。
    """

    model_config = {"extra": "ignore"}


def _skill_instructions(skills: list[ResourceDefinition]) -> dict[str, str]:
    instructions: dict[str, str] = {}
    for skill in skills:
        parsed = _SkillSpecView.model_validate(skill.spec_json)
        if parsed.instructions.strip():
            instructions[skill.id] = parsed.instructions.strip()
    return instructions


def _skill_allowed_tools(skills: list[ResourceDefinition]) -> list[str]:
    allowed: set[str] = set()
    for skill in skills:
        parsed = _SkillSpecView.model_validate(skill.spec_json)
        allowed.update(item for item in parsed.allowed_tools if item.strip())
    return sorted(allowed)


def _selectors(values: list[str]) -> list[ResourceSelector]:
    # 入参来自 spec model 校验后的 str 列表（capability_ref@version_pin）。
    return [_parse_selector(value) for value in values]


def _parse_selector(value: str) -> ResourceSelector:
    resource_id, _separator, selector = value.partition("@")
    if not selector.strip():
        return ResourceSelector(resource_id=resource_id, selector=LATEST_PUBLISHED)
    return ResourceSelector(resource_id=resource_id, selector=selector)


def _effective_skill_selectors(
    agent_selectors: list[ResourceSelector],
    bindings: list[ResourceBinding],
) -> list[ResourceSelector]:
    # Agent capability 是基线 allowlist，Binding 表达用户差异（grant 或版本 pin）。
    effective: dict[str, ResourceSelector] = {
        selector.resource_id: selector for selector in agent_selectors
    }
    for binding in bindings:
        agent_selector = effective.get(binding.resource_id)
        selector = (
            _merge_selector(agent_selector.selector, binding.resource_version_selector)
            if agent_selector is not None
            else binding.resource_version_selector
        )
        effective[binding.resource_id] = ResourceSelector(binding.resource_id, selector)
    return list(effective.values())


def _merge_selector(agent_selector: str, binding_selector: str) -> str:
    if agent_selector != LATEST_PUBLISHED:
        return agent_selector
    return binding_selector


def _is_non_sensitive(resource: ResourceDefinition) -> bool:
    return not _contains_sensitive_ref(resource.spec_json)


def _contains_sensitive_ref(value: object) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).lower()
            if "secret" in lowered or "credential" in lowered:
                return True
            if _contains_sensitive_ref(item):
                return True
    if isinstance(value, list):
        return any(_contains_sensitive_ref(item) for item in value)
    return False
