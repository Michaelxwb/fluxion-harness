from contextvars import ContextVar

_locale: ContextVar[str] = ContextVar("muad_locale", default="zh-CN")
_trace_id: ContextVar[str] = ContextVar("muad_trace_id", default="")
_request_id: ContextVar[str] = ContextVar("muad_request_id", default="")


def set_request_context(*, locale: str, trace_id: str, request_id: str) -> None:
    _locale.set(locale)
    _trace_id.set(trace_id)
    _request_id.set(request_id)


def current_locale() -> str:
    return _locale.get()


def current_trace_id() -> str:
    return _trace_id.get()


def current_request_id() -> str:
    return _request_id.get()
