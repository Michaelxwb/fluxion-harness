from __future__ import annotations

import logging
import sys
import traceback
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .catalog import MessageCatalog
from .errors import AppError
from .response import ApiResponse, build_response

logger = logging.getLogger(__name__)


def _json_response(body: ApiResponse[Any], status_code: int) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


def install_exception_handlers(app: FastAPI, catalog: MessageCatalog) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        # 5xx 必须可归因：带抛出点的堆栈记一条 warning（2026-10-20 容量压测中 140+ 个
        # 500 无任何日志，排查被迫反复加中间件）。
        if catalog.spec(exc.code).http_status >= 500:
            logger.warning(
                "app_error code=%s method=%s path=%s",
                exc.code,
                request.method,
                request.url.path,
                exc_info=exc,
            )
        spec = catalog.spec(exc.code)
        body = build_response(
            catalog,
            code=exc.code,
            data=exc.data,
            message_args=exc.message_args,
        )
        return _json_response(body, spec.http_status)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        safe_errors = [
            {
                "loc": [str(value) for value in item.get("loc", ())],
                "type": item.get("type", "validation_error"),
            }
            for item in exc.errors()
        ]
        code = "COMMON_VALIDATION_ERROR"
        body = build_response(catalog, code=code, data={"errors": safe_errors})
        return _json_response(body, catalog.spec(code).http_status)

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = "COMMON_NOT_FOUND" if exc.status_code == 404 else "COMMON_BAD_REQUEST"
        body = build_response(catalog, code=code)
        return _json_response(body, exc.status_code)

    @app.exception_handler(Exception)
    async def unexpected_error_handler(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("unexpected_error")
        # 兜底输出到 stderr：日志 handler 被绕过/级别被调高时，未处理异常仍然可见。
        traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stderr)
        sys.stderr.flush()
        code = "COMMON_INTERNAL_ERROR"
        body = build_response(catalog, code=code)
        return _json_response(body, 500)
