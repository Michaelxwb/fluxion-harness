from __future__ import annotations

import json
import traceback
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from fluxion.api.middleware import RequestContextMiddleware
from fluxion.api.responses import failure, success
from fluxion.errors.console import (
    INTERNAL_ERROR,
    RESOURCE_NOT_FOUND,
    RUNTIME_APPLICATION_ERROR,
    VALIDATION_FAILED,
)
from fluxion.observability.context import current_context
from fluxion.observability.logging import emit_error_log
from fluxion.services.runtime_app import (
    RunRuntimeRequest,
    RuntimeApplicationError,
    RuntimeApplicationService,
    ToolCallRequest,
)


class ToolCallPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_id: str
    arguments: dict[str, object] = Field(default_factory=dict)


class RunPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    tenant_id: str
    user_id: str
    session_id: str
    input_message: str = Field(alias="input")
    runtime_profile_version_selector: str = "latest-published"
    agent_definition_id: str | None = None
    tool_calls: list[ToolCallPayload] = Field(default_factory=list)


def create_app(service: RuntimeApplicationService) -> FastAPI:
    @asynccontextmanager
    async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # A15：initialize 必须在 serving 事件循环内执行——此前 cli serve（非 dev）
        # 用 asyncio.run(service.initialize()) 起一个临时 loop 初始化，aiosqlite
        # 连接绑回该 loop 后关闭，随后 uvicorn 新 loop 复用池中连接 → "Future
        # attached to a different loop"。改为 lifespan 在 uvicorn loop 内初始化
        # （与 dev bundle 一致）。httpx ASGITransport 不触发 lifespan，测试仍手动
        # initialize，无双重初始化。
        await service.initialize()
        outbox_worker = service.build_outbox_worker()
        outbox_worker.start()
        try:
            yield
        finally:
            await outbox_worker.stop()
            await service.close()

    app = FastAPI(title="Fluxion Runtime API", lifespan=_lifespan)
    # H1：Execution 入口信任链在 body tenant（RunRuntimeRequest.tenant_id）+ 调用方
    # 凭据，非 header-identity 模型；不强制 X-Tenant-ID/X-Actor-ID。
    app.add_middleware(RequestContextMiddleware, require_identity=False)
    _register_error_handlers(app)

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        health = await service.health()
        return success(health.to_payload())

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        ready = await service.ready()
        return success(ready.to_payload())

    @app.get("/health")
    async def health_alias() -> JSONResponse:
        return await healthz()

    @app.get("/ready")
    async def ready_alias() -> JSONResponse:
        return await readyz()

    @app.post("/api/v1/runtime-profiles/{runtime_profile_id}/runs")
    async def run_profile(
        runtime_profile_id: str,
        payload: RunPayload,
        x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-ID")] = None,
    ) -> JSONResponse:
        request_id = _context_request_id()
        result = await service.run(
            _run_request(runtime_profile_id, payload, request_id, x_tenant_id)
        )
        return success(result.to_payload())

    @app.post("/api/v1/runtime-profiles/{runtime_profile_id}/runs:stream")
    async def stream_profile(
        runtime_profile_id: str,
        payload: RunPayload,
        x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-ID")] = None,
    ) -> StreamingResponse:
        request_id = _context_request_id()
        events = _sse_events(
            service,
            _run_request(runtime_profile_id, payload, request_id, x_tenant_id),
        )
        return StreamingResponse(events, media_type="text/event-stream")

    return app


def _register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RuntimeApplicationError)
    async def runtime_error_handler(
        request: Request, exc: RuntimeApplicationError
    ) -> JSONResponse:
        # RuntimeApplicationError.code 是字符串 slug（如 resource_version_not_found），
        # 统一映射到 RUNTIME_APPLICATION_ERROR 整数码，slug 保留在 message 中追溯，
        # 不再回传字符串 code——与 Console 共用 responses.failure 整数码契约对齐。
        return failure(
            RUNTIME_APPLICATION_ERROR,
            f"{exc.code}: {exc}",
            status_code=exc.status_code,
            request=request,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        del exc
        return failure(
            VALIDATION_FAILED, "validation failed", status_code=400, request=request
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        # 路由级 404/405 等 HTTPException 需回到统一 envelope，而不是落到
        # 通用 Exception handler 变成 500 INTERNAL_ERROR。
        if exc.status_code == 404:
            return failure(
                RESOURCE_NOT_FOUND, "not found", status_code=404, request=request
            )
        return failure(
            VALIDATION_FAILED,
            str(exc.detail),
            status_code=exc.status_code,
            request=request,
        )

    @app.exception_handler(Exception)
    async def internal_error_handler(request: Request, exc: Exception) -> JSONResponse:
        # 与 Console API 对齐：未捕获异常必须回到统一 envelope，而不是裸 500 文本。
        context = current_context()
        emit_error_log(
            request_id=context.request_id if context is not None else "unknown",
            trace_id=context.trace_id if context is not None else "unknown",
            tenant_id=context.tenant_id if context is not None else "unknown",
            actor_id=context.actor_id if context is not None else "unknown",
            method=request.method,
            route=request.url.path,
            error_type=type(exc).__name__,
            error_code=INTERNAL_ERROR,
            stack=traceback.format_exc(),
        )
        return failure(
            INTERNAL_ERROR, "internal error", status_code=500, request=request
        )


def _run_request(
    runtime_profile_id: str,
    payload: RunPayload,
    request_id: str,
    x_tenant_id: str | None = None,
) -> RunRuntimeRequest:
    return RunRuntimeRequest(
        tenant_id=_tenant_id(x_tenant_id, payload.tenant_id),
        user_id=payload.user_id,
        runtime_profile_id=runtime_profile_id,
        session_id=payload.session_id,
        input_message=payload.input_message,
        runtime_profile_version_selector=payload.runtime_profile_version_selector,
        agent_definition_id=payload.agent_definition_id,
        request_id=request_id,
        tool_calls=[
            ToolCallRequest(tool_id=call.tool_id, arguments=call.arguments)
            for call in payload.tool_calls
        ],
    )


async def _sse_events(
    service: RuntimeApplicationService,
    request: RunRuntimeRequest,
) -> AsyncIterator[str]:
    try:
        async for event in service.stream(request):
            data = json.dumps(event.data, ensure_ascii=False)
            yield f"event: {event.event}\ndata: {data}\n\n"
    except RuntimeApplicationError as exc:
        # SSE error 帧同样用整数码（与 HTTP envelope 一致），slug 保留在 error 字段。
        data = json.dumps(
            {
                "code": RUNTIME_APPLICATION_ERROR,
                "error": exc.code,
                "message": str(exc),
                "request_id": request.request_id,
            },
            ensure_ascii=False,
        )
        yield f"event: error\ndata: {data}\n\n"


def _context_request_id() -> str:
    context = current_context()
    if context is not None:
        return context.request_id
    return f"req_{uuid4().hex}"


def _tenant_id(header: str | None, body: str) -> str:
    if header is not None and header.strip():
        return header.strip()
    return body
