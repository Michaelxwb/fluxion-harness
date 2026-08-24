from __future__ import annotations

from time import perf_counter
from uuid import uuid4

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response
from starlette.types import ASGIApp

from fluxion.config import DevModeSettings
from fluxion.errors.console import INTERNAL_ERROR
from fluxion.observability.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from fluxion.observability.logging import emit_access_log


class RequestContextMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, *, dev_mode: DevModeSettings | None = None) -> None:
        super().__init__(app)
        self._dev_mode = dev_mode or DevModeSettings.from_env()

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        started = perf_counter()
        tenant_id, actor_id = self._identity(request)
        context = RequestContext(
            request_id=_request_id(request.headers.get("X-Request-ID")),
            trace_id=_trace_id(request.headers.get("X-Trace-ID")),
            tenant_id=tenant_id,
            actor_id=actor_id,
            method=request.method,
            route=request.url.path,
            client_ip=request.client.host if request.client is not None else "unknown",
            user_agent=request.headers.get("User-Agent", ""),
        )
        token = bind_request_context(context)
        request.state.request_id = context.request_id
        request.state.trace_id = context.trace_id
        request.state.tenant_id = context.tenant_id
        request.state.actor_id = context.actor_id
        status_code = 500
        biz_code = INTERNAL_ERROR
        publish_id: str | None = None
        is_health = request.url.path == "/healthz"
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = context.request_id
            response.headers.setdefault("X-Trace-ID", context.trace_id)
            biz_code = _biz_code(response)
            publish_id = response.headers.get("X-Publish-ID")
            return response
        finally:
            latency_ms = (perf_counter() - started) * 1000
            if not is_health or status_code >= 500:
                emit_access_log(
                    context,
                    status_code=status_code,
                    biz_code=biz_code,
                    latency_ms=latency_ms,
                    headers=dict(request.headers),
                    query=dict(request.query_params),
                    publish_id=publish_id,
                )
            reset_request_context(token)

    def _identity(self, request: Request) -> tuple[str, str]:
        if self._dev_mode.enabled:
            return self._dev_mode.tenant_id, self._dev_mode.actor_id
        return (
            _header_or_unknown(request.headers.get("X-Tenant-ID")),
            _header_or_unknown(request.headers.get("X-Actor-ID")),
        )


def _request_id(value: str | None) -> str:
    if value is not None and value.strip():
        return value.strip()
    return f"req_{uuid4().hex}"


def _trace_id(value: str | None) -> str:
    if value is not None and value.strip():
        return value.strip()
    return f"trace_{uuid4().hex}"


def _header_or_unknown(value: str | None) -> str:
    if value is not None and value.strip():
        return value.strip()
    return "unknown"


def _biz_code(response: Response) -> int:
    raw = response.headers.get("X-Biz-Code")
    if raw is None:
        return 0 if response.status_code < 400 else INTERNAL_ERROR
    try:
        return int(raw)
    except ValueError:
        return INTERNAL_ERROR
