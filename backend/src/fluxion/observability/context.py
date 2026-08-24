from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RequestContext:
    request_id: str
    trace_id: str
    tenant_id: str
    actor_id: str
    method: str
    route: str
    client_ip: str
    user_agent: str


_REQUEST_CONTEXT: ContextVar[RequestContext | None] = ContextVar(
    "fluxion_request_context",
    default=None,
)


def bind_request_context(context: RequestContext) -> Token[RequestContext | None]:
    return _REQUEST_CONTEXT.set(context)


def reset_request_context(token: Token[RequestContext | None]) -> None:
    _REQUEST_CONTEXT.reset(token)


def current_context() -> RequestContext | None:
    return _REQUEST_CONTEXT.get()
