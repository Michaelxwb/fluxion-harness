"""B-115: SSE 到 IM 的收尾与中断呈现（真实 SSE 解析→生产 renderer→真实本地 WS SDK 出站）。

不得 Mock 的真实边界：TASK-014 的真实 SSE 解析器（分片文本输入）+ 生产
`StreamRenderer`/`InboundPipeline` + 生产 `WeComAdapter` → 官方 SDK → 真实本地 WS 探针
（真实 `wss://`：入站真实推送、出站流式帧回读）。
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any

import pytest
from fakes import FakeConsoleClient, FakeRuntimeClient, resolved_response
from muad_api.catalog import MessageCatalog
from muad_contracts import BotSnapshotItem
from muad_im_gateway.application.inbound import InboundPipeline
from muad_im_gateway.application.sse import SseEvent, iter_sse_events
from muad_im_gateway.channels.wecom.adapter import ConnectionState, WeComAdapter
from muad_im_gateway.infrastructure.dedupe import NullDedupeStore

from tests.e2e.wecom_probe_app import WeComProbe

B115_BOT = "bot-b115"
B115_SECRET = "b115-secret"
B115_EXTERNAL_USER = "ext-b115"
B115_CHAT = "chat-b115"
WAIT_TIMEOUT_SEC = 20.0
STOPPED_TEXT = "当前任务已停止"
BROKEN_STREAM_TEXT = "服务暂时中断，请重发消息"
STREAM_CMD = "aibot_respond_msg"
SEND_CMD = "aibot_send_msg"


async def _parse_real_sse(steps: list[tuple[str, dict[str, Any]]]) -> list[SseEvent]:
    """真实 SSE 解析：按封套构造帧，再按 13 字节分片喂给生产 parser。"""
    raw = "".join(
        "event: {type}\ndata: {payload}\n\n".format(
            type=event_type,
            payload=json.dumps(
                {
                    "run_id": "run-b115",
                    "seq": index,
                    "timestamp": "2026-09-24T00:00:00+00:00",
                    "type": event_type,
                    "data": data,
                },
                ensure_ascii=False,
            ),
        )
        for index, (event_type, data) in enumerate(steps, start=1)
    )
    fragments = [raw[index : index + 13] for index in range(0, len(raw), 13)]

    async def chunks() -> AsyncIterator[str]:
        for fragment in fragments:
            yield fragment

    return [event async for event in iter_sse_events(chunks())]


async def _wait_for(predicate: Any, *, what: str, timeout: float = WAIT_TIMEOUT_SEC) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(what)


@pytest.fixture()
async def probe() -> AsyncIterator[WeComProbe]:
    instance = WeComProbe(expected_bots={B115_BOT: B115_SECRET})
    await instance.start()
    try:
        yield instance
    finally:
        await instance.stop()


@pytest.fixture()
async def adapter(probe: WeComProbe, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[WeComAdapter]:
    monkeypatch.setenv("WECOM_WS_URL", probe.ws_url)
    monkeypatch.setenv("WECOM_WS_CA_FILE", str(probe.cert_path))
    instance = WeComAdapter(
        bots=[
            BotSnapshotItem(
                bot_account_id=uuid.uuid4(),
                bot_id=B115_BOT,
                secret=B115_SECRET,
                agent_id=uuid.uuid4(),
            )
        ],
        backoff_base_sec=0.2,
        backoff_max_sec=0.5,
        liveness_interval_sec=0.2,
    )
    await instance.start()
    try:
        await _wait_for(
            lambda: instance.connection_states.get(B115_BOT) is ConnectionState.CONNECTED,
            what="WeComAdapter 未在真实 WS 上完成认证",
        )
        yield instance
    finally:
        await instance.stop()


def _pipeline(events: list[SseEvent], catalog: MessageCatalog) -> InboundPipeline:
    console = FakeConsoleClient()
    console.resolve_response = resolved_response()
    return InboundPipeline(
        dedupe=NullDedupeStore(),
        console=console,
        runtime=FakeRuntimeClient(events),
        catalog=catalog,
        tenant_id="tenant-b115",
        locale="zh-CN",
    )


def _outbound_frames(probe: WeComProbe, since: int) -> list[dict[str, Any]]:
    """流式帧（aibot_respond_msg，内容为该帧累积全文）与文本帧（aibot_send_msg）。"""
    return [
        item.frame
        for item in probe.received[since:]
        if item.frame.get("cmd") in (STREAM_CMD, SEND_CMD)
    ]


def _frame_text(frame: dict[str, Any]) -> str:
    body = frame.get("body") or {}
    if frame.get("cmd") == STREAM_CMD:
        return str((body.get("stream") or {}).get("content") or "")
    return str((body.get("text") or {}).get("content") or "")


def _is_stream(frame: dict[str, Any]) -> bool:
    return frame.get("cmd") == STREAM_CMD


def _stream_finished(frame: dict[str, Any]) -> bool:
    body = frame.get("body") or {}
    return bool((body.get("stream") or {}).get("finish"))


async def _run_once(
    adapter: WeComAdapter,
    probe: WeComProbe,
    catalog: MessageCatalog,
    steps: list[tuple[str, dict[str, Any]]],
    *,
    expect: str,
) -> list[dict[str, Any]]:
    """真实入站（探针推送）→ 生产 Pipeline/Renderer → 真实 WS 出站帧（等到 expect 出现）。"""
    events = await _parse_real_sse(steps)
    pipeline = _pipeline(events, catalog)
    since = len(probe.received)
    consumer = asyncio.create_task(pipeline.consume(adapter))
    try:
        await probe.push_message(
            bot_id=B115_BOT,
            message_id=f"b115-{uuid.uuid4().hex[:8]}",
            external_user_id=B115_EXTERNAL_USER,
            text="检查设备",
            reply_id="req-b115",
            chat_id=B115_CHAT,
        )
        await _wait_for(
            lambda: any(expect in _frame_text(frame) for frame in _outbound_frames(probe, since)),
            what=f"未在真实 WS 上观测到出站内容 {expect!r}",
        )
    finally:
        consumer.cancel()
        with suppress(asyncio.CancelledError):
            await consumer
    return _outbound_frames(probe, since)


async def test_b115_stream_tail_is_complete_and_finalized_once(
    adapter: WeComAdapter, probe: WeComProbe, catalog: MessageCatalog
) -> None:
    frames = await _run_once(
        adapter,
        probe,
        catalog,
        [
            ("run.created", {"run_id": "run-b115", "resumed": False}),
            ("message.delta", {"delta": "分析"}),
            ("message.delta", {"delta": "完成"}),
            ("run.completed", {"status": "COMPLETED", "final_text": "分析完成"}),
        ],
        expect="分析完成",
    )

    stream_frames = [frame for frame in frames if _is_stream(frame)]
    # 真实 WeCom 流式协议：每帧携带"当前累积全文"，末帧 finish=true
    assert [_frame_text(frame) for frame in stream_frames][-1] == "分析完成", stream_frames
    assert all(
        "分析完成".startswith(_frame_text(frame)) for frame in stream_frames
    ), [(_frame_text(frame)) for frame in stream_frames]
    finished = [index for index, frame in enumerate(stream_frames) if _stream_finished(frame)]
    assert finished == [len(stream_frames) - 1], stream_frames


async def test_b115_cancelled_abandoned_and_interrupt_copy(
    adapter: WeComAdapter, probe: WeComProbe, catalog: MessageCatalog
) -> None:
    cancelled = await _run_once(
        adapter,
        probe,
        catalog,
        [
            ("run.created", {"run_id": "run-b115"}),
            ("message.delta", {"delta": "半句"}),
            ("run.completed", {"status": "CANCELLED", "final_text": ""}),
        ],
        expect=STOPPED_TEXT,
    )
    assert "半句" in "".join(_frame_text(frame) for frame in cancelled)
    assert STOPPED_TEXT in "".join(_frame_text(frame) for frame in cancelled)

    abandoned = await _run_once(
        adapter,
        probe,
        catalog,
        [
            ("run.created", {"run_id": "run-b115"}),
            ("run.failed", {"status": "FAILED", "error_code": "RUN_ABANDONED"}),
        ],
        expect=BROKEN_STREAM_TEXT,
    )
    assert BROKEN_STREAM_TEXT in "".join(_frame_text(frame) for frame in abandoned)

    interrupted = await _run_once(
        adapter,
        probe,
        catalog,
        [
            ("run.created", {"run_id": "run-b115"}),
            ("message.delta", {"delta": "分析中"}),
            (
                "interrupt.required",
                {"prompt": "是否继续？", "options": ["继续", "取消"]},
            ),
        ],
        expect="取消",
    )
    joined = "".join(_frame_text(frame) for frame in interrupted)
    assert "分析中" in joined and "是否继续？" in joined and "取消" in joined


async def test_b115_artifact_summary_has_no_links_or_internal_info(
    adapter: WeComAdapter, probe: WeComProbe, catalog: MessageCatalog
) -> None:
    artifact_id = str(uuid.uuid4())
    frames = await _run_once(
        adapter,
        probe,
        catalog,
        [
            ("run.created", {"run_id": "run-b115"}),
            (
                "artifact.created",
                {
                    "artifact_id": artifact_id,
                    "media_type": "text/markdown",
                    "preview": "策略检查摘要：18 项通过，2 项待处理",
                    "storage_key": "artifacts/secret-path/skill.zip",
                    "tool_name": "internal_policy_tool",
                },
            ),
            ("run.completed", {"status": "COMPLETED", "final_text": "完成"}),
        ],
        expect="策略检查摘要",
    )

    rendered = "".join(_frame_text(frame) for frame in frames)
    assert "策略检查摘要：18 项通过，2 项待处理" in rendered
    # 不提供下载入口/内部信息：artifact_id、storage_key、tool 名与任何 URL 都不出现
    assert artifact_id not in rendered
    assert "artifacts/secret-path" not in rendered
    assert "internal_policy_tool" not in rendered
    assert "http" not in rendered.lower()
    assert B115_SECRET not in rendered
