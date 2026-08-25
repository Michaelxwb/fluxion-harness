from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from time import monotonic

import httpx2

from fluxion.runtime.secrets import CredentialResolver, SecretProviderError


@dataclass(frozen=True, slots=True)
class MCPHTTPPoolKey:
    tenant_id: str
    user_id: str
    server_uri: str
    resource_version: str
    credential_version: str


@dataclass(slots=True)
class _MCPHTTPPoolEntry:
    client: httpx2.AsyncClient
    credential_ref: str | None
    last_used: float


class MCPHTTPClientPool:
    def __init__(self, *, ttl_seconds: float = 300, max_clients: int = 20) -> None:
        if ttl_seconds <= 0 or max_clients <= 0:
            raise ValueError("MCP HTTP pool limits must be positive")
        self._ttl_seconds = ttl_seconds
        self._max_clients = max_clients
        self._entries: dict[MCPHTTPPoolKey, _MCPHTTPPoolEntry] = {}
        self._lock = asyncio.Lock()
        self._hit_count = 0

    @property
    def client_count(self) -> int:
        return len(self._entries)

    @property
    def hit_count(self) -> int:
        return self._hit_count

    async def get_client(
        self,
        key: MCPHTTPPoolKey,
        *,
        headers: Mapping[str, str],
        timeout_ms: int,
        credential_ref: str | None,
    ) -> httpx2.AsyncClient:
        async with self._lock:
            await self._expire(monotonic())
            await self._invalidate_changed_version(key)
            entry = self._entries.get(key)
            if entry is not None:
                entry.last_used = monotonic()
                self._hit_count += 1
                return entry.client
            await self._evict_for_capacity()
            timeout_seconds = timeout_ms / 1000
            client = httpx2.AsyncClient(
                headers=dict(headers),
                timeout=httpx2.Timeout(timeout_seconds, read=timeout_seconds),
                limits=httpx2.Limits(max_connections=20, max_keepalive_connections=10),
                follow_redirects=True,
            )
            self._entries[key] = _MCPHTTPPoolEntry(client, credential_ref, monotonic())
            return client

    async def invalidate_credential(self, credential_ref: str) -> None:
        async with self._lock:
            keys = [
                key
                for key, entry in self._entries.items()
                if entry.credential_ref == credential_ref
            ]
            await self._close_keys(keys)

    async def close(self) -> None:
        async with self._lock:
            await self._close_keys(list(self._entries))

    async def _expire(self, now: float) -> None:
        expired = [
            key
            for key, entry in self._entries.items()
            if now - entry.last_used >= self._ttl_seconds
        ]
        await self._close_keys(expired)

    async def _invalidate_changed_version(self, requested: MCPHTTPPoolKey) -> None:
        stale = [
            key
            for key in self._entries
            if key.tenant_id == requested.tenant_id
            and key.user_id == requested.user_id
            and key.server_uri == requested.server_uri
            and key != requested
        ]
        await self._close_keys(stale)

    async def _evict_for_capacity(self) -> None:
        if len(self._entries) < self._max_clients:
            return
        oldest = min(self._entries, key=lambda key: self._entries[key].last_used)
        await self._close_keys([oldest])

    async def _close_keys(self, keys: list[MCPHTTPPoolKey]) -> None:
        for key in keys:
            entry = self._entries.pop(key, None)
            if entry is not None:
                await entry.client.aclose()


@dataclass(frozen=True, slots=True)
class MCPClient:
    tenant_id: str
    user_id: str
    server_uri: str
    credential_ref: str
    credential_version: str
    credential_value: str
    resource_version: str = "latest"


class MCPClientPool:
    def __init__(self, credential_resolver: CredentialResolver) -> None:
        self._credential_resolver = credential_resolver
        self._clients: dict[tuple[str, str, str, str, str], MCPClient] = {}

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def get_client(
        self,
        *,
        tenant_id: str,
        user_id: str,
        server_uri: str,
        credential_ref: str,
        resource_version: str = "latest",
    ) -> MCPClient:
        try:
            credential = await self._credential_resolver.resolve_with_metadata(credential_ref)
        except SecretProviderError:
            self._purge_credential(credential_ref)
            raise
        key = (tenant_id, user_id, server_uri, resource_version, credential.version)
        client = self._clients.get(key)
        if client is not None:
            return client
        client = MCPClient(
            tenant_id=tenant_id,
            user_id=user_id,
            server_uri=server_uri,
            credential_ref=credential_ref,
            credential_version=credential.version,
            credential_value=credential.value,
            resource_version=resource_version,
        )
        self._clients[key] = client
        return client

    def _purge_credential(self, credential_ref: str) -> None:
        for key, client in tuple(self._clients.items()):
            if client.credential_ref == credential_ref:
                self._clients.pop(key, None)
