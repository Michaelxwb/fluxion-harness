"""Trusted per-Skill context; credentials stay in the parent Runtime/Worker."""

from __future__ import annotations

import hashlib
import json
import ssl
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, cast

import httpx
from muad_common.redis_client import create_redis_client
from redis.exceptions import RedisClusterException, RedisError

from .adapter import MSSAdapter, PlatformAdapterRegistry
from .adapter.mss_config import PlatformAuthError
from .platform_transport import PlatformBudgetScope
from .request_budget import LocalRateLimitError
from .session.manager import SessionRequest
from .session.redis_manager import RedisPlatformSessionManager
from .shared_redis import SharedLimitExceeded
from .types import PlatformConfig, PlatformSession, SecretValue, SessionMode

#: Console 拒绝（无权限/无凭据）只收敛该平台，不把整个执行判为失败；其余错误原样上抛。
_AUTHORIZATION_DENIALS = frozenset(
    {"PLATFORM_ACCESS_DENIED", "PLATFORM_ACCESS_UNAVAILABLE", "CREDENTIAL_MISSING"}
)
#: 这些错误说明执行级基础设施不可用，不能降级成某个平台的结果条目。
_HARD_RESOLUTION_CODES = frozenset(
    {"PLATFORM_ACCESS_UNAVAILABLE", "PLATFORM_RESOLVE_FAILED", "PLATFORM_RESOLVE_INVALID"}
)


def _interrupt_reason(exc: BaseException) -> str:
    if isinstance(exc, LocalRateLimitError):
        return "budget_exhausted"
    if isinstance(exc, SharedLimitExceeded):
        return "rate_limited"
    if isinstance(exc, (RedisError, RedisClusterException)):
        return "store_unavailable"
    return "check_interrupted"


@dataclass(frozen=True, slots=True)
class PlatformAttempt:
    """一个平台在本批次中的最近一次结果；只投影安全字段。"""

    key: str
    name: str
    authentication: str
    available: bool | None
    expires_at: datetime | None = None
    code: str | None = None
    detail: dict[str, object] | None = None
    retryable: bool = False
    reason: str | None = None
    session: PlatformSession | None = field(default=None, repr=False, compare=False)

    def payload(self) -> dict[str, object]:
        item: dict[str, object] = {
            "key": self.key,
            "name": self.name,
            "authentication": self.authentication,
        }
        if self.available is None:
            item["available"] = None
            if self.reason:
                item["reason"] = self.reason
            return item
        if self.available:
            item["available"] = True
            item["expiresAt"] = self.expires_at.isoformat() if self.expires_at else None
            return item
        item["available"] = False
        error: dict[str, object] = {"code": self.code or "PLATFORM_SESSION_REQUIRED"}
        if self.detail:
            error["detail"] = self.detail
        if self.retryable:
            error["retryable"] = True
        item["error"] = error
        return item


@dataclass(slots=True)
class PlatformSessionReport:
    """执行级最后报告：按请求顺序保存每个平台最近一次结果。

    不持久化、不跨执行共享；每次采集覆盖同一平台，顺序按首次出现。
    """

    _order: list[str] = field(default_factory=list)
    _attempts: dict[str, PlatformAttempt] = field(default_factory=dict)
    #: 基础设施/预算中断的分类器；由调用方决定是 hard failure 还是“报告已知项”。
    hard_error: BaseException | None = field(default=None, repr=False, compare=False)

    def record(self, attempt: PlatformAttempt) -> None:
        if attempt.key not in self._attempts:
            self._order.append(attempt.key)
        self._attempts[attempt.key] = attempt

    def payload(self, keys: Sequence[str] | None = None) -> dict[str, object]:
        order = list(keys) if keys is not None else list(self._order)
        return {
            "platforms": [
                self._attempts[key].payload() for key in order if key in self._attempts
            ]
        }

    @property
    def has_failures(self) -> bool:
        return any(attempt.available is False for attempt in self._attempts.values())

    @property
    def has_success(self) -> bool:
        return any(attempt.available is True for attempt in self._attempts.values())

    def attempts(self) -> tuple[PlatformAttempt, ...]:
        return tuple(self._attempts[key] for key in self._order if key in self._attempts)

    def failures(self) -> tuple[PlatformAttempt, ...]:
        return tuple(
            attempt
            for key in self._order
            if (attempt := self._attempts.get(key)) is not None and attempt.available is False
        )

    def first_failure(self) -> PlatformAttempt | None:
        failures = self.failures()
        return failures[0] if failures else None

    def successes(self) -> tuple[PlatformAttempt, ...]:
        return tuple(
            attempt
            for key in self._order
            if (attempt := self._attempts.get(key)) is not None and attempt.available is True
        )


