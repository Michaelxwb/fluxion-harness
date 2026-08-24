from __future__ import annotations

from dataclasses import dataclass
from time import monotonic

from fluxion.resources.contracts import ResourceDefinition, ResourceKind


@dataclass(frozen=True)
class TenantResourceCacheKey:
    tenant_id: str
    kind: ResourceKind
    resource_id: str
    version: str


@dataclass(frozen=True)
class _CacheEntry:
    resource: ResourceDefinition
    expires_at: float


class TenantResourceCache:
    """Small tenant-scoped immutable resource cache for resolver boundaries."""

    def __init__(self, ttl_seconds: float = 60.0, max_entries: int = 1024) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._entries: dict[TenantResourceCacheKey, _CacheEntry] = {}

    def get(
        self,
        tenant_id: str,
        kind: ResourceKind,
        resource_id: str,
        version: str,
    ) -> ResourceDefinition | None:
        key = TenantResourceCacheKey(tenant_id, kind, resource_id, version)
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry.expires_at <= monotonic():
            self._entries.pop(key, None)
            return None
        return entry.resource

    def set(self, resource: ResourceDefinition, *, version_alias: str | None = None) -> None:
        if len(self._entries) >= self._max_entries:
            self._entries.pop(next(iter(self._entries)))
        key = TenantResourceCacheKey(
            resource.tenant_id,
            resource.kind,
            resource.id,
            version_alias or resource.version,
        )
        self._entries[key] = _CacheEntry(resource, monotonic() + self._ttl_seconds)

    def invalidate_tenant(self, tenant_id: str) -> None:
        for key in tuple(self._entries):
            if key.tenant_id == tenant_id:
                self._entries.pop(key, None)
