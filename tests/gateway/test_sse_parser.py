from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from muad_im_gateway.application.sse import SseEvent, iter_sse_events

B114_RUN_ID = "run-b114"
B114_TIMESTAMP = "2026-09-24T00:00:00+00:00"


async def _lines(*values: str) -> AsyncIterator[str]:
    for value in values:
        yield value


def _envelope_frame(
    event_type: str,
    payload: dict[str, Any],
    *,
    seq: int,
    run_id: str = B114_RUN_ID,
    timestamp: str = B114_TIMESTAMP,
) -> str:
    """真实 Runtime 出站帧（apps/agent-runtime/application/sse.py 的封套格式）。"""
    envelope = {
        "run_id": run_id,
        "seq": seq,
        "timestamp": timestamp,
        "type": event_type,
        "data": payload,
    }
    return f"event: {event_type}\ndata: {json.dumps(envelope, ensure_ascii=False)}\n\n"


async def test_parser_skips_comment_frames_and_flushes_at_eof() -> None:
    # 输入按 chunk 切分（非行原子）：末行无换行由 EOF 收尾
    frames = [
        ": heartbeat\n",
        "event: run.created\n",
        'data: {"run_id": "run-1", "resumed": false}',
    ]
    events = [event async for event in iter_sse_events(_lines(*frames))]
    assert events == [SseEvent(type="run.created", data={"run_id": "run-1", "resumed": False})]


async def test_parser_joins_multiline_data() -> None:
    frames = [
        "event: task.accepted\n",
        'data: {"task_id":\n',
        'data: "task-1"}\n',
        "\n",
    ]
    events = [event async for event in iter_sse_events(_lines(*frames))]
    assert events == [SseEvent(type="task.accepted", data={"task_id": "task-1"})]


async def test_parser_ignores_unknown_events_and_invalid_frames() -> None:
    frames = [
        "event: something.new\n",
        'data: {"x": 1}\n',
        "\n",
        "event: message.delta\n",
        "\n",
        "event: message.delta\n",
        "data: not-json\n",
        "\n",
        "event: run.failed\n",
        'data: {"error_code": "MODEL_UNAVAILABLE"}\n',
        "\n",
    ]
    events = [event async for event in iter_sse_events(_lines(*frames))]
    assert [event.type for event in events] == ["run.failed"]
    assert events[0].data == {"error_code": "MODEL_UNAVAILABLE"}


async def test_parser_handles_crlf_lines() -> None:
    frames = [
        ": heartbeat\r\n",
        "event: message.delta\r\n",
        'data: {"delta": "hi"}\r\n',
        "\r\n",
    ]
    events = [event async for event in iter_sse_events(_lines(*frames))]
    assert events == [SseEvent(type="message.delta", data={"delta": "hi"})]


# ---------------------------------------------------------------------------
# B-114: 强类型 SSE 事件（封套与序号）、分片输入与错误帧（真实 parser）
# ---------------------------------------------------------------------------


async def test_b114_preserves_envelope_fields_and_inner_data() -> None:
    """封套 run_id/seq/timestamp/type 提为强类型字段，事件 data 保持内层载荷。"""
    frames = [
        _envelope_frame(
            "run.created",
            {
                "run_id": B114_RUN_ID,
                "conversation_id": "conv-1",
                "resumed": True,
                "trace_id": "trace-1",
            },
            seq=7,
        ),
        _envelope_frame("message.delta", {"delta": "你"}, seq=8),
    ]
    events = [event async for event in iter_sse_events(_lines(*frames))]

    assert [event.type for event in events] == ["run.created", "message.delta"]
    created = events[0]
    assert created.run_id == B114_RUN_ID
    assert created.seq == 7
    assert created.timestamp == B114_TIMESTAMP
    # 内层 data 原样保留：resumed 字段不丢，且不再被封套包一层
    assert created.data == {
        "run_id": B114_RUN_ID,
        "conversation_id": "conv-1",
        "resumed": True,
        "trace_id": "trace-1",
    }
    assert created.data["resumed"] is True
    assert events[1].data == {"delta": "你"}


