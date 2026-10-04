"""One inbound callback owns one reply stream; status never becomes answer text."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from uuid import uuid4

from ..base import ChannelAdapterUnavailable
from .sdk_port import WeComSdkPort

logger = logging.getLogger(__name__)

#: 状态刷新是**可丢的**：通道慢或挂住时不能把正文顶在后面（设计 §3.4「不能让计时阻塞真实
#: 结果」）。超时即放弃本次刷新并记日志，下一个 tick 会带着最新阶段重来（合并计时更新）。
#: 正文与收尾不走这个上限——它们必须送达。
STATUS_SEND_TIMEOUT_SEC = 5.0


class StatusBudget:
    def __init__(self, rate: float, *, clock: Callable[[], float] = time.monotonic) -> None:
        if rate < 1:
            raise ValueError("status update rate must be at least one per second")
        self._rate = rate
        self._clock = clock
        self._tokens = rate
        self._at = clock()

    def take(self) -> bool:
        now = self._clock()
        self._tokens = min(self._rate, self._tokens + (now - self._at) * self._rate)
        self._at = now
        if self._tokens < 1:
            return False
        self._tokens -= 1
        return True


class ReplySession:
    def __init__(
        self,
        *,
        client: Callable[[], WeComSdkPort],
        reply_ref: str,
        flush_interval_sec: float = 0.5,
        budget: StatusBudget | None = None,
        on_finish: Callable[[], None] | None = None,
        status_timeout_sec: float = STATUS_SEND_TIMEOUT_SEC,
    ) -> None:
        self._client = client
        self._reply_ref = reply_ref
        self._stream_id = uuid4().hex
        self._flush_interval_sec = flush_interval_sec
        self._budget = budget
        self._on_finish = on_finish
        self._status_timeout_sec = status_timeout_sec
        self._body = ""
        self._status = ""
        self._last_flush = 0.0
        self._closed = False
        self._sent = False
        #: 发起本会话的 Run（`bind_run` 注入）。产物交付按 run 找回**本条消息**的回调时用。
        self.run_id: str | None = None

    @property
    def reply_ref(self) -> str:
        return self._reply_ref

    def bind_run(self, run_id: str) -> None:
        self.run_id = run_id

    async def update_status(self, text: str) -> None:
        if self._closed:
            return
        self._status = text
        if self._budget is not None and not self._budget.take():
            return
        content = f"<think>{text}</think>" + ("\n\n" + self._body if self._body else "")
        try:
            async with asyncio.timeout(self._status_timeout_sec):
                await self._flush(content)
        except TimeoutError:
            logger.warning("reply_status_send_timeout reply_ref=%s", self._reply_ref)

    async def stream(self, text: str) -> None:
        if self._closed:
            return
        self._status = ""
        self._body += text
        if time.monotonic() - self._last_flush >= self._flush_interval_sec:
            await self._flush(self._body)

    async def send(self, text: str) -> None:
        if self._closed:
            # 收尾帧已经发过，正文不能再追加。此时退化成**独立会话回复**——与非状态能力渠道
            # 的文本路径同一条命令（`reply_text`），而不是把这条文本静默丢掉。
            try:
                await self._client().reply_text(self._reply_ref, text)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                raise ChannelAdapterUnavailable("reply text send failed") from exc
        else:
            self._status = ""
            self._body += ("\n\n" if self._body else "") + text
            await self._flush(self._body)

    async def finish(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._sent:
                # 收尾只发正文：状态永远活在 `<think>` 占位里，**不进入正文**（SESSION-01）。
                # 本轮没有正文时发空内容——正是 B-402 要的「finish 移除占位」。
                await self._flush(self._body, finish=True)
        finally:
            if self._on_finish is not None:
                self._on_finish()

    async def _flush(self, content: str, *, finish: bool = False) -> None:
        try:
            await self._client().send_stream(self._reply_ref, self._stream_id, content, finish=finish)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise ChannelAdapterUnavailable("reply stream send failed") from exc
        self._last_flush = time.monotonic()
        self._sent = True
