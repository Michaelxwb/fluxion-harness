from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter

from fluxion.registry import RegistryStore, RegistryStoreError
from fluxion.resources import (
    ResourceBinding,
    ResourceDefinition,
    ResourceKind,
    SubjectType,
    TenantResourceCache,
)
from fluxion.runtime.resolver import LATEST_PUBLISHED, ResolverPolicy, ResourceResolver


@dataclass(frozen=True, slots=True)
class ConfigChangeEvent:
    tenant_id: str
    kind: ResourceKind
    resource_id: str
    version: str
    revision: int

    @property
    def event_type(self) -> str:
        """领域事件类型（TASK-014）：按资源 kind 判别 resource_published / policy_changed。"""
        return "policy_changed" if self.kind is ResourceKind.POLICY else "resource_published"

    def to_payload(self) -> dict[str, object]:
        return {
            "event_type": self.event_type,
            "tenant_id": self.tenant_id,
            "kind": self.kind.value,
            "resource_id": self.resource_id,
            "version": self.version,
            "revision": self.revision,
        }


@dataclass(frozen=True, slots=True)
class ResourcePublishedEvent(ConfigChangeEvent):
    """资源发布领域事件（TASK-014）：非 POLICY 资源发布。"""

    @property
    def event_type(self) -> str:
        return "resource_published"


@dataclass(frozen=True, slots=True)
class PolicyChangedEvent(ConfigChangeEvent):
    """策略变更领域事件（TASK-014）：POLICY 资源变更。"""

    @property
    def event_type(self) -> str:
        return "policy_changed"


@dataclass(frozen=True, slots=True)
class CacheRevisionState:
    tenant_id: str
    current_revision: int
    last_seen_revision: int


@dataclass(frozen=True, slots=True)
class BindingCacheKey:
    tenant_id: str
    subject_type: str
    subject_id: str
    resource_type: ResourceKind | None


REVISION_POLL_INTERVAL_SECONDS = 0.25


class RevisionAwareResourceResolver(ResourceResolver):
    def __init__(
        self,
        store: RegistryStore,
        *,
        cache: TenantResourceCache,
        policy: ResolverPolicy | None = None,
    ) -> None:
        super().__init__(store, cache=cache, policy=policy)
        self._app_cache = cache
        self._seen_revisions: dict[str, int] = {}
        self._last_polled_at: dict[str, float] = {}
        self._binding_cache: dict[BindingCacheKey, list[ResourceBinding]] = {}

    async def resolve_resource(
        self,
        tenant_id: str,
        kind: ResourceKind,
        resource_id: str,
        *,
        selector: str = LATEST_PUBLISHED,
    ) -> ResourceDefinition:
        await self.poll_revision(tenant_id)
        if selector == LATEST_PUBLISHED:
            cached = self._app_cache.get(tenant_id, kind, resource_id, selector)
            if cached is not None:
                return cached
        return await super().resolve_resource(
            tenant_id,
            kind,
            resource_id,
            selector=selector,
        )

    async def list_bindings(
        self,
        *,
        tenant_id: str,
        subject_type: SubjectType,
        subject_id: str,
        resource_type: ResourceKind | None = None,
    ) -> list[ResourceBinding]:
        await self.poll_revision(tenant_id)
        key = BindingCacheKey(tenant_id, subject_type.value, subject_id, resource_type)
        cached = self._binding_cache.get(key)
        if cached is not None:
            return list(cached)
        bindings = await super().list_bindings(
            tenant_id=tenant_id,
            subject_type=subject_type,
            subject_id=subject_id,
            resource_type=resource_type,
        )
        self._binding_cache[key] = list(bindings)
        return bindings

    def handle_config_changed(self, event: ConfigChangeEvent) -> None:
        self._app_cache.invalidate_tenant(event.tenant_id)
        self._invalidate_binding_tenant(event.tenant_id)
        self._seen_revisions[event.tenant_id] = event.revision
        self._last_polled_at.pop(event.tenant_id, None)

    async def poll_revision(self, tenant_id: str) -> CacheRevisionState:
        now = perf_counter()
        last_polled = self._last_polled_at.get(tenant_id)
        if last_polled is not None and (now - last_polled) < REVISION_POLL_INTERVAL_SECONDS:
            return self._state_with_last_seen(tenant_id)
        self._last_polled_at[tenant_id] = now
        if self._store is None:
            return self._state_with_last_seen(tenant_id)
        try:
            current = await self._store.read_revision(tenant_id=tenant_id)
        except RegistryStoreError:
            return self._state_with_last_seen(tenant_id)
        last_seen = self._seen_revisions.get(tenant_id)
        if last_seen is None:
            self._seen_revisions[tenant_id] = current
            return CacheRevisionState(tenant_id, current, current)
        if current != last_seen:
            self._app_cache.invalidate_tenant(tenant_id)
            self._invalidate_binding_tenant(tenant_id)
            self._seen_revisions[tenant_id] = current
            return CacheRevisionState(tenant_id, current, current)
        return CacheRevisionState(tenant_id, current, last_seen)

    def _state_with_last_seen(self, tenant_id: str) -> CacheRevisionState:
        last_seen = self._seen_revisions.get(tenant_id, 0)
        return CacheRevisionState(tenant_id, last_seen, last_seen)

    def last_seen_revision(self, tenant_id: str) -> int:
        return self._seen_revisions.get(tenant_id, 0)

    def _invalidate_binding_tenant(self, tenant_id: str) -> None:
        for key in tuple(self._binding_cache):
            if key.tenant_id == tenant_id:
                self._binding_cache.pop(key, None)
