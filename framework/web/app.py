import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from framework.observability.logging import configure_logging
from framework.settings import get_settings
from framework.web.errors import AppError
from framework.web.middleware import RequestContextMiddleware
from framework.web.response import failure

logger = logging.getLogger("app.error")


def _json_error(*, status_code: int, code: str, message: str, data: object | None = None) -> JSONResponse:
    body = failure(code=code, message=message, data=data).model_dump(mode="json")
    return JSONResponse(status_code=status_code, content=body)


def create_app(*, title: str, service_name: str) -> FastAPI:
    settings = get_settings()
    configure_logging(service_name=service_name, level=settings.log_level)
    app = FastAPI(title=title)
    app.add_middleware(RequestContextMiddleware)

    @app.exception_handler(AppError)
    async def app_error_handler(_: Request, exc: AppError):
        logger.warning(
            "application_error",
            extra={"event": "application_error", "code": exc.code, "status_code": exc.status_code},
        )
        return _json_error(
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            data=exc.details,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, exc: RequestValidationError):
        return _json_error(
            status_code=422,
            code="VALIDATION_ERROR",
            message="request validation failed",
            data=exc.errors(),
        )

    @app.exception_handler(HTTPException)
    async def http_error_handler(_: Request, exc: HTTPException):
        return _json_error(
            status_code=exc.status_code,
            code="HTTP_ERROR",
            message=str(exc.detail),
        )

    @app.exception_handler(Exception)
    async def unknown_error_handler(_: Request, exc: Exception):
        logger.exception("unhandled_exception", extra={"event": "unhandled_exception"})
        return _json_error(
            status_code=500,
            code="INTERNAL_ERROR",
            message="internal server error",
        )

    return app
