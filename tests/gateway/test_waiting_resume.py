"""TASK-006（functional）：Gateway 等待态、SSE 断流重连与终态主动投递。

真实边界：生产 `InboundPipeline` + 生产 `ExecutionProgress`/`StreamRenderer`/SSE 解析 +
生产 `ActiveChannelDelivery`（真实 reserve→发送→ack 状态转换，API-18 端口是进程内替身）；
Runtime 以带 canonical seq 的事件源替身模拟"等待 → 断流 → 按 `after_seq` 重连"，
断言不重复 POST、不重置预算、终态只出一次。
"""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from fakes import FakeConsoleClient, FakeRuntimeClient, make_envelope, resolved_response
from muad_api import AppError
from muad_api.catalog import MessageCatalog
from muad_contracts import DeliveryMessage, DeliveryRouteInput
from muad_contracts.channel_delivery import (
    DeliveryAck,
    DeliveryAckReceipt,
    DeliveryLeaseGrant,
    DeliveryReceiptPayload,
    DeliveryReservationRequest,
)
from muad_im_gateway.application.channel_delivery import ActiveChannelDelivery, terminal_delivery_key
from muad_im_gateway.application.inbound import InboundPipeline, route_from_envelope
from muad_im_gateway.application.progress import ExecutionProgress, ProgressPhase
from muad_im_gateway.application.sse import SseEvent, iter_sse_events
from muad_im_gateway.application.stream_renderer import BROKEN_STREAM_TEXT
from muad_im_gateway.channels.base import ChannelAdapterUnavailable
from muad_im_gateway.channels.fake import FakeChannelAdapter
from muad_im_gateway.infrastructure.dedupe import InMemoryDedupeStore

WAITING_TEXT = "等待任务结果"


class RecordingSession:
    """回复会话替身：记录状态/正文/收尾，可注入单个写入失败。"""

    def __init__(self, *, fail_stream: bool = False, fail_finish: bool = False) -> None:
        self.statuses: list[str] = []
        self.streamed: list[str] = []
        self.texts: list[str] = []
        self.finished = 0
        self.bound_run: str | None = None
        self.fail_stream = fail_stream
        self.fail_finish = fail_finish
        self._closed = False

    def bind_run(self, run_id: str) -> None:
        self.bound_run = run_id

    async def update_status(self, text: str) -> None:
        self.statuses.append(text)

    async def stream(self, text: str) -> None:
        if self.fail_stream:
            raise ChannelAdapterUnavailable("stream gone")
        self.streamed.append(text)

    async def send(self, text: str) -> None:
        self.texts.append(text)

    async def reply_once(self, text: str) -> None:
        self.texts.append(text)

    async def finish(self) -> None:
        if self.fail_finish:
            raise ChannelAdapterUnavailable("finish gone")
        if not self._closed:  # 真实会话的终帧是幂等的
            self._closed = True
            self.finished += 1


class SessionAdapter(FakeChannelAdapter):
    """实现可选回复会话能力的渠道。"""

    def __init__(self, session: RecordingSession) -> None:
        super().__init__()
        self.session = session
        self.opened: list[str] = []

    def open_reply(self, route: DeliveryRouteInput, message_id: str) -> RecordingSession:
        self.opened.append(message_id)
        return self.session


class ExpiredSessionAdapter(FakeChannelAdapter):
    """声明了回复会话能力、但本条消息的回调已过期（开不出会话）。"""

    def open_reply(self, route: DeliveryRouteInput, message_id: str) -> RecordingSession:
        raise ChannelAdapterUnavailable("inbound callback context expired")


class NoActiveAdapter:
    """最小适配器：没有 `send_active` 主动投递能力。"""

    name = "WECOM"

    def __init__(self) -> None:
        self.sent: list[DeliveryMessage] = []

    async def start(self) -> None: ...
    async def stop(self) -> None: ...

    async def iter_events(self):  # type: ignore[no-untyped-def]
        return
        yield

    async def send(self, route: DeliveryRouteInput, message: DeliveryMessage) -> None:
        self.sent.append(message)

    async def stream(self, route: DeliveryRouteInput, chunks: Any) -> None: ...


