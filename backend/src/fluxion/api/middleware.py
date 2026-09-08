from __future__ import annotations

from time import perf_counter
from typing import Any
from uuid import uuid4

from fastapi import Request
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from fluxion.api.responses import failure
from fluxion.config import DevModeSettings
from fluxion.errors.console import IDENTITY_HEADER_MISSING, INTERNAL_ERROR
from fluxion.observability.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from fluxion.observability.logging import emit_access_log
from fluxion.observability.tracing import traced_scope


class RequestContextMiddleware:
    """请求上下文中间件（纯 ASGI 透传；asgi-passthrough TASK-001 起不再继承
    Starlette ``BaseHTTPMiddleware``）。

    H1：Console（Control Plane）非 dev 模式强制身份头，缺失即 401 fail-closed，
    不落到 "unknown" 租户（否则匿名请求可与名为 unknown 的真实租户串户）。
    生产由鉴权网关/身份代理在认证后注入 X-Tenant-ID/X-Actor-ID，中间件把该
    假设变成可强制契约。Channel（用户 Chat）传 require_identity=False——/bind
    前置匿名、messages 用 Bearer token + header-tenant（S2 残留另有文档化）。

    与旧实现逐项一致的四职责：身份门禁、RequestContext 绑定、access 日志、
    OTel span。唯一行为差异：响应体透传（来一块发一块），不再攒完再发——
    SSE 真增量直达，首帧不再等整段生成。
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        dev_mode: DevModeSettings | None = None,
        require_identity: bool = True,
    ) -> None:
        self.app = app
        self._dev_mode = dev_mode or DevModeSettings.from_env()
        self._require_identity = require_identity

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive)
        started = perf_counter()
        is_health = request.url.path == "/healthz"
        if self._require_identity and not self._dev_mode.enabled and not is_health:
            missing = _missing_identity_header(request)
            if missing is not None:
                # 预置 request_id：failure() 在未绑定 context 时默认 req_unknown，
                # 显式给拒绝响应一个可关联的 request_id。
                request.state.request_id = _request_id(request.headers.get("X-Request-ID"))
                response = failure(
                    IDENTITY_HEADER_MISSING,
                    f"missing required identity header: {missing}",
                    status_code=401,
                    request=request,
                )
                await response(scope, receive, send)
                return
        tenant_id, actor_id = self._identity(request)
        context = RequestContext(
            request_id=_request_id(request.headers.get("X-Request-ID")),
            trace_id=_trace_id(request.headers.get("X-Trace-ID")),
            tenant_id=tenant_id,
            actor_id=actor_id,
            method=request.method,
            route=request.url.path,
            client_ip=request.client.host if request.client is not None else "unknown",
            user_agent=request.headers.get("User-Agent", ""),
        )
        token = bind_request_context(context)
        request.state.request_id = context.request_id
        request.state.trace_id = context.trace_id
        request.state.tenant_id = context.tenant_id
        request.state.actor_id = context.actor_id
        status_code = 500
        biz_code = INTERNAL_ERROR
        publish_id: str | None = None
        # O501（TASK-008）：HTTP span 经 traced_scope——关联字段 + 脱敏统一入口
        async with traced_scope(
            f"http.{request.method.lower()}.{request.url.path}",
            attributes={
                "http.request.method": request.method,
                "http.route": request.url.path,
                "fluxion.actor_id": context.actor_id,
            },
        ) as span:
            try:
                sender = _ObservingSend(send, context, span)
                await self.app(scope, receive, sender)
                status_code = sender.status_code
                biz_code = sender.biz_code
                publish_id = sender.publish_id
            finally:
                latency_ms = (perf_counter() - started) * 1000
                if not is_health or status_code >= 500:
                    emit_access_log(
                        context,
                        status_code=status_code,
                        biz_code=biz_code,
                        latency_ms=latency_ms,
                        headers=dict(request.headers),
                        query=dict(request.query_params),
                        publish_id=publish_id,
                    )
                reset_request_context(token)

    def _identity(self, request: Request) -> tuple[str, str]:
        if self._dev_mode.enabled:
            return self._dev_mode.tenant_id, self._dev_mode.actor_id
        return (
            _header_or_unknown(request.headers.get("X-Tenant-ID")),
            _header_or_unknown(request.headers.get("X-Actor-ID")),
        )


class _ObservingSend:
    """透传 send：在 ``http.response.start`` 处改响应头并记录观测值。

    body（含 SSE chunk）原样透传，不攒包——这就是与 BaseHTTPMiddleware
    的唯一行为差异。状态码/业务头只从 start 消息取（头在 start 后不变，
    与旧实现读完整响应等价）。
    """

    def __init__(self, send: Send, context: RequestContext, span: Any) -> None:
        self._send = send
        self._context = context
        self._span = span
        self.status_code = 500
        self.biz_code = INTERNAL_ERROR
        self.publish_id: str | None = None

    async def __call__(self, message: Message) -> None:
        if message["type"] == "http.response.start":
            headers = MutableHeaders(raw=message.setdefault("headers", []))
            headers["X-Request-ID"] = self._context.request_id
            headers.setdefault("X-Trace-ID", self._context.trace_id)
            self.status_code = message["status"]
            self.biz_code = _biz_code(message["status"], headers.get("X-Biz-Code"))
            self.publish_id = headers.get("X-Publish-ID")
            self._span.set_attribute("http.response.status_code", self.status_code)
        await self._send(message)


def _request_id(value: str | None) -> str:
    if value is not None and value.strip():
        return value.strip()
    return f"req_{uuid4().hex}"


def _trace_id(value: str | None) -> str:
    if value is not None and value.strip():
        return value.strip()
    return f"trace_{uuid4().hex}"


def _header_or_unknown(value: str | None) -> str:
    if value is not None and value.strip():
        return value.strip()
    return "unknown"


def _missing_identity_header(request: Request) -> str | None:
    """非 dev 模式身份头缺失时返回缺失的头名；全在则返回 None。"""
    for header in ("X-Tenant-ID", "X-Actor-ID"):
        value = request.headers.get(header)
        if value is None or not value.strip():
            return header
    return None


def _biz_code(status_code: int, biz_header: str | None) -> int:
    if biz_header is None:
        return 0 if status_code < 400 else INTERNAL_ERROR
    try:
        return int(biz_header)
    except ValueError:
        return INTERNAL_ERROR
