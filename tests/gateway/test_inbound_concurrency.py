"""B-116: 长流不阻塞后续消息与 /stop（真实 iter_events→Gateway 消费队列→Runtime HTTP/SSE→WS 回复）。

不得 Mock 的真实边界：真实本地 WS 探针（真实入站推送 + 生产 Adapter 真实出站帧回读）+
生产 `InboundPipeline.consume` 消费队列；Runtime 侧为按文本受控放行的 SSE 客户端
（断言 Gateway 不缓存活跃 Run 事实，RUN_BUSY/resume 由 Runtime 决定）。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any

import pytest
from fakes import FakeConsoleClient, resolved_response
from muad_api import AppError
from muad_api.catalog import MessageCatalog
from muad_api.error_codes import ErrorCode
from muad_contracts import BotSnapshotItem, RunRequest
from muad_im_gateway.application.inbound import InboundPipeline
from muad_im_gateway.application.sse import SseEvent
from muad_im_gateway.channels.wecom.adapter import ConnectionState, WeComAdapter
from muad_im_gateway.infrastructure.dedupe import NullDedupeStore

from tests.e2e.wecom_probe_app import WeComProbe

B116_BOT = "bot-b116"
B116_SECRET = "b116-secret"
USER_A = "ext-b116-a"
USER_B = "ext-b116-b"
CHAT_A = "chat-b116-a"
CHAT_B = "chat-b116-b"
WAIT_TIMEOUT_SEC = 20.0
STOP_ACCEPTED_TEXT = "正在停止当前任务…"
STREAM_CMD = "aibot_respond_msg"
SEND_CMD = "aibot_send_msg"


class GatedRuntimeClient:
    """受控 SSE 客户端：未放行的流保持"未终态"（模拟长流）；可选按文本抛错。"""

    def __init__(self) -> None:
        self.calls: list[RunRequest] = []
        self.started: list[str] = []
        self._gates: dict[str, asyncio.Event] = {}
        self._errors: dict[str, AppError] = {}
        self.cancel_calls = 0

    def gate(self, text: str) -> asyncio.Event:
        event = self._gates.setdefault(text, asyncio.Event())
        return event

    def fail_with(self, text: str, error: AppError) -> None:
        self._errors[text] = error

    async def create_run(
        self, request: RunRequest, *, tenant_id: str, trace_id: str = "", idempotency_key: str | None = None
    ) -> AsyncIterator[SseEvent]:
        text = request.message.text
        self.calls.append(request)
        self.started.append(text)
        error = self._errors.get(text)
        if error is not None:
            raise error
        yield SseEvent(
            type="run.created",
            data={"run_id": f"run-{text}", "conversation_id": str(uuid.uuid4()), "resumed": False},
        )
        yield SseEvent(type="message.delta", data={"delta": text})
        await self.gate(text).wait()
        yield SseEvent(type="run.completed", data={"status": "COMPLETED", "final_text": text})

    async def cancel_active(
        self, agent_id: uuid.UUID, platform_user_id: uuid.UUID, *, tenant_id: str = "", trace_id: str = ""
    ) -> dict[str, Any]:
        self.cancel_calls += 1
        return {"run_id": "run-cancel", "status": "CANCELLING"}

    async def create_conversation(
        self,
        agent_id: uuid.UUID,
        platform_user_id: uuid.UUID,
        *,
        tenant_id: str = "",
        trace_id: str = "",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return {"conversation_id": str(uuid.uuid4())}

    async def aclose(self) -> None:
        return None


async def _wait_for(predicate: Any, *, what: str, timeout: float = WAIT_TIMEOUT_SEC) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(what)


@pytest.fixture()
async def probe() -> AsyncIterator[WeComProbe]:
    instance = WeComProbe(expected_bots={B116_BOT: B116_SECRET})
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
                bot_id=B116_BOT,
                secret=B116_SECRET,
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
            lambda: instance.connection_states.get(B116_BOT) is ConnectionState.CONNECTED,
            what="WeComAdapter 未在真实 WS 上完成认证",
        )
        yield instance
    finally:
        await instance.stop()


def _pipeline(runtime: Any, catalog: MessageCatalog) -> InboundPipeline:
    console = FakeConsoleClient()
    console.resolve_response = resolved_response()
    return InboundPipeline(
        dedupe=NullDedupeStore(),
        console=console,
        runtime=runtime,
        catalog=catalog,
        tenant_id="tenant-b116",
        locale="zh-CN",
    )


def _outbound_texts(probe: WeComProbe, since: int) -> list[str]:
    texts: list[str] = []
    for item in probe.received[since:]:
        frame = item.frame
        body = frame.get("body") or {}
        if frame.get("cmd") == STREAM_CMD:
            texts.append(str((body.get("stream") or {}).get("content") or ""))
        elif frame.get("cmd") == SEND_CMD:
            texts.append(str((body.get("text") or {}).get("content") or ""))
    return texts


async def _push(probe: WeComProbe, *, external_user_id: str, text: str, chat_id: str) -> None:
    await probe.push_message(
        bot_id=B116_BOT,
        message_id=f"b116-{uuid.uuid4().hex[:8]}",
        external_user_id=external_user_id,
        text=text,
        reply_id=f"req-{uuid.uuid4().hex[:6]}",
        chat_id=chat_id,
    )


async def test_b116_long_stream_does_not_block_other_user_or_stop(
    adapter: WeComAdapter, probe: WeComProbe, catalog: MessageCatalog
) -> None:
    runtime = GatedRuntimeClient()
    pipeline = _pipeline(runtime, catalog)
    consumer = asyncio.create_task(pipeline.consume(adapter))
    since = len(probe.received)
    try:
        # 用户 A 开一条长流（未放行 → 一直未终态）
        await _push(probe, external_user_id=USER_A, text="长任务", chat_id=CHAT_A)
        await _wait_for(lambda: "长任务" in runtime.started, what="长流未开始")

        # 长流进行中：同用户 /stop 与另一用户的消息都必须被处理
        await _push(probe, external_user_id=USER_A, text="/stop", chat_id=CHAT_A)
        await _push(probe, external_user_id=USER_B, text="B 的问题", chat_id=CHAT_B)

        await _wait_for(
            lambda: any(STOP_ACCEPTED_TEXT in text for text in _outbound_texts(probe, since)),
            what="/stop 未能在长流期间被处理",
        )
        await _wait_for(
            lambda: "B 的问题" in runtime.started,
            what="另一用户的消息被长流阻塞",
        )
        # 无本地活跃 Run 事实缓存（resume/RUN_BUSY 由 Runtime 决定）
        assert not hasattr(pipeline, "pending_run_id")
        assert not hasattr(pipeline, "_pending_run_ids")
    finally:
        runtime.gate("长任务").set()
        runtime.gate("B 的问题").set()
        consumer.cancel()
        with suppress(asyncio.CancelledError):
            await consumer


async def test_b116_same_route_streams_are_serialized(
    adapter: WeComAdapter, probe: WeComProbe, catalog: MessageCatalog
) -> None:
    runtime = GatedRuntimeClient()
    pipeline = _pipeline(runtime, catalog)
    consumer = asyncio.create_task(pipeline.consume(adapter))
    since = len(probe.received)
    try:
        await _push(probe, external_user_id=USER_A, text="first", chat_id=CHAT_A)
        await _wait_for(lambda: "first" in runtime.started, what="首个流未开始")
        await _push(probe, external_user_id=USER_A, text="second", chat_id=CHAT_A)
        # 同 route 的第二条必须等待首条流结束（不交叉）
        await asyncio.sleep(0.5)
        assert runtime.started == ["first"], runtime.started

        runtime.gate("first").set()
        await _wait_for(lambda: "second" in runtime.started, what="首条流结束后第二条未开始")
        runtime.gate("second").set()
        await _wait_for(
            lambda: any(text == "second" for text in _outbound_texts(probe, since)),
            what="第二条流未出站",
        )
        # 同一流的 flush 帧与 finish 帧都携带累积全文 → 按"连续去重"判定不交叉
        ordered = [text for text in _outbound_texts(probe, since) if text in ("first", "second")]
        distinct: list[str] = []
        for text in ordered:
            if not distinct or distinct[-1] != text:
                distinct.append(text)
        assert distinct == ["first", "second"], ordered
    finally:
        runtime.gate("first").set()
        runtime.gate("second").set()
        consumer.cancel()
        with suppress(asyncio.CancelledError):
            await consumer


async def test_b116_runtime_decides_run_busy(
    adapter: WeComAdapter, probe: WeComProbe, catalog: MessageCatalog
) -> None:
    runtime = GatedRuntimeClient()
    runtime.fail_with("busy", AppError(ErrorCode.RUN_BUSY))
    pipeline = _pipeline(runtime, catalog)
    consumer = asyncio.create_task(pipeline.consume(adapter))
    since = len(probe.received)
    try:
        await _push(probe, external_user_id=USER_A, text="busy", chat_id=CHAT_A)
        expected = catalog.message(str(ErrorCode.RUN_BUSY), "zh-CN")
        await _wait_for(
            lambda: expected in _outbound_texts(probe, since),
            what=f"未按 catalog 回 RUN_BUSY 文案（{expected}）",
        )
    finally:
        consumer.cancel()
        with suppress(asyncio.CancelledError):
            await consumer
