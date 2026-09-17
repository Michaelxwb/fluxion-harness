from __future__ import annotations

from contextvars import ContextVar
from typing import Any

_context: ContextVar[dict[str, Any]] = ContextVar("muad_log_context", default={})


def set_log_context(**values: Any) -> None:
    current = dict(_context.get())
    current.update({key: value for key, value in values.items() if value is not None})
    _context.set(current)


def clear_log_context() -> None:
    _context.set({})


def get_log_context() -> dict[str, Any]:
    return dict(_context.get())
