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

#: 终帧的有界重试：一次瞬时断线不该让这条回复永远停在未收尾状态（客户端一直挂着占位）。
#: 重试是**同一个 stream_id + 同一段正文**，客户端按最后一次收到的那帧渲染，重复不会叠加。
FINISH_ATTEMPTS = 3
FINISH_RETRY_DELAY_SEC = 0.5


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
        on_run_bound: Callable[[str], None] | None = None,
        status_timeout_sec: float = STATUS_SEND_TIMEOUT_SEC,
        finish_attempts: int = FINISH_ATTEMPTS,
        finish_retry_delay_sec: float = FINISH_RETRY_DELAY_SEC,
    ) -> None:
        self._client = client
        self._reply_ref = reply_ref
        self._stream_id = uuid4().hex
        self._flush_interval_sec = flush_interval_sec
        self._budget = budget
        self._on_finish = on_finish
        self._on_run_bound = on_run_bound
        self._status_timeout_sec = status_timeout_sec
        self._finish_attempts = max(1, finish_attempts)
        self._finish_retry_delay_sec = finish_retry_delay_sec
        self._body = ""
        #: 最近一次真的发出去的状态帧内容（含占位与当时的正文）：同内容不再重复发。
        self._status_sent: str | None = None
        self._last_flush = 0.0
        self._closed = False
        self._closing = False
        self._sent = False
        #: 发起本会话的 Run（`bind_run` 注入）。产物交付按 run 找回**本条消息**的回调时用。
        self.run_id: str | None = None

    @property
    def reply_ref(self) -> str:
        return self._reply_ref

    def bind_run(self, run_id: str) -> None:
        self.run_id = run_id
        if self._on_run_bound is not None:
            self._on_run_bound(run_id)

    async def update_status(self, text: str) -> None:
        if self._closed or self._closing:
            return
        content = f"<think>{text}</think>" + ("\n\n" + self._body if self._body else "")
        if content == self._status_sent:
            # 一字不差的重复帧 = 客户端白重排一次、白滚动一次（实测 run.created 会紧跟着
            # 起始帧再发一遍同样的「准备中」）。额度也不该为它花掉。
            return
        if self._budget is not None and not self._budget.take():
            return
        try:
            async with asyncio.timeout(self._status_timeout_sec):
                await self._flush(content)
        except TimeoutError:
            logger.warning("reply_status_send_timeout reply_ref=%s", self._reply_ref)
        else:
            self._status_sent = content

    async def stream(self, text: str) -> None:
        if self._closed or self._closing:
            return
        self._body += text
        if time.monotonic() - self._last_flush >= self._flush_interval_sec:
            await self._flush(self._body)

    async def send(self, text: str) -> None:
        if self._closed:
            # 收尾帧已经发过，正文不能再追加。此时退化成**独立会话回复**——与非状态能力渠道
            # 的文本路径同一条命令（`reply_text`），而不是把这条文本静默丢掉。
            await self.reply_once(text)
            return
        self._body += ("\n\n" if self._body else "") + text
        await self._flush(self._body)

    async def reply_once(self, text: str) -> None:
        """发一条**独立、已收尾**的文本（命令回执 / 附件回执 / 错误文案）。

        这一类文本没有后续，客户端应当**一次**收到完整内容：走 `reply_text`（内部就是
        `finish=True` 的收尾帧），而不是"先发一帧未收尾的正文、再补一帧收尾"。
        """
        try:
            await self._client().reply_text(self._reply_ref, text)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise ChannelAdapterUnavailable("reply text send failed") from exc

    async def finish(self) -> None:
        """收尾。**只有终帧真的发出去了才关闭**（2026-10-06 修）。

        此前是"先置 `_closed=True` 再发终帧"，发送失败后会话已关，后续任何 `finish()` 都直接
        返回 —— 一次瞬时断线就让这条回复**永远**停在未收尾状态（客户端一直挂着占位），而且
        没有任何补偿路径。现在：失败保留会话、有界重试，重试仍不成功才放弃并留 ERROR 痕迹。
        """
        if self._closed or self._closing:
            return
        if not self._sent:
            # 本轮没有正文也无所谓：`finish` 的语义就是"收尾"（含移除占位），无需发帧。
            self._closed = True
            self._notify_finish()
            return
        self._closing = True
        try:
            for attempt in range(self._finish_attempts):
                try:
                    await self._flush(self._body, finish=True)
                except ChannelAdapterUnavailable:
                    if attempt + 1 < self._finish_attempts:
                        await asyncio.sleep(self._finish_retry_delay_sec)
                        continue
                    logger.error("reply_finish_failed reply_ref=%s attempts=%s", self._reply_ref, attempt + 1)
                    raise
                self._closed = True
                self._notify_finish()
                return
        finally:
            self._closing = False

    def _notify_finish(self) -> None:
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
