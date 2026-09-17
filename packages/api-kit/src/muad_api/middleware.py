from __future__ import annotations

import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from muad_logging import clear_log_context, set_log_context

from .context import set_request_context
from .locale import normalize_locale


class RequestContextMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, default_locale: str = "zh-CN") -> None:
        super().__init__(app)
        self.default_locale = default_locale

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())
        trace_id = request.headers.get("X-Trace-Id") or request_id
        requested_locale = request.headers.get("X-Locale") or request.headers.get("Accept-Language")
        locale = normalize_locale(requested_locale, self.default_locale)

        set_request_context(locale=locale, trace_id=trace_id, request_id=request_id)
        set_log_context(trace_id=trace_id, request_id=request_id, locale=locale)

        request.state.locale = locale
        request.state.trace_id = trace_id
        request.state.request_id = request_id

        try:
            response = await call_next(request)
            response.headers["X-Trace-Id"] = trace_id
            response.headers["X-Request-Id"] = request_id
            response.headers["Content-Language"] = locale
            return response
        finally:
            clear_log_context()
