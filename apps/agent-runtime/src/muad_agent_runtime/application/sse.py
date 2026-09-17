from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

from .executor import ExecutorEvent

SSE_MEDIA_TYPE = "text/event-stream"
HEARTBEAT_FRAME = ": heartbeat\n\n"
SSE_HEADERS: dict[str, str] = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


class _StreamEnd:
    pass


class SseEmitter:
    def __init__(self, run_id: uuid.UUID | str) -> None:
        self._run_id = str(run_id)
        self._seq = 0

    def frame(self, event: ExecutorEvent) -> str:
        self._seq += 1
        envelope: dict[str, Any] = {
            "run_id": self._run_id,
            "seq": self._seq,
            "timestamp": datetime.now(UTC).isoformat(),
            "type": event.type,
            "data": event.data,
        }
        return f"event: {event.type}\ndata: {json.dumps(envelope, ensure_ascii=False)}\n\n"


async def _pump(
    events: AsyncIterator[ExecutorEvent],
    queue: asyncio.Queue[ExecutorEvent | _StreamEnd],
) -> None:
    try:
        async for event in events:
            await queue.put(event)
    finally:
        await queue.put(_StreamEnd())


async def iter_sse_frames(
    events: AsyncIterator[ExecutorEvent],
    *,
    run_id: uuid.UUID | str,
    heartbeat_sec: float,
) -> AsyncIterator[str]:
    emitter = SseEmitter(run_id)
    queue: asyncio.Queue[ExecutorEvent | _StreamEnd] = asyncio.Queue()
    task = asyncio.create_task(_pump(events, queue))
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=heartbeat_sec)
            except TimeoutError:
                yield HEARTBEAT_FRAME
                continue
            if isinstance(item, _StreamEnd):
                break
            yield emitter.frame(item)
    finally:
        if not task.done():
            task.cancel()
        with suppress(asyncio.CancelledError):
            await task
