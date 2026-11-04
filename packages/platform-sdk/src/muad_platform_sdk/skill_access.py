"""Trusted per-Skill context; credentials stay in the parent Runtime/Worker."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Protocol, cast
from urllib.parse import urlsplit

import httpx
from muad_common.redis_client import create_redis_client

from .adapter import MSSAdapter, PlatformAdapterRegistry
from .adapter.mss import check, send
from .adapter.mss_config import PlatformAuthError, settings
from .session.manager import SessionRequest
from .session.redis_manager import RedisPlatformSessionManager
from .types import PlatformConfig, PlatformRequest, PlatformTarget, SecretValue


class PlatformAudit(Protocol):
    async def record_egress(
        self,
        *,
        target_type: str,
        target: str,
        policy_decision: str,
        method: str | None = None,
        error_code: str | None = None,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class SkillPlatformContext:
    tenant_id: str
    actor_user_id: str
    execution_id: str
    execution_type: str
    platform_keys: tuple[str, ...]
    console_url: str
    redis_url: str | None = field(repr=False)
    service_token: str | None = field(repr=False)
    redis_mode: str = "standalone"
    redis_cluster_seeds: str = ""
    audit: PlatformAudit | None = field(default=None, repr=False)


class SkillPlatformAccess:
    def __init__(self, context: SkillPlatformContext) -> None:
        self.context = context
        self.redis = (
            create_redis_client(
                context.redis_url,
                mode=context.redis_mode,
                seeds=[
                    item.strip()
                    for item in (context.redis_cluster_seeds or "").split(",")
                    if item.strip()
                ],
            )
            if context.redis_url
            else None
        )
        self.registry = PlatformAdapterRegistry()
        for adapter in (MSSAdapter(),):
            self.registry.register(adapter)

    async def close(self) -> None:
        if self.redis:
            await self.redis.aclose()

    async def resolve(
        self, key: str, method: str, path: str
    ) -> tuple[PlatformConfig, SessionRequest, RedisPlatformSessionManager]:
        ctx = self.context
        if key not in ctx.platform_keys or not ctx.service_token or self.redis is None:
            raise PlatformAuthError("PLATFORM_ACCESS_UNAVAILABLE")
        data = await self._resolve_http(key, method)
        return self._resolved(key, data)

    async def _resolve_http(self, key: str, method: str) -> dict[str, Any]:
        ctx = self.context
        if not ctx.service_token:
            raise PlatformAuthError("PLATFORM_ACCESS_UNAVAILABLE")
        payload = {
            "execution_ref": {"type": ctx.execution_type, "id": ctx.execution_id},
            "actor_user_id": ctx.actor_user_id,
            "platform_key": key,
            "target": {"type": "PLATFORM_SERVICE", "service": "http", "operation": method},
        }
        headers = {"X-Tenant-Id": ctx.tenant_id, "X-Internal-Service": ctx.service_token}
        try:
            async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
                response = await client.post(
                    ctx.console_url.rstrip("/") + "/internal/runtime/resolve-egress-access",
                    json=payload,
                    headers=headers,
                )
            body = response.json()
        except (httpx.HTTPError, ValueError):
            raise PlatformAuthError("PLATFORM_RESOLVE_FAILED", retryable=True) from None
        if not isinstance(body, dict):
            raise PlatformAuthError("PLATFORM_RESOLVE_INVALID")
        if response.status_code >= 400 or body.get("code") not in ("0", 0):
            raise PlatformAuthError("PLATFORM_ACCESS_DENIED")
        data = body.get("data")
        if not isinstance(data, dict):
            raise PlatformAuthError("PLATFORM_RESOLVE_INVALID")
        if data.get("decision") != "ALLOW":
            raise PlatformAuthError("PLATFORM_ACCESS_DENIED")
        return cast(dict[str, Any], data)

    def _resolved(
        self, key: str, data: dict[str, Any]
    ) -> tuple[PlatformConfig, SessionRequest, RedisPlatformSessionManager]:
        ctx = self.context
        if self.redis is None:
            raise PlatformAuthError("PLATFORM_SESSION_STORE_UNAVAILABLE")
        platform = PlatformConfig(
            name=key, enabled=True, **{k: v for k, v in data["platform"].items() if k != "id"}
        )
        credential = data.get("credential")
        if not credential or not credential.get("ref_id"):
            raise PlatformAuthError("CREDENTIAL_MISSING")
        value = json.dumps(credential["credential_json"], sort_keys=True)
        version = hashlib.sha256(value.encode()).hexdigest()
        request = SessionRequest(
            platform,
            credential.get("actor_scope", "credential:" + credential["ref_id"]),
            version,
            credential["ref_id"],
            SecretValue(value=value, version=version),
        )
        manager = RedisPlatformSessionManager(
            self.redis, self.registry, tenant_id=ctx.tenant_id, platform_id=data["platform"]["id"]
        )
        return platform, request, manager

    async def start(self) -> None:
        # Skill execution, including scripts without a business call, resets idle TTL.
        for key in self.context.platform_keys:
            _, request, manager = await self.resolve(key, "GET", "/")
            await manager.acquire(request)

    async def request(self, key: str, method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            result = await self._request(key, method, path, payload)
        except PlatformAuthError as exc:
            await self._audit(key, method, path, exc.code)
            raise
        await self._audit(key, method, path, None)
        return result

    async def _audit(self, key: str, method: str, path: str, error: str | None) -> None:
        if self.context.audit:
            await self.context.audit.record_egress(
                target_type="PLATFORM_SERVICE",
                target=key + ":" + urlsplit(path).path,
                method=method,
                policy_decision="DENY" if error else "ALLOW",
                error_code=error,
            )

    async def _request(self, key: str, method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        platform, session_request, manager = await self.resolve(key, method, path)
        adapter = self.registry.get(platform.adapter_key)
        if not isinstance(adapter, MSSAdapter):
            raise PlatformAuthError("PLATFORM_ADAPTER_UNSUPPORTED")
        state = await manager.acquire(session_request)
        request = PlatformRequest(target=PlatformTarget(method=method, path=path), payload=payload)
        prepared = await adapter.prepare_request(platform, state, request, None)
        async with adapter._client(settings(platform)) as client:
            response = await send(client, prepared.method, prepared.url, prepared.headers, prepared.body)
        if response.status_code == 403:
            raise PlatformAuthError("PLATFORM_PERMISSION_DENIED")
        # Re-authentication is bounded, and only a read request is replayed automatically.
        try:
            check(response)
        except PlatformAuthError as exc:
            if exc.code != "PLATFORM_AUTH_REJECTED":
                raise
            await manager.renew(session_request)
            if prepared.method == "GET":
                return await self._retry(adapter, platform, request, session_request, manager)
            raise PlatformAuthError("PLATFORM_AUTH_RETRY_REQUIRED") from None
        result = response.json()
        if not isinstance(result, dict):
            raise PlatformAuthError("PLATFORM_RESPONSE_INVALID")
        return cast(dict[str, Any], result)

    async def _retry(
        self,
        adapter: MSSAdapter,
        platform: PlatformConfig,
        request: PlatformRequest,
        session_request: SessionRequest,
        manager: RedisPlatformSessionManager,
    ) -> dict[str, Any]:
        state = await manager.acquire(session_request)
        prepared = await adapter.prepare_request(platform, state, request, None)
        async with adapter._client(settings(platform)) as client:
            response = await send(client, prepared.method, prepared.url, prepared.headers, prepared.body)
        check(response)
        result = response.json()
        if not isinstance(result, dict):
            raise PlatformAuthError("PLATFORM_RESPONSE_INVALID")
        return cast(dict[str, Any], result)