async def test_b114_seq_is_strictly_monotonic_and_repeats_are_dropped() -> None:
    """重复/乱序帧不重复输出：seq 严格单调。"""
    frames = [
        _envelope_frame("message.delta", {"delta": "a"}, seq=1),
        _envelope_frame("message.delta", {"delta": "b"}, seq=2),
        _envelope_frame("message.delta", {"delta": "b"}, seq=2),  # 重复帧
        _envelope_frame("message.delta", {"delta": "a"}, seq=1),  # 乱序回流
        _envelope_frame("run.completed", {"status": "COMPLETED"}, seq=3),
    ]
    events = [event async for event in iter_sse_events(_lines(*frames))]

    assert [event.seq for event in events] == [1, 2, 3]
    assert [event.data.get("delta") for event in events] == ["a", "b", None]


async def test_b114_heartbeat_does_not_count_as_event_or_advance_seq() -> None:
    """`: heartbeat` 注释帧不计事件、不影响 seq 序列。"""
    frames = [
        ": heartbeat",
        _envelope_frame("message.delta", {"delta": "a"}, seq=1),
        ": heartbeat",
        ": heartbeat",
        _envelope_frame("message.delta", {"delta": "b"}, seq=2),
        ": heartbeat",
        _envelope_frame("run.completed", {"status": "COMPLETED"}, seq=3),
        ": heartbeat",
    ]
    events = [event async for event in iter_sse_events(_lines(*frames))]

    assert [event.type for event in events] == ["message.delta", "message.delta", "run.completed"]
    assert [event.seq for event in events] == [1, 2, 3]


async def test_b114_reassembles_fragmented_chunks() -> None:
    """网络分片：按任意位置切分的 chunk 必须还原成同一组事件（非行原子的输入）。"""
    stream = (
        ": heartbeat\n"
        + _envelope_frame("message.delta", {"delta": "分片"}, seq=1)
        + _envelope_frame("run.completed", {"status": "COMPLETED", "final_text": "分片"}, seq=2)
    )
    fragments = [stream[index : index + 7] for index in range(0, len(stream), 7)]
    assert len(fragments) > 4  # 确实在行中间/JSON 中间切开

    events = [event async for event in iter_sse_events(_lines(*fragments))]
    assert [event.type for event in events] == ["message.delta", "run.completed"]
    assert [event.seq for event in events] == [1, 2]
    assert events[0].data == {"delta": "分片"}
    assert events[1].data == {"status": "COMPLETED", "final_text": "分片"}


async def test_b114_error_frames_do_not_corrupt_stream_state() -> None:
    """非法 JSON / 非对象 data / 未知事件 / 坏封套都不破坏流状态与 seq 判定。"""
    frames = [
        _envelope_frame("message.delta", {"delta": "a"}, seq=1),
        "event: message.delta\ndata: not-json\n\n",  # 非法 JSON
        'event: message.delta\ndata: ["not-an-object"]\n\n',  # data 不是对象
        'event: brand.new.event\ndata: {"x": 1}\n\n',  # 未知事件类型
        'event: message.delta\ndata: {"seq": "not-a-number", "data": {"delta": "x"}}\n\n',  # 坏封套
        'event: message.delta\ndata: \n\n',  # data 为空
        _envelope_frame("message.delta", {"delta": "b"}, seq=2),
        _envelope_frame("run.completed", {"status": "COMPLETED"}, seq=3),
    ]
    events = [event async for event in iter_sse_events(_lines(*frames))]

    assert [event.seq for event in events] == [1, 2, 3]
    assert events[0].data == {"delta": "a"}
    assert events[1].data == {"delta": "b"}
    assert events[2].type == "run.completed"
