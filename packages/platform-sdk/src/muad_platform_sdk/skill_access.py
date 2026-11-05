"""Trusted per-Skill context; credentials stay in the parent Runtime/Worker."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, cast

import httpx
from muad_common.redis_client import create_redis_client

from .adapter import MSSAdapter, PlatformAdapterRegistry
from .adapter.mss_config import PlatformAuthError
from .session.manager import SessionRequest
from .session.redis_manager import RedisPlatformSessionManager
from .types import PlatformConfig, SecretValue


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
