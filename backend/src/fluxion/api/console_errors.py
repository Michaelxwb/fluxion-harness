from __future__ import annotations

import traceback

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from fluxion.api.responses import failure
from fluxion.errors.console import (
    FORBIDDEN,
    INTERNAL_ERROR,
    RESOURCE_NOT_FOUND,
    VALIDATION_FAILED,
    ConsoleError,
)
from fluxion.observability.logging import emit_error_log


def _register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ConsoleError)
    async def console_error_handler(request: Request, exc: ConsoleError) -> JSONResponse:
        return failure(exc.code, exc.message, status_code=exc.status_code, request=request)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        del exc
        return failure(VALIDATION_FAILED, "validation failed", status_code=400, request=request)

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        # 路由级 404/405 等 HTTPException 需回到统一 envelope，而不是落到
        # 通用 Exception handler 变成 500 INTERNAL_ERROR。
        if exc.status_code == 404:
            return failure(RESOURCE_NOT_FOUND, "not found", status_code=404, request=request)
        if exc.status_code == 403:
            return failure(FORBIDDEN, "forbidden", status_code=403, request=request)
        return failure(
            VALIDATION_FAILED,
            str(exc.detail),
            status_code=exc.status_code,
            request=request,
        )

    @app.exception_handler(Exception)
    async def internal_error_handler(request: Request, exc: Exception) -> JSONResponse:
        _emit_unhandled_error_log(request, exc)
        return failure(INTERNAL_ERROR, "internal error", status_code=500, request=request)


def _emit_unhandled_error_log(request: Request, exc: Exception) -> None:
    emit_error_log(
        request_id=_state_or_header(request, "request_id", "X-Request-ID"),
        trace_id=_state_or_header(request, "trace_id", "X-Trace-ID"),
        tenant_id=_state_or_unknown(request, "tenant_id"),
        actor_id=_state_or_unknown(request, "actor_id"),
        method=request.method,
        route=request.url.path,
        error_type=type(exc).__name__,
        error_code=INTERNAL_ERROR,
        stack=traceback.format_exc(),
    )


def _state_or_header(request: Request, state_key: str, header_name: str) -> str:
    value = getattr(request.state, state_key, None)
    if isinstance(value, str) and value:
        return value
    return request.headers.get(header_name, "unknown")


def _state_or_unknown(request: Request, state_key: str) -> str:
    value = getattr(request.state, state_key, None)
    return value if isinstance(value, str) and value else "unknown"
