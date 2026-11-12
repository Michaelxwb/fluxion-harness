"""Explicit platform HTTP budget scope; the session broker never proxies business HTTP."""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import SplitResult, urlsplit

import httpx

from .adapter.mss_config import PlatformAuthError
from .request_budget import (
    AttemptContext,
    BudgetController,
    BudgetLease,
    InvocationBudget,
    LocalRateLimitError,
)
from .types import PlatformConfig, PreparedRequest

logger = logging.getLogger(__name__)


class PlatformBudgetAudit(Protocol):
    async def record_egress(
        self, *, target_type: str, target: str, policy_decision: str,
        method: str | None = None, error_code: str | None = None,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class PlatformBudgetScope:
    controller: BudgetController | None
    invocation: InvocationBudget | None = field(default=None, repr=False)
    audit: PlatformBudgetAudit | None = field(default=None, repr=False)

    @asynccontextmanager
    async def attempt(self, platform_key: str, method: str, url: str) -> AsyncIterator[BudgetLease | None]:
        if self.controller is None:
            yield None
            return
        context = AttemptContext(platform_key=platform_key, method=method, path=url,
                                 invocation=self.invocation)
        try:
            async with self.controller.attempt(context) as lease:
                yield lease
        except LocalRateLimitError as exc:
            await self._denied(platform_key, method, exc)
            raise

    async def _denied(self, platform_key: str, method: str, error: LocalRateLimitError) -> None:
        # Safe diagnostic projection: no URL/query, response, headers or credentials.
        logger.warning("egress_budget_denied", extra={
            "policy_decision": "DENY", "platform": platform_key,
            "limit_kind": error.limit_kind, "rule_id": error.rule_id,
        })
        if self.audit is None:
            return
        try:
            await self.audit.record_egress(
                target_type="PLATFORM_SERVICE", target=platform_key + ":" + error.rule_id,
                policy_decision="DENY", method=method, error_code=str(error),
            )
        except Exception as exc:
            # A failed observer must never permit or retry a denied request.
            logger.warning("egress_budget_audit_failed", extra={"error_type": type(exc).__name__})


class PlatformHttpSender:
    """Send an adapter-prepared request to its configured origin using a borrowed client.

    Auth/refresh remain the caller's Adapter/SessionManager responsibilities. Each explicit
    retry is a separate send. This object does not own or close the injected HTTP client.
    """

    def __init__(self, platform: PlatformConfig, client: httpx.AsyncClient,
                 *, budget: PlatformBudgetScope | None = None) -> None:
        self._platform = platform.model_copy(deep=True)
        self._client, self._budget = client, budget

    async def send(self, prepared: PreparedRequest) -> httpx.Response:
        from .adapter.mss import send

        self._check_target(prepared)
        return await send(self._client, prepared.method, prepared.url, prepared.headers,
                          prepared.body, platform_key=self._platform.key, budget=self._budget)

    def _check_target(self, prepared: PreparedRequest) -> None:
        try:
            base = urlsplit(str(self._platform.resolver_config.get("base_url", "")))
            target = urlsplit(prepared.url)
            same_origin = self._origin(base) == self._origin(target)
        except ValueError:
            raise PlatformAuthError("PLATFORM_TARGET_INVALID") from None
        if (not self._platform.enabled or target.scheme not in {"http", "https"}
                or target.username is not None or target.password is not None or target.fragment
                or not same_origin):
            raise PlatformAuthError("PLATFORM_TARGET_INVALID")
        if prepared.method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
            raise PlatformAuthError("PLATFORM_TARGET_INVALID")

    @staticmethod
    def _origin(url: SplitResult) -> tuple[str, str | None, int | None]:
        port = url.port if url.port is not None else {"http": 80, "https": 443}.get(url.scheme)
        return url.scheme, url.hostname, port

@dataclass(frozen=True, slots=True)
class McpBudgetScope:
    controller: BudgetController | None
    invocation: InvocationBudget | None = field(default=None, repr=False)

    @asynccontextmanager
    async def attempt(
        self,
        server_key: str,
        rpc_method: str,
        tool_name: str | None = None,
    ) -> AsyncIterator[BudgetLease | None]:
        if self.controller is None:
            yield None
            return
        context = AttemptContext(mcp_server_key=server_key, rpc_method=rpc_method,
                                 tool_name=tool_name, invocation=self.invocation)
        async with self.controller.attempt(context) as lease:
            yield lease
