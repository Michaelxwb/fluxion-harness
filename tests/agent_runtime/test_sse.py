import asyncio
import json
from collections.abc import AsyncIterator

from muad_agent_runtime.application.executor import ExecutorEvent
from muad_agent_runtime.application.sse import (
    HEARTBEAT_FRAME,
    SSE_HEADERS,
    SseEmitter,
    iter_sse_frames,
)


def _payload_of(frame: str) -> dict[str, object]:
    data_line = next(line for line in frame.splitlines() if line.startswith("data:"))
    return json.loads(data_line[len("data:") :].lstrip())


def test_emitter_builds_envelope_with_monotonic_seq() -> None:
    emitter = SseEmitter("run-1")
    first = emitter.frame(ExecutorEvent(type="run.created", data={"resumed": False}))
    second = emitter.frame(ExecutorEvent(type="message.delta", data={"delta": "a"}))

    first_payload = _payload_of(first)
    second_payload = _payload_of(second)
    assert first.startswith("event: run.created\ndata: ")
    assert first_payload["run_id"] == "run-1"
    assert first_payload["seq"] == 1
    assert second_payload["seq"] == 2
    assert first_payload["type"] == "run.created"
    assert first_payload["data"] == {"resumed": False}
    assert isinstance(first_payload["timestamp"], str)


def test_sse_headers_match_contract() -> None:
    assert SSE_HEADERS == {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }


async def _slow_events() -> AsyncIterator[ExecutorEvent]:
    await asyncio.sleep(0.05)
    yield ExecutorEvent(type="message.delta", data={"delta": "a"})
    await asyncio.sleep(0.05)
    yield ExecutorEvent(type="run.completed", data={"status": "COMPLETED", "final_text": "a"})


async def test_heartbeat_frames_are_emitted_while_idle() -> None:
    frames = [
        frame
        async for frame in iter_sse_frames(
            _slow_events(),
            run_id="run-1",
            heartbeat_sec=0.01,
        )
    ]
    assert frames.count(HEARTBEAT_FRAME) >= 1
    data_frames = [frame for frame in frames if frame.startswith("event:")]
    assert len(data_frames) == 2
    assert [_payload_of(frame)["seq"] for frame in data_frames] == [1, 2]


async def test_stream_ends_when_events_are_exhausted() -> None:
    async def events() -> AsyncIterator[ExecutorEvent]:
        yield ExecutorEvent(type="run.created", data={})

    frames = [
        frame
        async for frame in iter_sse_frames(events(), run_id="run-2", heartbeat_sec=5.0)
    ]
    assert len(frames) == 1
    assert _payload_of(frames[0])["run_id"] == "run-2"
