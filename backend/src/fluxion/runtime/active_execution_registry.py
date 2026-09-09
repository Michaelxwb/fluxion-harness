"""ActiveExecutionRegistry：本 Pod 当前执行 task/token 索引（设计 §11.5）。

只用于立即取消本实例任务，不作为事实源（PostgreSQL 才是）。
随执行结束注销，不跨执行泄漏。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from fluxion.runtime.cancellation import CancellationToken


@dataclass(slots=True)
class ActiveExecutionEntry:
    task: asyncio.Task[object]
    token: CancellationToken


class ActiveExecutionRegistry:
    """execution_id → (task, token)。单 Pod 内存索引，线程安全由事件循环保证。"""

    def __init__(self) -> None:
        self._entries: dict[str, ActiveExecutionEntry] = {}

    def register(self, execution_id: str, task: asyncio.Task[object], token: CancellationToken) -> None:
        self._entries[execution_id] = ActiveExecutionEntry(task=task, token=token)

    def unregister(self, execution_id: str) -> None:
        self._entries.pop(execution_id, None)

    def get(self, execution_id: str) -> ActiveExecutionEntry | None:
        return self._entries.get(execution_id)

    def cancel_local(self, execution_id: str, reason: str = "stop_requested") -> bool:
        """协作 + 抢占双取消：先令牌（阻新工作），再 task.cancel（断等待）。

        返回 False 表示本实例无此执行（他 Pod 或已结束，调用方按 PG 态处理）。
        """
        entry = self._entries.get(execution_id)
        if entry is None:
            return False
        entry.token.cancel(reason)
        if not entry.task.done():
            entry.task.cancel()
        return True
