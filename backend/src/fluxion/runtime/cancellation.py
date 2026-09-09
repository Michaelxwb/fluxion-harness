"""协作式取消令牌（设计 §11.6）：/stop 的执行边界检测手段。

Redis/task 取消负责"中断正在进行的 await"；令牌负责"不再开始下一段工作"。
两者缺一不可：前者在 I/O 等待中生效，后者在 await 间隙生效。

令牌触发后，Agent Loop 检查点抛 `ExecutionCancelledError`
（`asyncio.CancelledError` 子类——复用现有 CANCELLED 终态映射与清理链，
不另建取消终态实现）。
"""

from __future__ import annotations

import asyncio


class ExecutionCancelledError(asyncio.CancelledError):
    """协作式取消（/stop 令牌触发），语义等同外部取消。"""

    def __init__(self, reason: str = "stop_requested") -> None:
        self.reason = reason
        super().__init__(reason)


class CancellationToken:
    """单次执行的取消令牌（常驻内存，随执行结束释放，不做事实源）。"""

    def __init__(self) -> None:
        self._cancelled = False
        self._reason = ""

    def cancel(self, reason: str = "stop_requested") -> None:
        self._cancelled = True
        self._reason = reason

    def is_cancelled(self) -> bool:
        return self._cancelled

    def raise_if_cancelled(self) -> None:
        if self._cancelled:
            raise ExecutionCancelledError(self._reason or "stop_requested")
