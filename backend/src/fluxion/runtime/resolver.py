from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from fluxion.registry import RegistryReadStore, RegistryStoreError
from fluxion.resources import (
    ExecutionSnapshot,
    ResourceBinding,
    ResourceDefinition,
    ResourceKind,
    ResourceStatus,
    SubjectType,
    TenantResourceCache,
)
from fluxion.runtime.context import RequestContext

LATEST_PUBLISHED = "latest-published"


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
        bindings = await self._effective_bindings(request)
        skills = await self._resolve_skills(request.tenant_id, profile, bindings)
        mcps = await self._resolve_mcps(request.tenant_id, profile)
        plugins = await self._resolve_plugins(request.tenant_id, profile)
        policy_version = await self._resolve_policy_version(request.tenant_id, profile)
        return self.build_from_resolved(
            request,
            runtime_profile=profile,
            skills=skills,
            bindings=bindings,
            mcp_versions=mcps,
            plugin_versions=plugins,
            policy_version=policy_version,
        )

    def build_from_resolved(
        self,
        request: RequestContext,
        *,
        runtime_profile: ResourceDefinition,
        skills: list[ResourceDefinition],
        bindings: list[ResourceBinding],
        mcp_versions: dict[str, str] | None = None,
        plugin_versions: dict[str, str] | None = None,
        policy_version: str | None = None,
    ) -> ExecutionSnapshot:
        model_resolution = _dict_field(runtime_profile.spec_json.get("model_policy"))
        return ExecutionSnapshot(
            execution_id=request.execution_id,
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            runtime_profile_id=runtime_profile.id,
            runtime_profile_version=runtime_profile.version,
            model_resolution=model_resolution,
            trace_id=request.trace_id,
            system_prompt=_prompt_text(runtime_profile.spec_json.get("prompt")),
            skill_instructions=_skill_instructions(skills),
            skill_allowed_tools=_skill_allowed_tools(skills),
            skill_versions={skill.id: skill.version for skill in skills},
            mcp_versions=mcp_versions or {},
            plugin_versions=plugin_versions or {},
            policy_version=policy_version,
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

    async def _resolve_skills(
        self,
        tenant_id: str,
        profile: ResourceDefinition,
        bindings: list[ResourceBinding],
    ) -> list[ResourceDefinition]:
        selectors = _profile_selectors(profile, "allowed_skills")
        effective = _effective_skill_selectors(selectors, bindings)
        skills: list[ResourceDefinition] = []
        for selector in effective:
            skill = await self._resolver.resolve_resource(
                tenant_id,
                ResourceKind.SKILL,
                selector.resource_id,
                selector=selector.selector,
            )
            skills.append(skill)
        return skills

    async def _resolve_mcps(
        self,
        tenant_id: str,
        profile: ResourceDefinition,
    ) -> dict[str, str]:
        return await self._resolve_configured(tenant_id, profile, "allowed_mcps", ResourceKind.MCP)

    async def _resolve_plugins(
        self,
        tenant_id: str,
        profile: ResourceDefinition,
    ) -> dict[str, str]:
        return await self._resolve_configured(
            tenant_id, profile, "plugin_bindings", ResourceKind.PLUGIN
        )

    async def _resolve_policy_version(
        self,
        tenant_id: str,
        profile: ResourceDefinition,
    ) -> str | None:
        raw = profile.spec_json.get("guardrail_policy")
        if not isinstance(raw, str):
            return None
        selector = _parse_selector(raw)
        policy = await self._resolver.resolve_resource(
            tenant_id,
            ResourceKind.POLICY,
            selector.resource_id,
            selector=selector.selector,
        )
        return policy.version

    async def _resolve_configured(
        self,
        tenant_id: str,
        profile: ResourceDefinition,
        field: str,
        kind: ResourceKind,
    ) -> dict[str, str]:
        versions: dict[str, str] = {}
        for selector in _profile_selectors(profile, field):
            resource = await self._resolver.resolve_resource(
                tenant_id,
                kind,
                selector.resource_id,
                selector=selector.selector,
            )
            versions[selector.resource_id] = resource.version
        return versions


def _prompt_text(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("system", "content", "text"):
            prompt = value.get(key)
            if isinstance(prompt, str) and prompt.strip():
                return prompt.strip()
    return ""


def _skill_instructions(skills: list[ResourceDefinition]) -> dict[str, str]:
    instructions: dict[str, str] = {}
    for skill in skills:
        value = skill.spec_json.get("instructions")
        if isinstance(value, str) and value.strip():
            instructions[skill.id] = value.strip()
    return instructions


def _skill_allowed_tools(skills: list[ResourceDefinition]) -> list[str]:
    allowed: set[str] = set()
    for skill in skills:
        value = skill.spec_json.get("allowed_tools")
        if isinstance(value, list):
            allowed.update(item for item in value if isinstance(item, str) and item.strip())
    return sorted(allowed)


def _profile_selectors(profile: ResourceDefinition, field: str) -> list[ResourceSelector]:
    raw = profile.spec_json.get(field, [])
    if not isinstance(raw, list):
        return []
    return [_parse_selector(value) for value in raw if isinstance(value, str)]


def _parse_selector(value: str) -> ResourceSelector:
    resource_id, _separator, selector = value.partition("@")
    if not selector.strip():
        return ResourceSelector(resource_id=resource_id, selector=LATEST_PUBLISHED)
    return ResourceSelector(resource_id=resource_id, selector=selector)


def _effective_skill_selectors(
    profile_selectors: list[ResourceSelector],
    bindings: list[ResourceBinding],
) -> list[ResourceSelector]:
    # Profile 是租户基线 allowlist，Binding 表达用户/租户差异（grant 或版本 pin）。
    # profile 技能始终保留；存在 Binding 的技能用 Binding 的版本 selector 覆盖；
    # 仅由 Binding 授予、profile 未列出的技能也加入。
    effective: dict[str, ResourceSelector] = {
        selector.resource_id: selector for selector in profile_selectors
    }
    for binding in bindings:
        profile_selector = effective.get(binding.resource_id)
        selector = (
            _merge_selector(profile_selector.selector, binding.resource_version_selector)
            if profile_selector is not None
            else binding.resource_version_selector
        )
        effective[binding.resource_id] = ResourceSelector(binding.resource_id, selector)
    return list(effective.values())


def _merge_selector(profile_selector: str, binding_selector: str) -> str:
    if profile_selector != LATEST_PUBLISHED:
        return profile_selector
    return binding_selector


def _dict_field(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return cast(dict[str, object], value)
    return {}


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