class FailingActiveAdapter(FakeChannelAdapter):
    async def send_active(self, route: DeliveryRouteInput, message: DeliveryMessage) -> None:
        raise ChannelAdapterUnavailable("active channel down")


class FakeChannelDeliveryProtocol:
    """API-18 协调端口的进程内替身（真实验收边界见 B-267 的 HTTP/PG 证据）。

    只实现本文件断言所需的状态转换：首次 reserve 授租约；`DELIVERED` 冻结首次回执并在重放时
    原样返回；`NOT_SENT` 释放投递资格（可重试）；失约 ack 显式失败（停手）。
    """

    def __init__(self) -> None:
        self.reserves: list[DeliveryReservationRequest] = []
        self.acks: list[DeliveryAck] = []
        self._ids: dict[str, UUID] = {}
        self._epochs: dict[str, int] = {}
        self._owners: dict[str, UUID | None] = {}
        self._receipts: dict[str, DeliveryReceiptPayload] = {}

    async def reserve_delivery(
        self, request: DeliveryReservationRequest, *, tenant_id: str
    ) -> DeliveryLeaseGrant:
        self.reserves.append(request)
        key = request.delivery_key
        if key in self._receipts:
            return self._grant(key, lease=False)
        self._epochs[key] = self._epochs.get(key, 0) + 1
        self._owners[key] = request.attempt_id
        return self._grant(key, lease=True)

    async def ack_delivery(
        self, notification_id: UUID, body: DeliveryAck, *, tenant_id: str
    ) -> DeliveryAckReceipt:
        self.acks.append(body)
        key = body.delivery_key
        if self._owners.get(key) != body.attempt_id or self._epochs.get(key) != body.epoch:
            raise AppError("DELIVERY_LEASE_LOST")
        if body.outcome == "DELIVERED":
            self._receipts[key] = DeliveryReceiptPayload(
                outcome="DELIVERED",
                attempt=body.epoch,
                attempt_id=body.attempt_id,
                lease_epoch=body.epoch,
                acked_at=datetime.now(UTC),
                result=body.result,
            )
        self._owners[key] = None
        return DeliveryAckReceipt(
            notification_id=notification_id,
            delivery_key=key,
            state="DELIVERED" if body.outcome == "DELIVERED" else "PENDING",
            attempt=body.epoch,
            reused=False,
            receipt=self._receipts.get(key),
        )

    def _grant(self, key: str, *, lease: bool) -> DeliveryLeaseGrant:
        receipt = self._receipts.get(key)
        return DeliveryLeaseGrant(
            notification_id=self._ids.setdefault(key, uuid4()),
            delivery_key=key,
            state="DELIVERING" if lease else ("DELIVERED" if receipt else "PENDING"),
            lease_epoch=self._epochs.get(key, 0) if lease else 0,
            lease_until=datetime.now(UTC) + timedelta(seconds=30) if lease else None,
            attempt=self._epochs.get(key, 0),
            reused=receipt is not None,
            receipt=receipt,
        )


def _pipeline(
    catalog: MessageCatalog,
    runtime: FakeRuntimeClient,
    *,
    dedupe: InMemoryDedupeStore | None = None,
    channel_delivery: ActiveChannelDelivery | None = None,
    attempts: int = 0,
) -> InboundPipeline:
    console = FakeConsoleClient()
    console.resolve_response = resolved_response()
    return InboundPipeline(
        dedupe=dedupe if dedupe is not None else InMemoryDedupeStore(),
        console=console,
        runtime=runtime,
        catalog=catalog,
        tenant_id="tenant-waiting",
        delta_flush_interval_sec=0.0,
        channel_delivery=channel_delivery,
        runtime_reconnect_attempts=attempts,
        runtime_reconnect_delay_sec=0.0,
    )


