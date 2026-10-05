"""Execution facts → transient, channel-neutral status and elapsed time."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import suppress
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

import yaml

from .sse import SseEvent


class ProgressPhase(StrEnum):
    PREPARING = "PREPARING"
    THINKING = "THINKING"
    EXECUTING = "EXECUTING"
    WAITING_INPUT = "WAITING_INPUT"
    ACCEPTED = "ACCEPTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ExecutionProgress:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._started: float | None = None
        self._stopped: float | None = None
        self._tools: set[str] = set()
        self._model_active = False
        #: 是否已经开过一次模型调用。**只有**它还是 False 才允许显示 PREPARING：
        #: 执行一旦开始，「准备中」就是倒退（模型结束到工具开始之间会闪一下），
        #: 而且每次闪动都是客户端的一次整帧重排。
        self._model_started = False
        self.phase = ProgressPhase.PREPARING
        self.visible = True

    @property
    def active(self) -> bool:
        return self._started is not None and self._stopped is None

    @property
    def elapsed_seconds(self) -> int:
        if self._started is None:
            return 0
        end = self._stopped if self._stopped is not None else self._clock()
        return max(0, int(end - self._started))

    def apply(self, event: SseEvent) -> bool:
        if self._stopped is not None:
            return False
        previous = (self.phase, self.visible)
        if event.type == "run.created":
            if self._started is None:
                self._started = self._clock() - _event_age(event.timestamp)
        elif event.type in ("model.started", "model.completed"):
            self._model_active = event.type == "model.started"
            self._model_started = self._model_started or self._model_active
            self._execution_phase()
        elif event.type in ("tool.started", "tool.completed"):
            call_id = str(event.data.get("tool_call_id", ""))
            if event.type == "tool.started":
                self._tools.add(call_id)
                self.visible = True
            else:
                self._tools.discard(call_id)
            self._execution_phase()
        elif event.type == "message.delta":
            self.visible = False
        elif event.type in _STOP_PHASES:
            self.phase = _STOP_PHASES[event.type]
            if event.data.get("status") == "CANCELLED":
                self.phase = ProgressPhase.CANCELLED
            self._stopped = self._clock()
            self.visible = not bool(event.data.get("final_text")) and self.visible
        return previous != (self.phase, self.visible) or event.type == "run.created"

    def _execution_phase(self) -> None:
        if self._tools:
            self.phase = ProgressPhase.EXECUTING
        elif self._model_active or self._model_started:
            # 模型调用之间（上一个模型返回、工具还没起）仍然是「思考中」：
            # 只有**从未**开过模型调用才算准备中，否则每过一个模型/工具边界就闪一次「准备中」。
            self.phase = ProgressPhase.THINKING
        else:
            self.phase = ProgressPhase.PREPARING


_STOP_PHASES = {
    "interrupt.required": ProgressPhase.WAITING_INPUT,
    "task.accepted": ProgressPhase.ACCEPTED,
    "run.completed": ProgressPhase.COMPLETED,
    "run.failed": ProgressPhase.FAILED,
}

#: 计时节拍的默认值/下界不再有第二来源：由平台设置 `im.progress_interval_sec` 提供，
#: schema（`muad_contracts.platform_settings`）的 `ge=1.0` 是唯一来源（ADR-04）。


def _event_age(timestamp: str | None) -> float:
    if timestamp is None:
        return 0
    try:
        started = datetime.fromisoformat(timestamp)
    except ValueError:
        return 0
    if started.tzinfo is None:
        return 0
    return max(0, (datetime.now(UTC) - started).total_seconds())


class ActivityMessages:
    def __init__(self, catalog_path: Path, locale: str) -> None:
        raw = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))["activity_messages"]
        self._templates: dict[ProgressPhase, str] = {}
        for phase in ProgressPhase:
            templates = raw[phase.value]
            self._templates[phase] = str(templates.get(locale) or templates["en-US"])

    def render(self, progress: ExecutionProgress) -> str:
        minutes, seconds = divmod(progress.elapsed_seconds, 60)
        return self._templates[progress.phase].format(elapsed=f"{minutes:02d}:{seconds:02d}")


async def iter_with_ticks(
    events: AsyncIterator[SseEvent],
    *,
    interval: float,
) -> AsyncGenerator[SseEvent | None, None]:
    """Keep one pending read; timeouts never cancel an in-flight SSE read."""
    if interval <= 0:
        raise ValueError("progress interval must be positive")
    pending = asyncio.create_task(_read_next(events))
    next_tick = time.monotonic() + interval
    try:
        while True:
            done, _ = await asyncio.wait((pending,), timeout=max(0, next_tick - time.monotonic()))
            if not done:
                next_tick = time.monotonic() + interval
                yield None
                continue
            try:
                event = pending.result()
            except StopAsyncIteration:
                return
            yield event
            pending = asyncio.create_task(_read_next(events))
    finally:
        if not pending.done():
            pending.cancel()
            with suppress(asyncio.CancelledError):
                await pending
        # 按**能力**而不是按类型收上游：真正需要回收的是持有 HTTP 响应的 `async def` 生成器，
        # 但端口声明的是 `AsyncIterator`，换一个非生成器实现就会漏关连接（见 `_read_next`）。
        aclose = getattr(events, "aclose", None)
        if aclose is not None:
            await aclose()


async def _read_next(events: AsyncIterator[SseEvent]) -> SseEvent:
    return await anext(events)