class PlatformScope(StrEnum):
    AUTO = "AUTO"
    SELECTED = "SELECTED"
    NONE = "NONE"


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
    scope: PlatformScope = PlatformScope.AUTO

    def __post_init__(self) -> None:
        if self.platform_keys and self.scope is PlatformScope.AUTO:
            object.__setattr__(self, "scope", PlatformScope.SELECTED)
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
        #: 执行级最后报告：只保存在 broker 生命周期内，供 list/start/get-state 共用。
        self.report = PlatformSessionReport()

    async def close(self) -> None:
        if self.redis:
            await self.redis.aclose()

    async def resolve(
        self, key: str, method: str, path: str
    ) -> tuple[PlatformConfig, SessionRequest, RedisPlatformSessionManager]:
        ctx = self.context
        if ctx.scope is PlatformScope.NONE or (
            ctx.scope is PlatformScope.SELECTED and key not in ctx.platform_keys
        ) or not ctx.service_token or self.redis is None:
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
        if self.context.scope is PlatformScope.NONE:
            return ()
        keys = self.context.platform_keys
        if self.context.scope is PlatformScope.AUTO and not keys:
            keys = await self._discover_keys()
        allowed: list[str] = []
        for key in keys:
            try:
                await self.resolve_platform(key)
            except PlatformAuthError as exc:
                if exc.code in _AUTHORIZATION_DENIALS:
                    if self.context.require_all_platforms:
                        raise PlatformAuthError("PLATFORM_SESSION_REQUIRED") from None
                    continue
                raise
            allowed.append(key)
        if not allowed and self.context.scope is PlatformScope.SELECTED:
            raise PlatformAuthError("PLATFORM_SESSION_REQUIRED")
        return tuple(allowed)

    async def _discover_keys(self) -> tuple[str, ...]:
        return tuple(item["key"] for item in await self.list_platforms() if item.get("available"))

    def _mark_not_checked(
        self,
        keys: Sequence[str],
        metadata: Mapping[str, Mapping[str, Any]],
        exc: Exception,
    ) -> None:
        reason = _interrupt_reason(exc)
        for key in keys:
            item = metadata.get(key, {})
            self.report.record(
                PlatformAttempt(
                    key=key,
                    name=str(item.get("name") or key),
                    authentication=str(item.get("authentication") or "session"),
                    available=None,
                    reason=reason,
                )
            )
        self.report.hard_error = exc

    def batch_error(self, keys: Sequence[str]) -> PlatformAuthError:
        exc = self.report.hard_error
        detail: dict[str, object] | None = None
        if isinstance(exc, LocalRateLimitError):
            code, retryable = exc.code, exc.retryable
        elif isinstance(exc, SharedLimitExceeded):
            code = "LOCAL_BUDGET_EXHAUSTED" if exc.kind == "TOTAL" else "LOCAL_RATE_LIMITED"
            retryable = False
            detail = {
                "limit_kind": exc.kind,
                "scope": str(exc.scope),
                "rule_id": exc.rule_id,
            }
        elif isinstance(exc, (RedisError, RedisClusterException)):
            code, retryable = "PLATFORM_SESSION_STORE_UNAVAILABLE", True
        elif isinstance(exc, PlatformAuthError):
            code, retryable, detail = exc.code, exc.retryable, exc.detail
        else:
            code, retryable = "PLATFORM_RESOLVE_FAILED", True
        return PlatformAuthError(
            code,
            retryable=retryable,
            detail=detail,
            platform_sessions=self.report.payload(keys),
        )

    async def collect_batch(
        self,
        keys: Sequence[str],
        *,
        metadata: Mapping[str, Mapping[str, Any]] | None = None,
        refresh: bool = False,
    ) -> PlatformSessionReport:
        """按请求顺序逐平台解析/获取，记录最后报告；不抛出平台级失败。

        基础设施/预算/限流中断时保留已知结果，并把当前及剩余平台标成未检查；
        调用方用 ``hard_error``/``batch_error()`` 决定是硬失败还是返回报告。
        缺凭据、授权拒绝、认证失败和各平台自身错误只进入报告。
        """
        meta = metadata or {}
        for index, key in enumerate(keys):
            item = meta.get(key, {})
            name = str(item.get("name") or key)
            authentication = str(item.get("authentication") or "session")
            try:
                platform, request, manager = await self.resolve_platform(key)
            except PlatformAuthError as exc:
                if exc.code in _HARD_RESOLUTION_CODES:
                    self._mark_not_checked(keys[index:], meta, exc)
                    return self.report
                self.report.record(
                    PlatformAttempt(
                        key=key,
                        name=name,
                        authentication=authentication,
                        available=False,
                        code=exc.code,
                        detail=exc.detail,
                        retryable=exc.retryable,
                    )
                )
                continue
            name = str(item.get("name") or platform.name or key)
            authentication = str(
                item.get("authentication")
                or ("none" if platform.credential_mode == "NONE" else "session")
            )
            adapter = self.registry.get(platform.adapter_key)
            if adapter.session_mode is not SessionMode.SESSION:
                self.report.record(
                    PlatformAttempt(
                        key=key,
                        name=name,
                        authentication=authentication,
                        available=None,
                        reason="session_not_applicable",
                    )
                )
                continue
            try:
                state = await manager.renew(request) if refresh else await manager.acquire(request)
            except PlatformAuthError as exc:
                self.report.record(
                    PlatformAttempt(
                        key=key,
                        name=name,
                        authentication=authentication,
                        available=False,
                        code=exc.code,
                        detail=exc.detail,
                        retryable=exc.retryable,
                    )
                )
                continue
            except (LocalRateLimitError, SharedLimitExceeded, RedisError, RedisClusterException) as exc:
                self._mark_not_checked(keys[index:], meta, exc)
                return self.report
            if state is None:
                self.report.record(
                    PlatformAttempt(
                        key=key,
                        name=name,
                        authentication=authentication,
                        available=None,
                        reason="session_not_available",
                    )
                )
                continue
            self.report.record(
                PlatformAttempt(
                    key=key,
                    name=name,
                    authentication=authentication,
                    available=True,
                    expires_at=state.expires_at,
                    session=state,
                )
            )
        return self.report

    async def list_platforms(self) -> list[dict[str, Any]]:
        ctx = self.context
        if not ctx.service_token:
            raise PlatformAuthError("PLATFORM_ACCESS_UNAVAILABLE")
        payload = {
            "execution_ref": {"type": ctx.execution_type, "id": ctx.execution_id},
            "actor_user_id": ctx.actor_user_id,
        }
        headers = {"X-Tenant-Id": ctx.tenant_id, "X-Internal-Service": ctx.service_token}
        try:
            async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
                response = await client.post(
                    ctx.console_url.rstrip("/") + "/internal/runtime/list-platforms",
                    json=payload,
                    headers=headers,
                )
            body = response.json()
        except (httpx.HTTPError, ValueError):
            raise PlatformAuthError("PLATFORM_RESOLVE_FAILED", retryable=True) from None
        if response.status_code >= 400 or not isinstance(body, dict) or body.get("code") not in ("0", 0):
            raise PlatformAuthError("PLATFORM_ACCESS_DENIED")
        data = body.get("data")
        platforms = data.get("platforms") if isinstance(data, dict) else None
        if not isinstance(platforms, list):
            raise PlatformAuthError("PLATFORM_RESOLVE_INVALID")
        return [item for item in platforms if isinstance(item, dict) and item.get("key")]

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

    async def start(self) -> PlatformSessionReport:
        # Explicit declarations collect the requested set and report per-platform
        # failures before the script starts; AUTO remains fully lazy.
        if self.context.scope is not PlatformScope.SELECTED:
            return self.report
        requested = tuple(dict.fromkeys(self.context.platform_keys))
        await self.collect_batch(requested)
        if self.report.hard_error is not None:
            raise self.batch_error(requested)
        first = self.report.first_failure()
        if first is not None:
            raise PlatformAuthError(
                first.code or "PLATFORM_SESSION_REQUIRED",
                retryable=first.retryable,
                detail=first.detail,
                platform_sessions=self.report.payload(requested),
            )
        return self.report