async def _wait_until(predicate: Any, *, what: str, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(what)


def test_waiting_tool_pauses_elapsed_and_resumed_starts_new_segment() -> None:
    """run.waiting 停本段计时、不伪造活动；run.resumed 开新段；总耗时按起止另算。"""
    now = [100.0]
    state = ExecutionProgress(clock=lambda: now[0])
    state.apply(SseEvent("run.created", {}))
    now[0] += 3.4
    state.apply(SseEvent("model.started", {}))
    assert state.phase is ProgressPhase.THINKING
    now[0] += 1.2
    state.apply(SseEvent("run.waiting_tool", {"status": "WAITING_TOOL"}))
    assert state.phase is ProgressPhase.WAITING_TOOL
    assert state.active, "等待不是终态：状态展示仍活着"
    assert state.elapsed_seconds == 4

    now[0] += 3600  # 等了一小时
    assert state.elapsed_seconds == 4, "等待空档不得计入执行计时"
    assert state.total_seconds == 3604, "总耗时按起止另算（含等待）"

    state.apply(SseEvent("run.resumed", {"execution_epoch": 2}))
    assert state.phase is ProgressPhase.THINKING
    now[0] += 2.0
    assert state.elapsed_seconds == 6, "接续后的新执行段继续累计"
    state.apply(SseEvent("run.completed", {"status": "COMPLETED", "final_text": "ok"}))
    now[0] += 100
    assert state.elapsed_seconds == 6
    assert state.total_seconds == 3606
    assert not state.active


async def test_sse_parser_keeps_waiting_and_async_metadata_event_types() -> None:
    """等待/接续与异步工具轮廓是已知事件：解析器不得静默丢弃（否则 seq 断档、重连失准）。"""
    steps = [
        "tool.submission_pending",
        "tool.submitted",
        "tool.result.received",
        "run.waiting_tool",
        "tool.result",
        "run.resumed",
    ]
    raw = "".join(
        "event: {type}\ndata: {payload}\n\n".format(
            type=event_type,
            payload=json.dumps(
                {
                    "run_id": "run-1",
                    "seq": index,
                    "timestamp": "2026-10-08T00:00:00+00:00",
                    "type": event_type,
                    "data": {},
                }
            ),
        )
        for index, event_type in enumerate(steps, start=1)
    )

    async def chunks():  # type: ignore[no-untyped-def]
        yield raw

    events = [event async for event in iter_sse_events(chunks())]
    assert [event.type for event in events] == steps
    assert [event.seq for event in events] == [1, 2, 3, 4, 5, 6]


async def test_waiting_keeps_reply_open_and_resume_streams_final_answer_once(
    catalog: MessageCatalog,
) -> None:
    release = asyncio.Event()

    class WaitingRuntime(FakeRuntimeClient):
        async def create_run(
            self,
            request,  # type: ignore[no-untyped-def]
            *,
            tenant_id: str,
            trace_id: str = "",
            idempotency_key: str | None = None,
        ):
            self.run_requests.append(request)
            yield SseEvent("run.created", {"run_id": "run-wait"}, seq=1)
            yield SseEvent("model.started", {}, seq=2)
            yield SseEvent("tool.started", {"tool_call_id": "c1"}, seq=3)
            yield SseEvent("run.waiting_tool", {"status": "WAITING_TOOL"}, seq=4)
            await release.wait()
            yield SseEvent("run.resumed", {"execution_epoch": 2}, seq=5)
            yield SseEvent("message.delta", {"delta": "最终"}, seq=6)
            yield SseEvent("run.completed", {"status": "COMPLETED", "final_text": "最终答案"}, seq=7)

    session = RecordingSession()
    adapter = SessionAdapter(session)
    pipeline = _pipeline(catalog, WaitingRuntime())
    task = asyncio.create_task(pipeline.handle(adapter, make_envelope("检查任务")))
    try:
        await _wait_until(
            lambda: any(WAITING_TEXT in status for status in session.statuses),
            what="等待态未呈现（状态帧缺失）",
        )
        assert session.finished == 0, "等待阶段不得发完成帧"
        assert session.streamed == [], "等待阶段不得输出正文"
        assert session.bound_run == "run-wait"
        release.set()
        assert await asyncio.wait_for(task, timeout=5.0) is True
    finally:
        release.set()
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
    assert session.finished == 1, "终态只收尾一次"
    assert session.texts == []
    assert "".join(session.streamed).endswith("最终")


async def test_broken_stream_reconnects_from_confirmed_seq_without_creating_run(
    catalog: MessageCatalog,
) -> None:
    class DroppingRuntime(FakeRuntimeClient):
        async def create_run(
            self,
            request,  # type: ignore[no-untyped-def]
            *,
            tenant_id: str,
            trace_id: str = "",
            idempotency_key: str | None = None,
        ):
            self.run_requests.append(request)
            yield SseEvent("run.created", {"run_id": "run-drop"}, seq=1)
            yield SseEvent("model.started", {}, seq=2)
            yield SseEvent("run.waiting_tool", {"status": "WAITING_TOOL"}, seq=3)
            # 等待期 socket 断开：流在这里结束（非终态、非等待输入）

    runtime = DroppingRuntime()
    runtime.reconnect_events = [
        SseEvent("run.resumed", {"execution_epoch": 2}, seq=4),
        SseEvent("message.delta", {"delta": "恢复答案"}, seq=5),
        SseEvent("run.completed", {"status": "COMPLETED", "final_text": "恢复答案"}, seq=6),
    ]
    session = RecordingSession()
    adapter = SessionAdapter(session)
    pipeline = _pipeline(catalog, runtime, attempts=2)

    assert await pipeline.handle(adapter, make_envelope("断流任务")) is True
    assert runtime.open_calls == [("run-drop", 3)], "重连必须从最后**已确认**的 seq 之后回放"
    assert len(runtime.run_requests) == 1, "重连不得重复 POST 创建 Run"
    assert session.finished == 1
    assert "恢复答案" in "".join(session.streamed)


async def test_reconnect_exhaustion_reports_broken_stream_once(catalog: MessageCatalog) -> None:
    runtime = FakeRuntimeClient(
        [
            SseEvent("run.created", {"run_id": "run-x"}, seq=1),
            SseEvent("message.delta", {"delta": "半句"}, seq=2),
        ]
    )
    session = RecordingSession()
    adapter = SessionAdapter(session)
    pipeline = _pipeline(catalog, runtime, attempts=2)

    assert await pipeline.handle(adapter, make_envelope("断流")) is True
    assert [call[0] for call in runtime.open_calls] == ["run-x", "run-x"]
    assert session.texts.count(BROKEN_STREAM_TEXT) == 1
    assert session.finished == 1


async def test_lost_reply_session_delivers_final_text_once_with_stable_key(
    catalog: MessageCatalog,
) -> None:
    runtime = FakeRuntimeClient(
        [
            SseEvent("run.created", {"run_id": "run-final"}, seq=1),
            SseEvent("message.delta", {"delta": "半句"}, seq=2),
            SseEvent("run.completed", {"status": "COMPLETED", "final_text": "完整答案"}, seq=3),
        ]
    )
    session = RecordingSession(fail_stream=True)
    adapter = SessionAdapter(session)
    protocol = FakeChannelDeliveryProtocol()
    delivery = ActiveChannelDelivery(protocol)
    pipeline = _pipeline(catalog, runtime, channel_delivery=delivery)

    assert await pipeline.handle(adapter, make_envelope("兜底")) is True
    assert [message.text for _route, message in adapter.sent_active] == ["完整答案"]
    assert adapter.streamed == () and adapter.sent == (), "失败会话不得再落路由级回调"
    # 同稳定键预留/确认：与 Runtime 持久通知/投递泵**同一个** delivery_key
    assert [request.delivery_key for request in protocol.reserves] == [
        terminal_delivery_key("run-final")
    ]
    assert [ack.delivery_key for ack in protocol.acks] == [terminal_delivery_key("run-final")]
    assert [ack.outcome for ack in protocol.acks] == ["DELIVERED"], "实际发送成功才 ack"
    # 同投递键重放：不再真发，但如实回报"已送达（去重）"。
    route = route_from_envelope(make_envelope("兜底"))
    assert (
        await delivery.deliver(
            adapter, route, tenant_id="tenant-waiting", run_id="run-final", text="完整答案"
        )
        == "deduplicated"
    )
    assert len(adapter.sent_active) == 1


async def test_expired_reply_session_falls_back_to_active_delivery(
    catalog: MessageCatalog,
) -> None:
    """回调已过期（open 失败）也是 ReplySession 失效：终态文本走主动投递，不冒充最新回调。"""
    runtime = FakeRuntimeClient(
        [
            SseEvent("run.created", {"run_id": "run-expired"}, seq=1),
            SseEvent("message.delta", {"delta": "半句"}, seq=2),
            SseEvent("run.completed", {"status": "COMPLETED", "final_text": "过期答案"}, seq=3),
        ]
    )
    adapter = ExpiredSessionAdapter()
    dedupe = InMemoryDedupeStore()
    pipeline = _pipeline(catalog, runtime, dedupe=dedupe)

    assert await pipeline.handle(adapter, make_envelope("过期")) is True
    assert [message.text for _route, message in adapter.sent_active] == ["过期答案"]
    assert adapter.streamed == () and adapter.sent == ()


async def test_missing_active_capability_is_an_explicit_failure() -> None:
    adapter = NoActiveAdapter()
    protocol = FakeChannelDeliveryProtocol()
    delivery = ActiveChannelDelivery(protocol)
    route = DeliveryRouteInput(
        channel="WECOM", bot_id="bot-1", external_user_id="ext-1", external_conversation_id="c"
    )
    assert (
        await delivery.deliver(
            adapter, route, tenant_id="tenant-1", run_id="run-no-cap", text="答案"
        )
        == "failed"
    )
    assert adapter.sent == []
    assert protocol.reserves == [], "没有发送能力就不该占投递资格"


async def test_active_send_failure_releases_the_claim_and_reports_failure() -> None:
    route = DeliveryRouteInput(
        channel="WECOM", bot_id="bot-1", external_user_id="ext-1", external_conversation_id="c"
    )
    protocol = FakeChannelDeliveryProtocol()
    delivery = ActiveChannelDelivery(protocol)
    failing = FailingActiveAdapter()
    assert (
        await delivery.deliver(
            failing, route, tenant_id="tenant-1", run_id="run-fail", text="答案"
        )
        == "failed"
    )
    # 没送达 ⇒ ack NOT_SENT（回到可重试状态），不宣称成功、也不留成功回执
    assert [ack.outcome for ack in protocol.acks] == ["NOT_SENT"]
    assert await delivery.deliver(
        failing, route, tenant_id="tenant-1", run_id="run-fail", text="答案"
    ) == "failed"
    # 能力恢复时仍可真实补发：重试拿到新租约、发送成功后冻结回执
    recovered = FakeChannelAdapter()
    assert (
        await delivery.deliver(
            recovered, route, tenant_id="tenant-1", run_id="run-fail", text="答案"
        )
        == "delivered"
    )
    assert [message.text for _route, message in recovered.sent_active] == ["答案"]
    assert [ack.outcome for ack in protocol.acks][-1] == "DELIVERED"
