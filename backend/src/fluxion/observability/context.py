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

# Phase 5 TASK-007：execution_id 关联字段（O502 Runtime execution 由 TASK-008 接线；
# traced_scope 自动读取并挂到 span）。
_EXECUTION_ID: ContextVar[str | None] = ContextVar("fluxion_execution_id", default=None)


def bind_request_context(context: RequestContext) -> Token[RequestContext | None]:
    return _REQUEST_CONTEXT.set(context)


def reset_request_context(token: Token[RequestContext | None]) -> None:
    _REQUEST_CONTEXT.reset(token)


def current_context() -> RequestContext | None:
    return _REQUEST_CONTEXT.get()


def bind_execution_id(execution_id: str) -> Token[str | None]:
    return _EXECUTION_ID.set(execution_id)


def reset_execution_id(token: Token[str | None]) -> None:
    _EXECUTION_ID.reset(token)


def current_execution_id() -> str | None:
    return _EXECUTION_ID.get()
