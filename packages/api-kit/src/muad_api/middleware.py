from __future__ import annotations

import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response
from starlette.types import ASGIApp

from .context import TRACE_CORRELATION_FIELDS, context_scope
from .locale import normalize_locale


def _trace_id_from_traceparent(value: str | None) -> str:
    if not value:
        return ""
    parts = value.split("-")
    if len(parts) == 4 and len(parts[1]) == 32:
        return parts[1]
    return ""


class RequestContextMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, default_locale: str = "zh-CN") -> None:
        super().__init__(app)
        self.default_locale = default_locale

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())
        trace_id = (
            request.headers.get("X-Trace-Id")
            or _trace_id_from_traceparent(request.headers.get("traceparent"))
            or request_id
        )
        tenant_id = request.headers.get("X-Tenant-Id", "")
        caller_service = request.headers.get("X-Caller-Service", "")
        requested_locale = request.headers.get("X-Locale") or request.headers.get("Accept-Language")
        locale = normalize_locale(requested_locale, self.default_locale)

        request.state.locale = locale
        request.state.trace_id = trace_id
        request.state.request_id = request_id

        fields = {name: "" for name in TRACE_CORRELATION_FIELDS}
        fields.update(trace_id=trace_id, request_id=request_id)
        with context_scope(locale=locale, tenant_id=tenant_id, caller_service=caller_service, **fields):
            response = await call_next(request)
            response.headers["X-Trace-Id"] = trace_id
            response.headers["X-Request-Id"] = request_id
            response.headers["Content-Language"] = locale
            response.headers["Vary"] = "X-Locale, Accept-Language"
            return response
