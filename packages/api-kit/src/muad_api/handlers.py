from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .catalog import MessageCatalog
from .errors import AppError
from .response import build_response

logger = logging.getLogger(__name__)


def _json_response(body, status_code: int) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


def install_exception_handlers(app: FastAPI, catalog: MessageCatalog) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(_: Request, exc: AppError):
        spec = catalog.spec(exc.code)
        body = build_response(
            catalog,
            code=exc.code,
            data=exc.data,
            message_args=exc.message_args,
        )
        return _json_response(body, spec.http_status)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, exc: RequestValidationError):
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
    async def http_error_handler(_: Request, exc: StarletteHTTPException):
        code = "COMMON_NOT_FOUND" if exc.status_code == 404 else "COMMON_BAD_REQUEST"
        body = build_response(catalog, code=code)
        return _json_response(body, exc.status_code)

    @app.exception_handler(Exception)
    async def unexpected_error_handler(_: Request, exc: Exception):
        logger.exception("unexpected_error")
        code = "COMMON_INTERNAL_ERROR"
        body = build_response(catalog, code=code)
        return _json_response(body, 500)
