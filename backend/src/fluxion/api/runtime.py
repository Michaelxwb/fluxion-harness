from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, Header, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from fluxion.api.middleware import RequestContextMiddleware
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
    tool_calls: list[ToolCallPayload] = Field(default_factory=list)


def create_app(service: RuntimeApplicationService) -> FastAPI:
    app = FastAPI(title="Fluxion Runtime API")
    app.add_middleware(RequestContextMiddleware)

    @app.exception_handler(RuntimeApplicationError)
    async def runtime_error_handler(request: Request, exc: RuntimeApplicationError) -> JSONResponse:
        request_id = _request_id(request.headers.get("X-Request-ID"))
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(exc.code, str(exc), None, request_id),
            headers={"X-Request-ID": request_id},
        )

    @app.get("/healthz")
    async def healthz(
        response: Response,
        x_request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
    ) -> dict[str, object]:
        request_id = _request_id(x_request_id)
        response.headers["X-Request-ID"] = request_id
        health = await service.health()
        return _envelope("ok", "ok", health.to_payload(), request_id)

    @app.get("/readyz")
    async def readyz(
        response: Response,
        x_request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
    ) -> dict[str, object]:
        request_id = _request_id(x_request_id)
        response.headers["X-Request-ID"] = request_id
        ready = await service.ready()
        return _envelope("ok", "ok", ready.to_payload(), request_id)

    @app.get("/health")
    async def health_alias(
        response: Response,
        x_request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
    ) -> dict[str, object]:
        return await healthz(response, x_request_id)

    @app.get("/ready")
    async def ready_alias(
        response: Response,
        x_request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
    ) -> dict[str, object]:
        return await readyz(response, x_request_id)

    @app.post("/api/v1/runtime-profiles/{runtime_profile_id}/runs")
    async def run_profile(
        runtime_profile_id: str,
        payload: RunPayload,
        response: Response,
        x_request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
        x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-ID")] = None,
    ) -> dict[str, object]:
        request_id = _request_id(x_request_id)
        response.headers["X-Request-ID"] = request_id
        result = await service.run(_run_request(runtime_profile_id, payload, request_id, x_tenant_id))
        return _envelope("ok", "ok", result.to_payload(), request_id)

    @app.post("/api/v1/runtime-profiles/{runtime_profile_id}/runs:stream")
    async def stream_profile(
        runtime_profile_id: str,
        payload: RunPayload,
        x_request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
        x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-ID")] = None,
    ) -> StreamingResponse:
        request_id = _request_id(x_request_id)
        events = _sse_events(
            service,
            _run_request(runtime_profile_id, payload, request_id, x_tenant_id),
        )
        return StreamingResponse(
            events,
            media_type="text/event-stream",
            headers={"X-Request-ID": request_id},
        )

    return app


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
        data = json.dumps(
            {
                "code": exc.code,
                "message": str(exc),
                "request_id": request.request_id,
            },
            ensure_ascii=False,
        )
        yield f"event: error\ndata: {data}\n\n"


def _request_id(value: str | None) -> str:
    if value is not None and value.strip():
        return value.strip()
    return f"req_{uuid4().hex}"


def _tenant_id(header: str | None, body: str) -> str:
    if header is not None and header.strip():
        return header.strip()
    return body


def _envelope(
    code: str,
    message: str,
    data: dict[str, object] | None,
    request_id: str,
) -> dict[str, object]:
    return {
        "code": code,
        "message": message,
        "data": data,
        "request_id": request_id,
    }
