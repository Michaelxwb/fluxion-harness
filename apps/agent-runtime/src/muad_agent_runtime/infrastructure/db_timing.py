"""数据库连接等待/持有的分段计时（容量瓶颈定位；不改变连接语义）。

两段样本分别回答两个问题：

- **取连接的等待**：`QueuePool` 的一次 `_pool.get(wait, timeout)` 就是全部排队。SQLAlchemy
  公开的 `checkout` 事件在连接**已经取到**之后才触发，等待只能在 `_do_get` 处测，故用
  `AsyncAdaptedQueuePool` 子类覆盖（私有方法，但语义稳定、只在最外层记一次样本）。
- **持有时间**：`checkout` → `checkin` 的墙钟。持有时间远大于语句时间的部分就是
  "连接在手但没在跑 SQL"（idle-in-transaction 的应用侧对应物）；PG 侧另由
  `pg_stat_activity` 采样对账。

label 只有闭集，不含连接/事务/资源 ID。
"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.orm import Session
from sqlalchemy.pool import AsyncAdaptedQueuePool
from sqlalchemy.pool.base import ConnectionPoolEntry

from ..metrics import (
    DB_COMMIT_SECONDS,
    DB_CONNECTION_HOLD_SECONDS,
    DB_POOL_CHECKOUT_SECONDS,
    record_latency,
)

_CHECKOUT_STARTED_KEY = "muad_checkout_started"
_COMMIT_STARTED_KEY = "muad_commit_started"
_SESSION_TIMING_INSTALLED = False


class TimedAsyncAdaptedQueuePool(AsyncAdaptedQueuePool):
    """在取连接处记录等待时长（含溢出重试的整段等待，只记最外层一次）。"""

    def _do_get(self) -> ConnectionPoolEntry:
        if getattr(self, "_muad_timing", False):
            # 溢出重试的递归调用：整段等待由最外层记录，内层不再重复记样本。
            return super()._do_get()
        self._muad_timing = True
        started = time.monotonic()
        try:
            return super()._do_get()
        finally:
            self._muad_timing = False
            record_latency(DB_POOL_CHECKOUT_SECONDS, time.monotonic() - started)


def _on_checkout(
    dbapi_connection: Any, connection_record: ConnectionPoolEntry, connection_proxy: Any
) -> None:
    connection_record.info[_CHECKOUT_STARTED_KEY] = time.monotonic()


def _on_checkin(dbapi_connection: Any, connection_record: ConnectionPoolEntry) -> None:
    started = connection_record.info.pop(_CHECKOUT_STARTED_KEY, None)
    if started is not None:
        record_latency(DB_CONNECTION_HOLD_SECONDS, time.monotonic() - started)


def _on_before_commit(session: Session) -> None:
    session.info[_COMMIT_STARTED_KEY] = time.monotonic()


def _on_after_commit(session: Session) -> None:
    started = session.info.pop(_COMMIT_STARTED_KEY, None)
    if started is not None:
        record_latency(DB_COMMIT_SECONDS, time.monotonic() - started)


def _install_session_timing() -> None:
    """全局 Session 事件只挂一次：commit 墙钟（含 flush）单独成族。"""
    global _SESSION_TIMING_INSTALLED
    if _SESSION_TIMING_INSTALLED:
        return
    event.listen(Session, "before_commit", _on_before_commit)
    event.listen(Session, "after_commit", _on_after_commit)
    _SESSION_TIMING_INSTALLED = True


def install_db_timing(engine: AsyncEngine) -> None:
    """把持有时间监听器挂到真实连接池上（每引擎一次；重复挂会重复计数）。"""
    pool = engine.sync_engine.pool
    event.listen(pool, "checkout", _on_checkout)
    event.listen(pool, "checkin", _on_checkin)
    _install_session_timing()
