"""Process-owned control sender; suspended runs never own its HTTP client."""

from .control_dispatcher import ControlDispatcher

_dispatcher: ControlDispatcher | None = None


def set_control_dispatcher(dispatcher: ControlDispatcher | None) -> None:
    global _dispatcher
    _dispatcher = dispatcher


def get_control_dispatcher() -> ControlDispatcher:
    if _dispatcher is None:
        raise RuntimeError("Runtime control dispatcher has not started")
    return _dispatcher
