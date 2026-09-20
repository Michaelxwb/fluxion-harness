"""HTTP 出网 Egress Boundary：allowlist、必填 timeout、≤max_bytes、禁跳转；审计 port。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

MAX_BYTES_DEFAULT = 5 * 1024 * 1024
PROTOCOLS = frozenset({"http", "https"})


class ForbiddenEgressError(PermissionError):
    """目标不在 allowlist：请求未发出。"""


class ResponseTooLargeError(ValueError):
    """响应超过 max_bytes 上限。"""


@dataclass(frozen=True, slots=True)
class EgressPolicy:
    allowed_hosts: tuple[str, ...]
    max_bytes: int = MAX_BYTES_DEFAULT
    timeout_sec: float = 10.0


class EgressBoundary:
    """ctx.http、平台与 MCP 共用：拒绝 host 零外发；逐次调用按 allowlist 判定。"""

    def __init__(
        self,
        *,
        policy: EgressPolicy,
        audit_writer: Any = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._policy = policy
        self._audit_writer = audit_writer
        self._client = httpx.AsyncClient(
            timeout=policy.timeout_sec,
            transport=transport,
            follow_redirects=False,  # 跳转必须逐跳授权
        )

    def _check(self, url: str) -> None:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        if parsed.scheme not in PROTOCOLS:
            raise ForbiddenEgressError(f"scheme not allowed: {parsed.scheme!r}")
        host = parsed.hostname or ""
        if host not in self._policy.allowed_hosts:
            # 拒绝 host 零外发：先判后发
            raise ForbiddenEgressError(f"host not in egress allowlist: {host}")

    async def _check_audited(self, url: str) -> None:

        try:
            self._check(url)
        except ForbiddenEgressError:
            await self._audit(target=url, decision="DENY", status_code=None)
            raise

    async def _audit(self, *, target: str, decision: str, status_code: int | None) -> None:
        if self._audit_writer is None:
            return

        outcome = self._audit_writer.record_egress(
            target_type="HTTP", target=target, policy_decision=decision, status_code=status_code
        )
        if hasattr(outcome, "__await__"):
            await outcome

    async def http_get(
        self, url: str, *, follow_redirects: bool = False
    ) -> httpx.Response:
        await self._check_audited(url)
        response = await self._client.get(url, follow_redirects=False)
        if follow_redirects and response.is_redirect:
            location = response.headers.get("location", "")
            self._check(location)  # 跳转目标也必须过 allowlist
            response = await self._client.get(location, follow_redirects=False)
        if len(response.content) > self._policy.max_bytes:
            raise ResponseTooLargeError(
                f"response exceeds {self._policy.max_bytes} bytes"
            )
        await self._audit(
            target=url, decision="ALLOW", status_code=response.status_code
        )
        return response

    async def http_post(
        self, url: str, *, json_body: dict[str, Any] | None = None
    ) -> httpx.Response:
        await self._check_audited(url)
        response = await self._client.post(url, json=json_body, follow_redirects=False)
        if len(response.content) > self._policy.max_bytes:
            raise ResponseTooLargeError(
                f"response exceeds {self._policy.max_bytes} bytes"
            )
        await self._audit(target=url, decision="ALLOW", status_code=response.status_code)
        return response

    async def aclose(self) -> None:
        await self._client.aclose()
