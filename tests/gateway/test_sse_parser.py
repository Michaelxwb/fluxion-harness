from __future__ import annotations

from collections.abc import AsyncIterator

from muad_im_gateway.application.runtime_client import SseEvent, iter_sse_events


async def _lines(*values: str) -> AsyncIterator[str]:
    for value in values:
        yield value


async def test_parser_skips_comment_frames_and_flushes_at_eof() -> None:
    frames = [
        ": heartbeat",
        "event: run.created",
        'data: {"run_id": "run-1", "resumed": false}',
    ]
    events = [event async for event in iter_sse_events(_lines(*frames))]
    assert events == [SseEvent(type="run.created", data={"run_id": "run-1", "resumed": False})]


async def test_parser_joins_multiline_data() -> None:
    frames = [
        "event: task.accepted",
        'data: {"task_id":',
        'data: "task-1"}',
        "",
    ]
    events = [event async for event in iter_sse_events(_lines(*frames))]
    assert events == [SseEvent(type="task.accepted", data={"task_id": "task-1"})]


async def test_parser_ignores_unknown_events_and_invalid_frames() -> None:
    frames = [
        "event: something.new",
        'data: {"x": 1}',
        "",
        "event: message.delta",
        "",
        "event: message.delta",
        "data: not-json",
        "",
        "event: run.failed",
        'data: {"error_code": "MODEL_UNAVAILABLE"}',
        "",
    ]
    events = [event async for event in iter_sse_events(_lines(*frames))]
    assert [event.type for event in events] == ["run.failed"]
    assert events[0].data == {"error_code": "MODEL_UNAVAILABLE"}


async def test_parser_handles_crlf_lines() -> None:
    frames = [
        ": heartbeat\r",
        "event: message.delta\r",
        'data: {"delta": "hi"}\r',
        "\r",
    ]
    events = [event async for event in iter_sse_events(_lines(*frames))]
    assert events == [SseEvent(type="message.delta", data={"delta": "hi"})]
