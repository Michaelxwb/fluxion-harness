from contextvars import ContextVar

_locale: ContextVar[str] = ContextVar("muad_locale", default="zh-CN")
_trace_id: ContextVar[str] = ContextVar("muad_trace_id", default="")
_request_id: ContextVar[str] = ContextVar("muad_request_id", default="")
_tenant_id: ContextVar[str] = ContextVar("muad_tenant_id", default="")
_caller_service: ContextVar[str] = ContextVar("muad_caller_service", default="")


def set_request_context(
    *,
    locale: str,
    trace_id: str,
    request_id: str,
    tenant_id: str = "",
    caller_service: str = "",
) -> None:
    _locale.set(locale)
    _trace_id.set(trace_id)
    _request_id.set(request_id)
    _tenant_id.set(tenant_id)
    _caller_service.set(caller_service)


def current_locale() -> str:
    return _locale.get()


def current_trace_id() -> str:
    return _trace_id.get()


def current_request_id() -> str:
    return _request_id.get()


def current_tenant_id() -> str:
    return _tenant_id.get()


def current_caller_service() -> str:
    return _caller_service.get()
