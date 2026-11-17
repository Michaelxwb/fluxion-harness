"""Trusted per-Skill context; credentials stay in the parent Runtime/Worker."""

from __future__ import annotations

import hashlib
import json
import ssl
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, cast

import httpx
from muad_common.redis_client import create_redis_client

from .adapter import MSSAdapter, PlatformAdapterRegistry
from .adapter.mss_config import PlatformAuthError
from .platform_transport import PlatformBudgetScope
from .session.manager import SessionRequest
from .session.redis_manager import RedisPlatformSessionManager
from .types import PlatformConfig, SecretValue

#: Console 拒绝（无权限/无凭据）只收敛该平台，不把整个执行判为失败；其余错误原样上抛。
_AUTHORIZATION_DENIALS = frozenset(
    {"PLATFORM_ACCESS_DENIED", "PLATFORM_ACCESS_UNAVAILABLE", "CREDENTIAL_MISSING"}
)


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
    require_all_platforms: bool = False
    budget_scope: PlatformBudgetScope | None = field(default=None, repr=False)
    transport_factory: Callable[[ssl.SSLContext], httpx.AsyncBaseTransport] | None = field(
        default=None, repr=False,
    )

    def __post_init__(self) -> None:
        if self.budget_scope is not None and self.transport_factory is not None:
            raise ValueError("shared transport and local budget cannot be combined")


class SkillPlatformAccess:
    def __init__(self, context: SkillPlatformContext) -> None:
        self.context = context
        self.redis = (
            create_redis_client(
                context.redis_url,
                mode=context.redis_mode,
                seeds=[
                    item.strip() for item in (context.redis_cluster_seeds or "").split(",") if item.strip()
                ],
            )
            if context.redis_url
            else None
        )
        self.registry = PlatformAdapterRegistry()
        adapter = MSSAdapter(budget=context.budget_scope, transport_factory=context.transport_factory)
        self.registry.register(adapter)
        #: 同一执行内 Console 解析结果复用：broker 的授权计算、会话续期与 state 写入
        #: 都读同一份平台/凭据快照，避免每次 get-state 重复出网。
        self._resolved_bindings: dict[
            str, tuple[PlatformConfig, SessionRequest, RedisPlatformSessionManager]
        ] = {}

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

    async def resolve_platform(
        self, key: str
    ) -> tuple[PlatformConfig, SessionRequest, RedisPlatformSessionManager]:
        """同一执行内复用 Console 解析结果（平台只读配置 + 凭据版本）。"""
        cached = self._resolved_bindings.get(key)
        if cached is None:
            cached = await self.resolve(key, "GET", "/")
            self._resolved_bindings[key] = cached
        return cached

    async def allowed_keys(self) -> tuple[str, ...]:
        """受信 Run/Task 上下文内、Console 实际放行的平台集合。

        Skill 声明的 ``platforms`` 只是候选上界；凭据模式与平台权限由 Console 判定。
        全部被拒绝时按契约返回 ``PLATFORM_SESSION_REQUIRED``，部分拒绝只收敛该平台。
        """
        allowed: list[str] = []
        for key in self.context.platform_keys:
            try:
                await self.resolve_platform(key)
            except PlatformAuthError as exc:
                if exc.code in _AUTHORIZATION_DENIALS:
                    if self.context.require_all_platforms:
                        raise PlatformAuthError("PLATFORM_SESSION_REQUIRED") from None
                    continue
                raise
            allowed.append(key)
        if not allowed:
            raise PlatformAuthError("PLATFORM_SESSION_REQUIRED")
        return tuple(allowed)

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
        # 只续期 Console 放行的平台；未授权平台不触发登录，也不进入 state 文件。
        for key in await self.allowed_keys():
            _, request, manager = await self.resolve_platform(key)
            await manager.acquire(request)
