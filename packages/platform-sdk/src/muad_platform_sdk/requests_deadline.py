"""Bound native requests timeouts by the host execution deadline."""
from __future__ import annotations

import math

from urllib3.exceptions import TimeoutStateError
from urllib3.util import Timeout


def _number(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("requests timeout must be a positive number or None")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError("requests timeout must be finite and positive")
    return result


def bounded_timeout(value: object, remaining: float) -> Timeout:
    if remaining <= 0:
        raise TimeoutError("execution deadline exceeded")
    total: float | None = None
    if isinstance(value, Timeout):
        total = _number(value.total)
        connect = value.connect_timeout
        try:
            read = value.read_timeout
        except TimeoutStateError:
            read = None  # Native total budget has not started connecting yet.
        connect = connect if isinstance(connect, (int, float)) else None
    elif isinstance(value, tuple) and len(value) == 2:
        connect, read = value
    else:
        connect = read = _number(value)
    connect_value, read_value = _number(connect), _number(read)
    return Timeout(total=min(remaining, total) if total is not None else remaining,
                   connect=min(remaining, connect_value) if connect_value is not None else remaining,
                   read=min(remaining, read_value) if read_value is not None else remaining)


def deadline_arguments(args: tuple[object, ...], kwargs: dict[str, object],
                       remaining: float) -> tuple[tuple[object, ...], dict[str, object]]:
    result = dict(kwargs)
    if len(args) > 1:
        return (args[0], bounded_timeout(args[1], remaining), *args[2:]), result
    result["timeout"] = bounded_timeout(result.get("timeout"), remaining)
    return args, result
