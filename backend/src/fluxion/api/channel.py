from __future__ import annotations

import json
import traceback
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict

from fluxion.api.middleware import RequestContextMiddleware
from fluxion.api.responses import failure, success
from fluxion.config import DevModeSettings
from fluxion.errors.console import (
    CHANNEL_ACCESS_DENIED,
    CHANNEL_BIND_FAILED,
    CHANNEL_VALIDATION_FAILED,
    INTERNAL_ERROR,
)
from fluxion.observability.context import current_context
from fluxion.observability.logging import emit_error_log
from fluxion.plugins.channel_adapters import WebChannelAdapter
from fluxion.protocols.channel import ExternalChannelMessage
from fluxion.services.channel_app import (
    ChannelAccessError,
    ChannelApplicationService,
    ChannelBindError,
)


class ChannelMessagePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel_user_id: str
    conversation_id: str
    message_id: str
    content: str
    agent_id: str


class ChatAccessMessagePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str
    message_id: str
    content: str


def create_app(
    service: ChannelApplicationService,
    *,
    dev_mode: DevModeSettings | None = None,
) -> FastAPI:
    app = FastAPI(title="Fluxion Channel API")
    # H1：chat 面鉴权在 Bearer token / bind 层；/bind 前置匿名无身份头，
    # 故不强制 X-Tenant-ID/X-Actor-ID（messages 依赖 header-tenant 属 S2 已文档残留）。
    app.add_middleware(
        RequestContextMiddleware, dev_mode=dev_mode, require_identity=False
    )
    _register_errors(app)
    # S2 残留：/channels/web/messages 对已绑定 channel_user_id 逐消息信任、不重新
    # 鉴权 → 可冒充任意已绑定用户。真正收口需引入真实认证中间件（S1，per-message
    # token），属较大功能构建而非最小修复。dev_mode 门控会破坏该端点的多租户
    # header-tenant 设计（golden-path 契约依赖 X-Tenant-ID），且 dev bundle 已置
    # dev_mode、门控对实际部署无增益，故不采用。S2 随 S1 一并落地。
    _register_message(app, service)
    _register_stream(app, service)
    _register_access_routes(app, service)
    return app


def _register_errors(app: FastAPI) -> None:
    @app.exception_handler(ChannelBindError)
    async def bind_error(request: Request, exc: ChannelBindError) -> JSONResponse:
        del exc
        return failure(
            CHANNEL_BIND_FAILED,
            "绑定码无效或已过期",
            status_code=400,
            request=request,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        del exc
        return failure(
            CHANNEL_VALIDATION_FAILED,
            "请求参数无效",
            status_code=400,
            request=request,
        )

    @app.exception_handler(ChannelAccessError)
    async def access_error(request: Request, exc: ChannelAccessError) -> JSONResponse:
        del exc
        return failure(
            CHANNEL_ACCESS_DENIED,
            "Chat 访问链接无效或已撤销",
            status_code=401,
            request=request,
        )

    @app.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception) -> JSONResponse:
        # 与 Console API 对齐：未捕获异常必须回到统一 envelope，而不是裸 500 文本。
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
        return failure(INTERNAL_ERROR, "internal error", status_code=500, request=request)


def _register_message(app: FastAPI, service: ChannelApplicationService) -> None:
    @app.post("/api/v1/channels/web/messages")
    async def post_message(
        payload: ChannelMessagePayload,
        x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-ID")] = None,
    ) -> JSONResponse:
        result = await service.handle(WebChannelAdapter(), _external(payload, x_tenant_id))
        return success(result.to_payload())


def _register_stream(app: FastAPI, service: ChannelApplicationService) -> None:
    @app.post("/api/v1/channels/web/messages:stream")
    async def stream_message(
        payload: ChannelMessagePayload,
        x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-ID")] = None,
    ) -> StreamingResponse:
        events = _events(service, _external(payload, x_tenant_id))
        return StreamingResponse(events, media_type="text/event-stream")


