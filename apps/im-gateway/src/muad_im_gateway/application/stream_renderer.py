"""Runtime SSE 事件 → IM 出站动作（design §3.4.1 SSE 事件处理 / §3.4.2 文案映射）。

规则：
- `message.delta` 按 SDK 最小发送间隔节流合并（时间维度，不按字符数硬切）；
- `interrupt.required` 先 flush 已缓冲文本，再输出 prompt 与 options；
- `task.accepted` / `run.completed` / `run.failed` 只 finalize 一次；
- `run.completed(status=CANCELLED)` 显示已停止；`run.failed(RUN_ABANDONED)` 用终态文案；
- `artifact.created` 只给摘要（不提供 IM 下载入口），不展示内部 Tool/Secret。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from muad_api.error_codes import ErrorCode

from .sse import SseEvent

logger = logging.getLogger(__name__)

DELTA_FLUSH_INTERVAL_SEC = 0.5
CANCELLED_STATUS = "CANCELLED"
RUN_ABANDONED_CODE = "RUN_ABANDONED"
STOPPED_TEXT = "当前任务已停止"
BROKEN_STREAM_TEXT = "服务暂时中断，请重发消息"
TASK_ACCEPTED_TEXT = "任务已受理，完成后会通知你"

STREAM_KIND = "stream"
TEXT_KIND = "text"
FINALIZE_KIND = "finalize"


@dataclass(frozen=True)
class RenderAction:
    """渲染结果：由调用方落到渠道适配器（stream / text / finalize）。"""

    kind: str
    text: str = ""


class StreamRenderer:
    """把一次 Run 的 SSE 事件序列渲染成有序出站动作（有状态、可单测）。"""

    def __init__(
        self,
        *,
        flush_interval_sec: float = DELTA_FLUSH_INTERVAL_SEC,
        clock: Callable[[], float] = time.monotonic,
        message_for: Callable[[str], str] | None = None,
    ) -> None:
        self._flush_interval_sec = flush_interval_sec
        self._clock = clock
        self._message_for = message_for
        self._buffer: list[str] = []
        self._last_flush_at = 0.0
        self._has_output = False
        self._finalized = False
        self.awaiting_input = False

    @property
    def has_output(self) -> bool:
        return self._has_output

    @property
    def finalized(self) -> bool:
        return self._finalized

    def apply(self, event: SseEvent) -> tuple[RenderAction, ...]:
        handlers = {
            "message.delta": self._on_delta,
            "interrupt.required": self._on_interrupt,
            "task.accepted": self._on_task_accepted,
            "artifact.created": self._on_artifact,
            "run.completed": self._on_completed,
            "run.failed": self._on_failed,
        }
        handler = handlers.get(event.type)
        if handler is None:
            return ()
        return handler(event)

    def flush(self) -> tuple[RenderAction, ...]:
        """把缓冲文本作为一次 stream 动作送出（无缓冲则无动作）。"""
        if not self._buffer:
            return ()
        text = "".join(self._buffer)
        self._buffer.clear()
        self._has_output = True
        self._last_flush_at = self._clock()
        return (RenderAction(STREAM_KIND, text),)

    def finalize(self) -> tuple[RenderAction, ...]:
        """收尾：幂等，只出一次 finalize（附带 flush）。"""
        if self._finalized:
            return ()
        actions = (*self.flush(), RenderAction(FINALIZE_KIND))
        self._finalized = True
        return actions

    def _on_delta(self, event: SseEvent) -> tuple[RenderAction, ...]:
        delta = str((event.data or {}).get("delta") or "")
        if not delta:
            return ()
        if not self._buffer:
            self._last_flush_at = self._clock()  # 开窗：首个 delta 只起算间隔
        self._buffer.append(delta)
        if self._clock() - self._last_flush_at < self._flush_interval_sec:
            return ()
        return self.flush()

    def _on_interrupt(self, event: SseEvent) -> tuple[RenderAction, ...]:
        data = event.data or {}
        actions = list(self.flush())
        prompt = str(data.get("prompt") or "").strip()
        if prompt:
            actions.append(RenderAction(TEXT_KIND, prompt))
        options = [str(option) for option in _as_sequence(data.get("options")) if str(option).strip()]
        if options:
            actions.append(RenderAction(TEXT_KIND, "\n".join(options)))
        self.awaiting_input = True
        self._has_output = self._has_output or bool(prompt or options)
        return tuple(actions)

    def _on_task_accepted(self, event: SseEvent) -> tuple[RenderAction, ...]:
        message = str((event.data or {}).get("message") or "").strip() or TASK_ACCEPTED_TEXT
        return (*self.finalize(), RenderAction(TEXT_KIND, message))

    def _on_artifact(self, event: SseEvent) -> tuple[RenderAction, ...]:
        # 只给摘要：不使用 artifact_id 生成任何下载入口/链接
        preview = str((event.data or {}).get("preview") or "").strip()
        if not preview:
            logger.debug("artifact_created_without_preview")
            return ()
        return (RenderAction(TEXT_KIND, preview),)

    def _on_completed(self, event: SseEvent) -> tuple[RenderAction, ...]:
        data = event.data or {}
        status = str(data.get("status") or "")
        if status == CANCELLED_STATUS:
            return (*self.finalize(), RenderAction(TEXT_KIND, STOPPED_TEXT))
        final_text = str(data.get("final_text") or "").strip()
        actions = list(self.finalize())
        if final_text and not self._has_output:
            actions.append(RenderAction(TEXT_KIND, final_text))
        return tuple(actions)

    def _on_failed(self, event: SseEvent) -> tuple[RenderAction, ...]:
        code = str((event.data or {}).get("error_code") or "")
        if code == RUN_ABANDONED_CODE:
            text = BROKEN_STREAM_TEXT
        else:
            text = self._message_for(code) if self._message_for is not None else ""
            if not text:
                text = self._fallback_error_text(code)
        return (*self.finalize(), RenderAction(TEXT_KIND, text))

    def _fallback_error_text(self, code: str) -> str:
        default = str(ErrorCode.COMMON_INTERNAL_ERROR)
        if self._message_for is not None:
            return self._message_for(default)
        return ""


def _as_sequence(value: object) -> Sequence[object]:
    if isinstance(value, (list, tuple)):
        return value
    return ()
