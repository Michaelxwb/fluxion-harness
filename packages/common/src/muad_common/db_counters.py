"""DB 语句/事务计数：按 service × stage 归因（容量诊断，纯观测）。

- `before_cursor_execute` 数语句；`begin` 数事务（短会话单位）。
- `db_stage` 用 ContextVar 标当前阶段；后台任务默认 `bg:unlabeled`。
- 计数只在进程内累加；由各服务的 metrics 循环发布为 gauge，避免每语句取注册表锁。
- stage 是固定闭集，未知值在计数时兜底为 `bg:unlabeled`（不把 SQL/资源 ID 带进 label）。
"""

from __future__ import annotations

import threading
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from typing import Any, Final

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine

DEFAULT_STAGE: Final = "bg:unlabeled"
STAGES: Final[frozenset[str]] = frozenset(
    {
        "admission",
        "model",
        "event",
        "finalize",
        "turn",
        "turn:history",
        "turn:start",
        "turn:resume",
        "turn:build",
        "turn:segment",
        "turn:claim",
        "poll",
        "console",
        "webchat",
        "internal",
        "api",
        "bg:unlabeled",
        "bg:tail",
        "bg:delivery",
        "bg:lease",
        "bg:retention",
        "bg:capacity",
        "bg:worker",
    }
)

_STAGE: ContextVar[str] = ContextVar("muad_db_stage", default=DEFAULT_STAGE)
_COUNTS: dict[tuple[str, str, str], int] = {}
_LOCK = threading.Lock()

_STAGE_LABEL = "stage"
_SERVICE_LABEL = "service"


def current_stage() -> str:
    return _STAGE.get()


def _normalize(name: str) -> str:
    return name if name in STAGES else DEFAULT_STAGE


@contextmanager
def db_stage(name: str) -> Iterator[None]:
    """在 with 块内把 DB 计数归到 `name`；嵌套时内层覆盖、退出恢复。"""
    token = _STAGE.set(_normalize(name))
    try:
        yield
    finally:
        _STAGE.reset(token)


def db_stage_scope(name: str) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """异步方法装饰器：整个方法体归到 `name`。"""

    def decorate(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            with db_stage(name):
                return await fn(*args, **kwargs)

        return wrapper

    return decorate


def _increment(service: str, stage: str, kind: str) -> None:
    key = (service, _normalize(stage), kind)
    with _LOCK:
        _COUNTS[key] = _COUNTS.get(key, 0) + 1


def install_db_counters(engine: AsyncEngine, *, service: str) -> None:
    """给一个引擎挂语句/事务计数；同一引擎重复调用只挂一次。"""
    sync = engine.sync_engine
    if getattr(sync, "_muad_db_counters", False):
        return
    sync._muad_db_counters = True  # type: ignore[attr-defined]

    def on_begin(_conn: object) -> None:
        _increment(service, current_stage(), "transaction")

    def on_statement(
        _conn: object,
        _cursor: object,
        _statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        _increment(service, current_stage(), "statement")

    event.listen(sync, "begin", on_begin)
    event.listen(sync, "before_cursor_execute", on_statement)


def snapshot_db_counters() -> dict[tuple[str, str, str], int]:
    with _LOCK:
        return dict(_COUNTS)


def reset_db_counters() -> None:
    with _LOCK:
        _COUNTS.clear()


def publish_db_counters(
    record_gauge: Callable[[str, float, dict[str, str] | None], None],
) -> None:
    """把累计值发布成 gauge；由各服务的 metrics 循环调用（不在每语句 hook 里取锁）。"""
    for (service, stage, kind), value in snapshot_db_counters().items():
        name = "db_statements_total" if kind == "statement" else "db_transactions_total"
        record_gauge(name, float(value), {_SERVICE_LABEL: service, _STAGE_LABEL: stage})
