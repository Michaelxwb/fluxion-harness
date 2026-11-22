"""Shared MCP JSON-RPC client/session: singleflight init, strict request ids, bounded reads."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Mapping
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import httpx
from muad_contracts import ToolRetrySafety

from .mcp_audit import McpAuditEvent, McpAuditPort
from .mcp_content import (
    McpArtifactWriter,
    McpContentTooLarge,
    McpResult,
    convert_content,
)
from .mcp_limits import McpLimits
from .platform_transport import McpBudgetScope
from .shared_redis import MeteringUnavailable, SharedLimitExceeded

MCP_PROTOCOL_VERSION = "2025-03-26"
MCP_ACCEPT = "application/json, text/event-stream"


class McpError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        recoverable: bool,
        sent: bool,
        outcome_known: bool,
        replay_safe: bool = False,
        status_code: int | None = None,
        attempt_id: str | None = None,
        rpc_error_code: str | None = None,
        is_error: bool = False,
        retry_after: str | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.recoverable = recoverable
        self.sent = sent
        self.outcome_known = outcome_known
        self.replay_safe = replay_safe
        self.retryable = recoverable and (not sent or replay_safe)
        self.status_code = status_code
        self.attempt_id = attempt_id or uuid4().hex
        self.rpc_error_code = rpc_error_code
        self.is_error = is_error
        self.retry_after = retry_after
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class McpServerConfig:
    server_id: UUID
    key: str
    endpoint: str
    headers: Mapping[str, str] = field(default_factory=dict)
    secret_header: str | None = None
    auth_secret: str | None = field(default=None, repr=False)


@dataclass(slots=True)
class _Session:
    session_id: str | None = None
    initialized: bool = False


@dataclass(frozen=True, slots=True)
class _ReadResponse:
    status_code: int
    headers: Mapping[str, str]
    content: bytes
    content_type: str = ""


class McpClient:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        limits: McpLimits | None = None,
        audit: McpAuditPort | None = None,
        artifact_writer: McpArtifactWriter | None = None,
        budget: McpBudgetScope | None = None,
        timeout_sec: float = 30.0,
        client_name: str = "muad",
    ) -> None:
        self._client = client
        self._limits = limits or McpLimits()
        self._audit = audit
        self._artifact_writer = artifact_writer
        self._budget = budget
        self._timeout_sec = timeout_sec
        self._client_name = client_name
        self._sessions: dict[str, _Session] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._request_ids = 0

    async def call(
        self,
        server: McpServerConfig,
        tool: str,
        arguments: Mapping[str, Any],
        *,
        retry_safety: ToolRetrySafety = ToolRetrySafety.NON_RETRYABLE,
        call_id: str,
    ) -> McpResult:
        attempt_id = uuid4().hex
        started = time.monotonic()
        session = await self._ready_session(
            server, call_id=call_id, attempt_id=attempt_id, retry_safety=retry_safety
        )
        request_id = self._next_request_id()
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": tool, "arguments": dict(arguments)},
        }
        try:
            response = await self._post(
                server, payload, session, retry_safety=retry_safety
            )
            data = self._decode(response, request_id)
            result = data.get("result")
            if not isinstance(result, Mapping):
                raise McpError(
                    "MCP_PROTOCOL_FAILED",
                    "tools/call result is not an object",
                    recoverable=False,
                    sent=True,
                    outcome_known=False,
                    attempt_id=attempt_id,
                )
            is_error = result.get("isError") is True
            if is_error:
                raise McpError(
                    "MCP_TOOL_FAILED",
                    self._content_text(result),
                    recoverable=True,
                    sent=True,
                    outcome_known=True,
                    replay_safe=retry_safety is not ToolRetrySafety.NON_RETRYABLE,
                    attempt_id=attempt_id,
                    is_error=True,
                )
            try:
                content = await convert_content(
                    result.get("content", []),
                    limits=self._limits,
                    writer=self._artifact_writer,
                )
            except McpContentTooLarge as exc:
                raise McpError(
                    "MCP_CONTENT_TOO_LARGE",
                    str(exc),
                    recoverable=False,
                    sent=True,
                    outcome_known=True,
                    attempt_id=attempt_id,
                ) from exc
            await self._record_audit(
                server,
                tool,
                call_id,
                "tools/call",
                str(request_id),
                response.status_code,
                None,
                False,
                True,
                True,
                "OK",
                None,
                started,
                attempt_id,
            )
            return content
        except asyncio.CancelledError:
            await self._discard(server.key)
            raise
        except McpError as exc:
            if exc.code in (
                "MCP_TOOL_FAILED",
                "MCP_PROTOCOL_FAILED",
                "MCP_TRANSPORT_FAILED",
                "MCP_SESSION_EXPIRED",
                "MCP_AUTH_FAILED",
            ):
                await self._discard(server.key)
            await self._record_audit(
                server,
                tool,
                call_id,
                "tools/call",
                str(request_id),
                exc.status_code,
                exc.rpc_error_code,
                exc.is_error,
                exc.sent,
                exc.outcome_known,
                "ERROR",
                exc.code,
                started,
                attempt_id,
            )
            raise

    async def _ready_session(
        self,
        server: McpServerConfig,
        *,
        call_id: str,
        attempt_id: str,
        retry_safety: ToolRetrySafety = ToolRetrySafety.NON_RETRYABLE,
    ) -> _Session:
        lock = self._locks.setdefault(server.key, asyncio.Lock())
        async with lock:
            session = self._sessions.get(server.key)
            if session is not None and session.initialized:
                return session
            session = _Session()
            payload = {
                "jsonrpc": "2.0",
                "id": self._next_request_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": self._client_name, "version": "1"},
                },
            }
            try:
                response = await self._post(
                    server, payload, session, retry_safety=retry_safety
                )
                self._decode(response, payload["id"])
                await self._post(
                    server,
                    {"jsonrpc": "2.0", "method": "notifications/initialized"},
                    session,
                    retry_safety=retry_safety,
                )
                session.initialized = True
                self._sessions[server.key] = session
            except McpError:
                await self._discard(server.key)
                raise
            return session

    async def _post(
        self,
        server: McpServerConfig,
        payload: Mapping[str, Any],
        session: _Session,
        *,
        retry_safety: ToolRetrySafety = ToolRetrySafety.NON_RETRYABLE,
    ) -> _ReadResponse:
        headers = dict(server.headers)
        headers["Accept"] = MCP_ACCEPT
        headers["MCP-Protocol-Version"] = MCP_PROTOCOL_VERSION
        if session.session_id:
            headers["Mcp-Session-Id"] = session.session_id
        if server.auth_secret:
            if server.secret_header:
                headers[server.secret_header] = server.auth_secret
            else:
                headers["Authorization"] = f"Bearer {server.auth_secret}"
        method = str(payload.get("method") or "")
        params = payload.get("params")
        tool_name = params.get("name") if method == "tools/call" and isinstance(params, Mapping) else None
        replay_safe = retry_safety is not ToolRetrySafety.NON_RETRYABLE
        try:
            budget_context = (
                self._budget.attempt(server.key, method, tool_name)
                if self._budget is not None
                else nullcontext(None)
            )
            async with budget_context as lease:
                request = self._client.build_request(
                    "POST", server.endpoint, json=dict(payload), headers=headers,
                    timeout=self._timeout_sec,
                )
                response = await self._client.send(request, stream=True)
                try:
                    declared = response.headers.get("content-length")
                    if declared is not None and int(declared) > self._limits.max_response_bytes:
                        raise McpError(
                            "MCP_CONTENT_TOO_LARGE",
                            "MCP response limit exceeded",
                            recoverable=False,
                            sent=True,
                            outcome_known=True,
                            status_code=response.status_code,
                        )
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > self._limits.max_response_bytes:
                            raise McpError(
                                "MCP_CONTENT_TOO_LARGE",
                                "MCP response limit exceeded",
                                recoverable=False,
                                sent=True,
                                outcome_known=True,
                                status_code=response.status_code,
                            )
                    if lease is not None and response.status_code == 429:
                        lease.outcome = "proxy_rate_limited"
                finally:
                    await response.aclose()
        except SharedLimitExceeded as exc:
            code = "LOCAL_BUDGET_EXHAUSTED" if exc.kind == "TOTAL" else "LOCAL_RATE_LIMITED"
            raise McpError(
                code, str(exc), recoverable=False, sent=False, outcome_known=True
            ) from exc
        except MeteringUnavailable as exc:
            raise McpError(
                "EGRESS_METERING_UNAVAILABLE",
                str(exc),
                recoverable=False,
                sent=False,
                outcome_known=True,
            ) from exc
        except httpx.ConnectError as exc:
            raise McpError(
                "MCP_TRANSPORT_FAILED",
                type(exc).__name__,
                recoverable=True,
                sent=False,
                outcome_known=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise McpError(
                "MCP_TRANSPORT_FAILED",
                type(exc).__name__,
                recoverable=True,
                sent=True,
                outcome_known=False,
                replay_safe=replay_safe,
            ) from exc
        result = _ReadResponse(
            status_code=response.status_code,
            headers=response.headers,
            content=bytes(body),
            content_type=response.headers.get("content-type", ""),
        )
        if result.status_code == 404:
            await self._discard(server.key)
            raise McpError(
                "MCP_SESSION_EXPIRED",
                "MCP session expired",
                recoverable=True,
                sent=False,
                outcome_known=True,
                status_code=404,
            )
        if result.status_code in (401, 403):
            await self._discard(server.key)
            raise McpError(
                "MCP_AUTH_FAILED",
                f"http {result.status_code}",
                recoverable=False,
                sent=True,
                outcome_known=True,
                status_code=result.status_code,
            )
        if result.status_code == 429 or result.status_code >= 500:
            await self._discard(server.key)
            raise McpError(
                "MCP_TRANSPORT_FAILED",
                f"http {result.status_code}",
                recoverable=True,
                sent=True,
                outcome_known=False,
                status_code=result.status_code,
                replay_safe=replay_safe,
                retry_after=result.headers.get("Retry-After"),
            )
        if result.status_code >= 400:
            await self._discard(server.key)
            raise McpError(
                "MCP_TRANSPORT_FAILED",
                f"http {result.status_code}",
                recoverable=True,
                sent=True,
                outcome_known=False,
                status_code=result.status_code,
                replay_safe=replay_safe,
            )
        session_id = result.headers.get("Mcp-Session-Id")
        if session_id:
            session.session_id = session_id
        return result

    def _decode(self, response: _ReadResponse, request_id: object) -> dict[str, Any]:
        if response.content_type.startswith("text/event-stream"):
            payload = self._decode_sse(response)
        else:
            try:
                payload = json.loads(response.content.decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as exc:
                raise McpError(
                    "MCP_PROTOCOL_FAILED",
                    "invalid JSON payload",
                    recoverable=False,
                    sent=True,
                    outcome_known=False,
                ) from exc
        if not isinstance(payload, dict):
            raise McpError(
                "MCP_PROTOCOL_FAILED",
                "payload is not an object",
                recoverable=False,
                sent=True,
                outcome_known=False,
            )
        if payload.get("id") != request_id:
            raise McpError(
                "MCP_PROTOCOL_FAILED",
                "response id mismatch",
                recoverable=False,
                sent=True,
                outcome_known=False,
            )
        error = payload.get("error")
        if isinstance(error, Mapping):
            code = error.get("code")
            raise McpError(
                "MCP_PROTOCOL_FAILED",
                str(error.get("message") or "RPC error"),
                recoverable=False,
                sent=True,
                outcome_known=False,
                rpc_error_code=str(code) if code is not None else None,
            )
        return payload

    def _decode_sse(self, response: _ReadResponse) -> dict[str, Any]:
        payload: dict[str, Any] | None = None
        try:
            text = response.content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise McpError(
                "MCP_PROTOCOL_FAILED", "invalid SSE payload", recoverable=False,
                sent=True, outcome_known=False,
            ) from exc
        for line in text.splitlines():
            if len(line.encode("utf-8")) > self._limits.max_sse_event_bytes:
                raise McpError(
                    "MCP_CONTENT_TOO_LARGE",
                    "MCP SSE event limit exceeded",
                    recoverable=False,
                    sent=True,
                    outcome_known=True,
                )
            if not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            if not raw:
                continue
            try:
                item = json.loads(raw)
            except ValueError:
                continue
            if isinstance(item, dict):
                payload = item
        if payload is None:
            raise McpError(
                "MCP_PROTOCOL_FAILED",
                "empty SSE payload",
                recoverable=False,
                sent=True,
                outcome_known=False,
            )
        return payload

    async def _discard(self, server_key: str) -> None:
        self._sessions.pop(server_key, None)

    def _next_request_id(self) -> int:
        self._request_ids += 1
        return self._request_ids

    async def _record_audit(
        self,
        server: McpServerConfig,
        tool: str,
        call_id: str,
        rpc_method: str,
        request_id: str,
        http_status: int | None,
        rpc_error_code: str | None,
        is_error: bool,
        sent: bool,
        outcome_known: bool,
        result_status: str,
        error_code: str | None,
        started: float,
        attempt_id: str,
    ) -> None:
        if self._audit is None:
            return
        await self._audit.record(
            McpAuditEvent(
                server_key=server.key,
                tool_name=tool,
                call_id=call_id,
                rpc_method=rpc_method,
                request_id=request_id,
                http_status=http_status,
                rpc_error_code=rpc_error_code,
                is_error=is_error,
                sent=sent,
                outcome_known=outcome_known,
                result_status=result_status,
                error_code=error_code,
                latency_ms=int((time.monotonic() - started) * 1000),
                attempt_id=attempt_id,
            )
        )

    @staticmethod
    def _content_text(result: Mapping[str, Any]) -> str:
        content = result.get("content")
        if isinstance(content, list):
            parts = [
                str(item.get("text", ""))
                for item in content
                if isinstance(item, Mapping) and item.get("type") == "text"
            ]
            if parts:
                return "\n".join(parts)
        return "MCP tool returned isError"

    async def aclose(self) -> None:
        self._sessions.clear()


__all__ = ["McpClient", "McpError", "McpServerConfig"]
