"""Runtime SSE 解析：强类型事件（封套 + 序号）与真实分片输入。

真实 Runtime 出站帧（`apps/agent-runtime/.../application/sse.py`）：

    event: <type>
    data: {"run_id": ..., "seq": N, "timestamp": ..., "type": ..., "data": {...}}

封套字段提为强类型字段，`SseEvent.data` 只保留内层事件载荷；`: heartbeat` 注释帧
不计事件、不参与 seq；seq 严格单调（重复/乱序帧不重复输出）；非法/未知帧显式记录并
丢弃，不破坏流状态。输入按任意位置切分的文本 chunk（非行原子）也能正确还原。
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

KNOWN_EVENT_TYPES = frozenset(
    {
        "run.created",
        "message.delta",
        "skill.loaded",
        "tool.started",
        "tool.completed",
        "task.accepted",
        "artifact.created",
        "interrupt.required",
        "run.completed",
        "run.failed",
    }
)

# 出现任一封套键即按封套解析（合成/测试帧不带这些键）
ENVELOPE_KEYS = ("seq", "timestamp", "run_id")


@dataclass(frozen=True)
class SseEvent:
    """SSE 事件：封套（run_id/seq/timestamp/type）与内层 data 分层保留。"""

    type: str
    data: dict[str, Any]
    run_id: str | None = None
    seq: int | None = None
    timestamp: str | None = None


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _is_envelope(payload: dict[str, Any]) -> bool:
    return isinstance(payload.get("data"), dict) and any(key in payload for key in ENVELOPE_KEYS)


def _known_event(
    name: str,
    *,
    data: dict[str, Any],
    run_id: str | None = None,
    seq: int | None = None,
    timestamp: str | None = None,
) -> SseEvent | None:
    if name not in KNOWN_EVENT_TYPES:
        logger.debug("sse_unknown_event_type event_type=%s", name)
        return None
    return SseEvent(type=name, data=data, run_id=run_id, seq=seq, timestamp=timestamp)


def _decode_payload(event_type: str, data_lines: list[str]) -> dict[str, Any] | None:
    try:
        payload = json.loads("\n".join(data_lines))
    except ValueError:
        logger.warning("sse_invalid_json event_type=%s", event_type)
        return None
    if not isinstance(payload, dict):
        logger.warning("sse_non_object_data event_type=%s", event_type)
        return None
    return payload


def _build_event(event_type: str, data_lines: list[str]) -> SseEvent | None:
    if not data_lines:
        return None
    payload = _decode_payload(event_type, data_lines)
    if payload is None:
        return None
    if not _is_envelope(payload):
        return _known_event(event_type, data=payload)
    # 封套字段非法按错误帧显式记录并丢弃（不污染 seq 判定与流状态）
    seq = payload.get("seq")
    if seq is not None and not isinstance(seq, int):
        logger.warning("sse_invalid_envelope event_type=%s field=seq", event_type)
        return None
    inner = payload.get("data")
    if not isinstance(inner, dict):
        logger.warning("sse_invalid_envelope event_type=%s field=data", event_type)
        return None
    name = raw_type if isinstance(raw_type := payload.get("type"), str) else event_type
    return _known_event(
        name,
        data=inner,
        run_id=_optional_str(payload.get("run_id")),
        seq=seq,
        timestamp=_optional_str(payload.get("timestamp")),
    )


class _FrameBuilder:
    """SSE 行 → 帧：注释/heartbeat 跳过、多行 data 合并、CRLF 容错、空行收帧。"""

    def __init__(self) -> None:
        self._event_type = ""
        self._data_lines: list[str] = []

    def feed(self, line: str) -> SseEvent | None:
        line = line.rstrip("\r")
        if line.startswith(":"):
            return None
        if not line:
            return self.flush()
        field, _, value = line.partition(":")
        if field == "event":
            self._event_type = value.strip()
        elif field == "data":
            self._data_lines.append(value[1:] if value.startswith(" ") else value)
        return None

    def flush(self) -> SseEvent | None:
        event = _build_event(self._event_type, self._data_lines)
        self._event_type = ""
        self._data_lines = []
        return event


class _SeqGuard:
    """seq 严格单调：重复/乱序帧不重复输出（无 seq 的合成帧不参与判定）。"""

    def __init__(self) -> None:
        self._last_seq: int | None = None

    def accept(self, event: SseEvent | None) -> SseEvent | None:
        if event is None:
            return None
        if event.seq is None:
            return event
        if self._last_seq is not None and event.seq <= self._last_seq:
            logger.warning(
                "sse_out_of_order_dropped type=%s seq=%s last_seq=%s",
                event.type,
                event.seq,
                self._last_seq,
            )
            return None
        self._last_seq = event.seq
        return event


async def iter_sse_events(chunks: AsyncIterator[str]) -> AsyncIterator[SseEvent]:
    """真实分片文本流 → 强类型事件；封套还原与 seq 单调性在此收口。"""
    builder = _FrameBuilder()
    guard = _SeqGuard()
    buffer = ""
    async for chunk in chunks:
        buffer += chunk
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            event = guard.accept(builder.feed(line))
            if event is not None:
                yield event
    if buffer:  # EOF 终止最后一行（含未以换行结尾的收尾帧）
        event = guard.accept(builder.feed(buffer))
        if event is not None:
            yield event
    event = guard.accept(builder.flush())
    if event is not None:
        yield event
