"""Product Agent API（closure TASK-003 / P1C-08）。

产品面以 ``agent_id`` 为主坐标：产品信息查询与执行发起；RuntimeProfile
mechanics 由服务层解析，不进入任何产品面响应。统一 envelope
（``{code, message, data, request_id}``）。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from fluxion.api.middleware import RequestContextMiddleware
from fluxion.api.responses import failure, success
from fluxion.errors.console import (
    INTERNAL_ERROR,
    RUNTIME_APPLICATION_ERROR,
    VALIDATION_FAILED,
)
from fluxion.observability.context import current_context
from fluxion.services.agents_app import ProductAgentApplicationService
from fluxion.services.runtime_app import RuntimeApplicationError


class ProductRunPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    tenant_id: str
    user_id: str
    session_id: str
    input_message: str = Field(alias="input")


def create_app(service: ProductAgentApplicationService) -> FastAPI:
    @asynccontextmanager
    async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield

    app = FastAPI(title="Fluxion Product Agent API", lifespan=_lifespan)
    app.add_middleware(RequestContextMiddleware, require_identity=False)
    _register_error_handlers(app)

    @app.get("/api/v1/agents/{agent_id}")
    async def get_agent(
        agent_id: str,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")],
    ) -> JSONResponse:
        face = await service.get_agent_face(tenant_id=x_tenant_id, agent_id=agent_id)
        return success(face)

    @app.post("/api/v1/agents/{agent_id}/runs")
    async def run_agent(agent_id: str, payload: ProductRunPayload) -> JSONResponse:
        request_id = _context_request_id()
        result = await service.run(
            tenant_id=payload.tenant_id,
            agent_id=agent_id,
            user_id=payload.user_id,
            session_id=payload.session_id,
            input_message=payload.input_message,
            request_id=request_id,
        )
        return success(result)

    @app.post("/api/v1/agents/{agent_id}/runs:stream")
    async def stream_agent(agent_id: str, payload: ProductRunPayload) -> StreamingResponse:
        request_id = _context_request_id()
        events = service.stream(
            tenant_id=payload.tenant_id,
            agent_id=agent_id,
            user_id=payload.user_id,
            session_id=payload.session_id,
            input_message=payload.input_message,
            request_id=request_id,
        )
        return StreamingResponse(
            _sse_events(events, request_id), media_type="text/event-stream"
        )

    return app


def _register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RuntimeApplicationError)
    async def runtime_error_handler(
        request: Request, exc: RuntimeApplicationError
    ) -> JSONResponse:
        return failure(RUNTIME_APPLICATION_ERROR, str(exc), status_code=502, request=request)

    @app.exception_handler(RequestValidationError)
    async def validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        del exc
        return failure(VALIDATION_FAILED, "请求参数无效", status_code=400, request=request)

    @app.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception) -> JSONResponse:
        del exc
        return failure(INTERNAL_ERROR, "internal error", status_code=500, request=request)


async def _sse_events(events: AsyncIterator, request_id: str) -> AsyncIterator[str]:
    try:
        async for event in events:
            data = json.dumps(event.data, ensure_ascii=False)
            yield f"event: {event.event}\ndata: {data}\n\n"
    except RuntimeApplicationError as exc:
        data = json.dumps(
            {
                "code": RUNTIME_APPLICATION_ERROR,
                "error": exc.code,
                "message": str(exc),
                "request_id": request_id,
            },
            ensure_ascii=False,
        )
        yield f"event: error\ndata: {data}\n\n"


def _context_request_id() -> str:
    context = current_context()
    if context is not None:
        return context.request_id
    return f"req_{uuid4().hex}"