def _register_access_routes(app: FastAPI, service: ChannelApplicationService) -> None:
    @app.get("/api/v1/channels/web/access")
    async def resolve_access(
        authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    ) -> JSONResponse:
        access = await service.resolve_chat_access(_bearer_token(authorization))
        return success(
            {
                "access_id": access.access_id,
                "tenant_id": access.tenant_id,
                "platform_user_id": access.platform_user_id,
                "agent_id": access.agent_id,
            }
        )

    @app.post("/api/v1/channels/web/access/messages")
    async def post_access_message(
        payload: ChatAccessMessagePayload,
        authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    ) -> JSONResponse:
        request_id, trace_id = _request_ids(payload.message_id)
        result = await service.handle_chat_access(
            _bearer_token(authorization),
            conversation_id=payload.conversation_id,
            content=payload.content,
            request_id=request_id,
            trace_id=trace_id,
        )
        return success(result.to_payload())

    @app.post("/api/v1/channels/web/access/messages:stream")
    async def stream_access_message(
        payload: ChatAccessMessagePayload,
        authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    ) -> StreamingResponse:
        events = _access_events(service, payload, _bearer_token(authorization))
        return StreamingResponse(events, media_type="text/event-stream")


async def _events(
    service: ChannelApplicationService, message: ExternalChannelMessage
) -> AsyncIterator[str]:
    yield _event("started", {"request_id": message.request_id, "message_id": message.message_id})
    try:
        result = await service.handle(WebChannelAdapter(), message)
        yield _event("completed", result.to_payload())
    except ChannelBindError:
        yield _event(
            "error",
            {
                "code": CHANNEL_BIND_FAILED,
                "message": "绑定码无效或已过期",
                "request_id": message.request_id,
                "trace_id": message.trace_id,
            },
        )
    except Exception:  # noqa: BLE001 - SSE 流内异常必须产出 error 帧，不能截断连接
        yield _event(
            "error",
            {
                "code": INTERNAL_ERROR,
                "message": "internal error",
                "request_id": message.request_id,
                "trace_id": message.trace_id,
            },
        )


async def _access_events(
    service: ChannelApplicationService,
    payload: ChatAccessMessagePayload,
    token: str,
) -> AsyncIterator[str]:
    request_id, trace_id = _request_ids(payload.message_id)
    try:
        async for event in service.stream_chat_access(
            token,
            conversation_id=payload.conversation_id,
            content=payload.content,
            request_id=request_id,
            trace_id=trace_id,
        ):
            yield _event(event.event, event.data)
    except ChannelAccessError:
        yield _event(
            "error",
            {
                "code": CHANNEL_ACCESS_DENIED,
                "message": "Chat 访问链接无效或已撤销",
                "request_id": request_id,
                "trace_id": trace_id,
            },
        )
    except Exception:  # noqa: BLE001 - SSE 流内异常必须产出 error 帧，不能截断连接
        yield _event(
            "error",
            {
                "code": INTERNAL_ERROR,
                "message": "internal error",
                "request_id": request_id,
                "trace_id": trace_id,
            },
        )


def _event(name: str, data: dict[str, object]) -> str:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _external(payload: ChannelMessagePayload, tenant_header: str | None) -> ExternalChannelMessage:
    context = current_context()
    tenant_id = context.tenant_id if context is not None else (tenant_header or "unknown")
    return ExternalChannelMessage(
        tenant_id=tenant_id,
        channel_user_id=payload.channel_user_id,
        conversation_id=payload.conversation_id,
        message_id=payload.message_id,
        content=payload.content,
        agent_id=payload.agent_id,
        request_id=context.request_id if context is not None else payload.message_id,
        trace_id=context.trace_id if context is not None else payload.message_id,
    )


def _bearer_token(authorization: str | None) -> str:
    if authorization is None:
        raise ChannelAccessError()
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token.strip():
        raise ChannelAccessError()
    return token.strip()


def _request_ids(fallback: str) -> tuple[str, str]:
    context = current_context()
    if context is None:
        return fallback, fallback
    return context.request_id, context.trace_id


def _state_or_header(request: Request, state_key: str, header_name: str) -> str:
    value = getattr(request.state, state_key, None)
    if isinstance(value, str) and value:
        return value
    return request.headers.get(header_name, "unknown")


def _state_or_unknown(request: Request, state_key: str) -> str:
    value = getattr(request.state, state_key, None)
    return value if isinstance(value, str) and value else "unknown"
