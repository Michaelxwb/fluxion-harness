import asyncio
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

import pytest
from fakes import FakeConsoleClient, FakeRuntimeClient, FakeWeComSdkFactory, make_envelope, resolved_response
from muad_api.catalog import MessageCatalog
from muad_artifact_store import NfsArtifactStore
from muad_contracts import AttachmentRef, BotSnapshotItem, DeliveryRouteInput
from muad_contracts.platform_settings import parse_platform_settings
from muad_im_gateway.application.inbound import InboundPipeline
from muad_im_gateway.application.ports import PlatformSettingsSnapshot
from muad_im_gateway.application.progress import ExecutionProgress, ProgressPhase, iter_with_ticks
from muad_im_gateway.application.sse import SseEvent
from muad_im_gateway.channels.base import ARTIFACT_DELIVERED, ChannelAdapterUnavailable
from muad_im_gateway.channels.fake import FakeChannelAdapter
from muad_im_gateway.channels.wecom.adapter import WeComAdapter
from muad_im_gateway.channels.wecom.reply import ReplySession, StatusBudget
from muad_im_gateway.channels.wecom.sdk_port import WeComInboundMessage
from muad_im_gateway.infrastructure.dedupe import NullDedupeStore

from tests.e2e.wecom_probe_app import WeComProbe, frame_text
from tests.gateway.test_stream_renderer import (
    B115_BOT,
    B115_CHAT,
    B115_EXTERNAL_USER,
    _run_once,
)
from tests.gateway.test_stream_renderer import (
    adapter as adapter,
)
from tests.gateway.test_stream_renderer import (
    probe as probe,
)

#: 本文件里不经 WS 探针的适配器用例（回复目标/产物）用这套身份。
PROGRESS_BOT = "bot-progress"
PROGRESS_EXTERNAL_USER = "ext-progress"


def test_b401_real_elapsed_and_parallel_tools():
    now = [100.0]
    state = ExecutionProgress(clock=lambda: now[0])
    state.apply(SseEvent("run.created", {}))
    state.apply(SseEvent("model.started", {}))
    assert state.phase == ProgressPhase.THINKING
    now[0] += 2.9
    assert state.elapsed_seconds == 2
    state.apply(SseEvent("tool.started", {"tool_call_id": "a"}))
    state.apply(SseEvent("tool.started", {"tool_call_id": "b"}))
    state.apply(SseEvent("model.started", {}))
    assert state.phase == ProgressPhase.EXECUTING
    state.apply(SseEvent("tool.completed", {"tool_call_id": "a"}))
    assert state.phase == ProgressPhase.EXECUTING
    state.apply(SseEvent("tool.completed", {"tool_call_id": "b"}))
    assert state.phase == ProgressPhase.THINKING
    state.apply(SseEvent("run.completed", {"status": "COMPLETED"}))
    now[0] += 100
    assert state.elapsed_seconds == 2
    state.apply(SseEvent("model.started", {}))
    assert not state.active


async def test_b401_ticks_do_not_cancel_slow_event_or_leak_reader():
    closed = asyncio.Event()

    async def events():
        try:
            await asyncio.sleep(0.04)
            yield SseEvent("model.started", {})
        finally:
            closed.set()

    stream = iter_with_ticks(events(), interval=0.01)
    items = [item async for item in stream]
    assert any(item is None for item in items)
    assert [item.type for item in items if item is not None] == ["model.started"]
    assert closed.is_set()


class Sdk:
    def __init__(self):
        self.frames = []
        self.replied_texts = []

    async def send_stream(self, reply_id, stream_id, content, *, finish):
        self.frames.append((reply_id, stream_id, content, finish))

    async def reply_text(self, reply_id, text):
        self.replied_texts.append((reply_id, text))


async def test_b402_status_replaces_and_final_body_excludes_thinking():
    sdk = Sdk()
    reply = ReplySession(client=lambda: sdk, reply_ref="original", flush_interval_sec=0)
    await reply.update_status("🔵 思考中 · 已执行 00:00")
    await reply.update_status("⚙️ 执行中 · 已执行 00:01")
    await reply.stream("hello")
    await reply.stream(" world")
    await reply.finish()
    await reply.finish()
    assert sdk.frames[0][2].startswith("<think>")
    assert "00:00" not in sdk.frames[1][2]
    assert sdk.frames[-1][2] == "hello world"
    assert sdk.frames[-1][3] is True
    assert len({row[1] for row in sdk.frames}) == 1
    assert len(sdk.frames) == 5
    await reply.update_status("stale")
    assert len(sdk.frames) == 5


def test_b401_budget_caps_refreshes_and_refills_without_backlog():
    now = [0.0]
    budget = StatusBudget(2, clock=lambda: now[0])
    assert [budget.take() for _ in range(10)].count(True) == 2
    now[0] = 100
    assert [budget.take() for _ in range(10)].count(True) == 2


@pytest.mark.parametrize(
    ("terminal", "data", "expected"),
    [
        ("run.completed", {"status": "CANCELLED"}, "当前任务已停止"),
        ("run.failed", {"error_code": "MODEL_UNAVAILABLE"}, "模型服务暂时不可用"),
        ("interrupt.required", {"prompt": "请确认", "options": ["继续", "停止"]}, "继续"),
        ("task.accepted", {}, "任务已受理"),
    ],
)
async def test_e401_pipeline_closes_real_reply_on_stop_or_wait(
    adapter: WeComAdapter,
    probe: WeComProbe,
    catalog: MessageCatalog,
    terminal: str,
    data: dict[str, object],
    expected: str,
):
    frames = await _run_once(
        adapter,
        probe,
        catalog,
        [("run.created", {}), ("model.started", {}), (terminal, data)],
        expect=expected,
    )
    streams = [frame for frame in frames if frame.get("body", {}).get("msgtype") == "stream"]
    assert any("思考中" in frame_text(frame) for frame in streams)
    assert sum(bool(frame["body"]["stream"].get("finish")) for frame in streams) == 1
    assert not adapter._replies
    assert not streams[-1]["body"]["stream"]["content"].startswith("<think>")


async def test_e401_pipeline_eof_is_failure_and_closes_reply(
    adapter: WeComAdapter,
    probe: WeComProbe,
    catalog: MessageCatalog,
):
    frames = await _run_once(
        adapter, probe, catalog, [("run.created", {}), ("model.started", {})], expect="服务暂时中断"
    )
    streams = [frame for frame in frames if frame.get("body", {}).get("msgtype") == "stream"]
    assert streams[-1]["body"]["stream"]["finish"] is True
    assert "服务暂时中断" in frame_text(streams[-1])
    assert not adapter._replies


async def test_b402_reply_session_keeps_original_callback_when_route_changes(
    adapter: WeComAdapter,
    probe: WeComProbe,
):
    events = await adapter.iter_events()
    for message_id in ("first", "second"):
        await probe.push_message(
            bot_id=B115_BOT,
            message_id=message_id,
            external_user_id=B115_EXTERNAL_USER,
            chat_id=B115_CHAT,
            text="检查",
            reply_id=f"req-{message_id}",
        )
        await asyncio.wait_for(anext(events), 5)
    route = DeliveryRouteInput(
        channel="WECOM",
        bot_id=B115_BOT,
        external_user_id=B115_EXTERNAL_USER,
        external_conversation_id=B115_CHAT,
    )
    reply = adapter.open_reply(route, "first")
    await reply.update_status("🔵 思考中 · 已执行 00:00")
    await reply.stream("完成")
    await reply.finish()
    frames = probe.frames_of("aibot_respond_msg")
    assert frames
    assert {frame["headers"]["req_id"] for frame in frames} == {"req-first"}
    assert len({frame["body"]["stream"]["id"] for frame in frames}) == 1
    assert frame_text(frames[-1]) == "完成"
    assert frames[-1]["body"]["stream"]["finish"] is True


async def test_b401_cancelling_ticks_closes_pending_reader():
    closed = asyncio.Event()

    async def events():
        try:
            await asyncio.Event().wait()
            yield SseEvent("model.started", {})
        finally:
            closed.set()

    stream = iter_with_ticks(events(), interval=0.001)
    assert await anext(stream) is None
    await stream.aclose()
    assert closed.is_set()


async def test_b401_budget_exhaustion_never_blocks_body_or_finish():
    sdk = Sdk()
    reply = ReplySession(
        client=lambda: sdk,
        reply_ref="budget",
        budget=StatusBudget(1, clock=lambda: 0.0),
        flush_interval_sec=0,
    )
    await reply.update_status("first")
    await reply.update_status("skipped")
    await reply.stream("answer")
    await reply.finish()
    assert [frame[2] for frame in sdk.frames] == ["<think>first</think>", "answer", "answer"]
    assert sdk.frames[-1][3]


def test_b401_replay_restores_elapsed_and_wait_does_not_grow():
    from datetime import UTC, datetime, timedelta

    timestamp = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()
    now = [100.0]
    state = ExecutionProgress(clock=lambda: now[0])
    state.apply(SseEvent("run.created", {}, timestamp=timestamp))
    assert state.elapsed_seconds == 5
    state.apply(SseEvent("interrupt.required", {}))
    now[0] += 3600
    assert state.elapsed_seconds == 5
    resumed = ExecutionProgress(clock=lambda: now[0])
    resumed.apply(SseEvent("run.created", {"resumed": True}))
    assert resumed.elapsed_seconds == 0


async def test_e401_rejected_status_does_not_abort_run_or_leak_session(
    adapter: WeComAdapter,
    probe: WeComProbe,
    catalog: MessageCatalog,
    caplog: pytest.LogCaptureFixture,
):
    probe.fail_reply_bots.add(B115_BOT)
    frames = await _run_once(
        adapter,
        probe,
        catalog,
        [
            ("run.created", {}),
            ("model.started", {}),
            ("tool.started", {"tool_call_id": "work"}),
            ("run.completed", {"status": "COMPLETED", "final_text": "仍然完成"}),
        ],
        expect="仍然完成",
    )
    assert any("仍然完成" in frame_text(frame) for frame in frames)
    assert "channel_status_update_failed" in caplog.text
    assert not adapter._replies


@pytest.mark.parametrize("rate", [0, 0.5, -1])
def test_b401_rejects_unusable_status_rate(rate):
    with pytest.raises(ValueError):
        StatusBudget(rate)


class SlowReplySession:
    """回复会话替身：**第 `arm_after` 次写入起卡住**，用来验证「写者唯一」与「tick 不排队」。

    卡住点由写入次数决定（不是由测试掐时机），所以用例是确定的。
    """

    def __init__(self, *, arm_after: int) -> None:
        self.statuses: list[str] = []
        self.streamed: list[str] = []
        self.texts: list[str] = []
        self.finished = 0
        self.bound_run: str | None = None
        self.armed = asyncio.Event()
        self.gate = asyncio.Event()
        self.writing = 0
        self.max_writing = 0
        self._arm_after = arm_after
        self._writes = 0

    def bind_run(self, run_id: str) -> None:
        self.bound_run = run_id

    async def _write(self) -> None:
        self._writes += 1
        self.writing += 1
        self.max_writing = max(self.max_writing, self.writing)
        try:
            if self._writes >= self._arm_after:
                self.armed.set()
                await self.gate.wait()
        finally:
            self.writing -= 1

    async def update_status(self, text: str) -> None:
        self.statuses.append(text)
        await self._write()

    async def stream(self, text: str) -> None:
        self.streamed.append(text)
        await self._write()

    async def send(self, text: str) -> None:
        self.texts.append(text)
        await self._write()

    async def finish(self) -> None:
        self.finished += 1
        await self._write()


class ReplyAdapter(FakeChannelAdapter):
    """实现了可选回复会话能力的渠道（`ReplySessionFactory`）。"""

    def __init__(self, session: SlowReplySession) -> None:
        super().__init__()
        self.session = session
        self.opened: list[str] = []

    def open_reply(self, route: DeliveryRouteInput, message_id: str) -> SlowReplySession:
        self.opened.append(message_id)
        return self.session


class IdleRuntime(FakeRuntimeClient):
    """给完起始事件就**停住**（等测试放行）：模拟真实运行中的空档，让 tick 有机会开火。"""

    def __init__(self, events: list[SseEvent], release: asyncio.Event) -> None:
        super().__init__(events)
        self._release = release

    async def create_run(self, request, *, tenant_id: str, trace_id: str = ""):  # type: ignore[no-untyped-def]
        self.run_requests.append(request)
        for event in self.events:
            yield event
        await self._release.wait()
        yield SseEvent("message.delta", {"delta": "答案"})
        yield SseEvent("run.completed", {"status": "COMPLETED", "final_text": "答案"})


class _StaticSettingsClient:
    """固定节拍的设置源：快照里的 `im.progress_interval_sec` 取自平台设置 schema（下界 1.0）。"""

    def __init__(self, interval: float) -> None:
        self._interval = interval

    async def fetch_snapshot(
        self, *, tenant_id: str, trace_id: str = ""
    ) -> PlatformSettingsSnapshot:
        return PlatformSettingsSnapshot(
            revision=1, settings={"im": {"progress_interval_sec": self._interval}}
        )


def _pipeline_with_progress(
    catalog: MessageCatalog, runtime: FakeRuntimeClient, interval: float
) -> InboundPipeline:
    console = FakeConsoleClient()
    console.resolve_response = resolved_response()
    return InboundPipeline(
        dedupe=NullDedupeStore(),
        console=console,
        runtime=runtime,
        catalog=catalog,
        tenant_id="tenant-1",
        settings_client=_StaticSettingsClient(interval),
        delta_flush_interval_sec=0.0,  # 增量立刻成帧：正文写入紧随"隐藏占位"的那一帧
    )


def test_b401_progress_interval_default_is_five_seconds():
    """生产默认节拍只有一个来源：平台设置 schema 的 `im.progress_interval_sec`（=5.0）。

    E2E 用 1s 只证明「计时在走、秒数在涨」这条规律；「生产默认是 5 秒」这条**常量事实**
    在这里钉——它同时也是客户端重排成本的取舍，改它要有意识地改（改 schema 默认即改此断言）。
    """
    assert parse_platform_settings({}).im.progress_interval_sec == 5.0
    assert (
        parse_platform_settings({"im": {"progress_interval_sec": 1.0}}).im.progress_interval_sec
        == 1.0
    )


async def test_b402_identical_status_frame_is_not_re_sent():
    """一字不差的状态帧不再重发：客户端白重排一次、滚动一次（实测 run.created 会紧跟着
    起始帧再发一遍同样的「准备中」）。内容变了才发。"""
    sdk = Sdk()
    reply = ReplySession(client=lambda: sdk, reply_ref="dedup", flush_interval_sec=0)
    await reply.update_status("🔵 准备中 · 已执行 00:00")
    await reply.update_status("🔵 准备中 · 已执行 00:00")
    await reply.update_status("🔵 思考中 · 已执行 00:00")
    assert [frame[2] for frame in sdk.frames] == [
        "<think>🔵 准备中 · 已执行 00:00</think>",
        "<think>🔵 思考中 · 已执行 00:00</think>",
    ]


def test_b401_never_falls_back_to_preparing_after_execution_started():
    """执行一旦开始就不再显示「准备中」。

    模型结束到工具开始之间落回 PREPARING 会让阶段一路跳（思考中→准备中→执行中→准备中），
    而每次跳都是一次整帧重排。
    """
    state = ExecutionProgress(clock=lambda: 100.0)
    state.apply(SseEvent("run.created", {}))
    assert state.phase == ProgressPhase.PREPARING

    state.apply(SseEvent("model.started", {}))
    assert state.phase == ProgressPhase.THINKING
    state.apply(SseEvent("model.completed", {}))
    assert state.phase == ProgressPhase.THINKING

    state.apply(SseEvent("tool.started", {"tool_call_id": "t1"}))
    assert state.phase == ProgressPhase.EXECUTING
    state.apply(SseEvent("tool.completed", {"tool_call_id": "t1"}))
    assert state.phase == ProgressPhase.THINKING


async def test_b401_in_flight_status_never_shares_the_stream_with_the_answer(
    catalog: MessageCatalog,
):
    """PROGRESS-01「同一 session 不并发写入」：在飞的计时状态没落地之前，正文不得开始写。

    信道是一条有序流（状态帧与正文帧共用同一个 stream id），两个写者交错会让正文被占位盖回去。
    """
    session = SlowReplySession(arm_after=4)  # 前三次是主路径状态（准备中/run.created/思考中）
    adapter = ReplyAdapter(session)
    release = asyncio.Event()
    runtime = IdleRuntime(
        [SseEvent("run.created", {"run_id": "run-1"}), SseEvent("model.started", {})],
        release,
    )
    pipeline = _pipeline_with_progress(catalog, runtime, interval=1.0)
    consumer = asyncio.create_task(pipeline.consume(adapter))
    try:
        await adapter.push(make_envelope("检查设备"))
        await asyncio.wait_for(session.armed.wait(), 5)
        assert session.bound_run == "run-1", "回复会话必须绑到起它的 Run 上"

        in_flight = len(session.statuses)
        release.set()
        await asyncio.sleep(0.2)  # 正文与终态都已就绪，但状态发送还卡着
        assert session.streamed == [] and session.texts == [], "状态未落地就抢先写了正文"
        assert session.max_writing == 1, "同一会话出现并发写入"
        assert len(session.statuses) <= in_flight + 1, "tick 积压了：发送者忙时必须整条丢弃"

        session.gate.set()
        await _wait_until(lambda: session.finished, what="收尾未落地")
        assert "答案" in "".join(session.streamed) + "".join(session.texts)
        assert session.max_writing == 1
    finally:
        session.gate.set()  # 断言失败时也别让卡住的写吊住拆除
        consumer.cancel()
        with suppress(asyncio.CancelledError):
            await consumer


async def _wait_until(predicate, *, what: str, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(what)


async def test_b402_finish_without_body_removes_placeholder_instead_of_shipping_status():
    """空正文的终态：收尾把占位抹掉，**不得**把状态串当正文发出去（SESSION-01 / B-402）。"""
    sdk = Sdk()
    reply = ReplySession(client=lambda: sdk, reply_ref="empty-body", flush_interval_sec=0)
    await reply.update_status("✅ 已完成 · 用时 00:03")
    await reply.finish()
    assert sdk.frames[-1][2] == ""
    assert sdk.frames[-1][3] is True


class StallingSdk(Sdk):
    """状态帧不回执：模拟通道侧挂住（正文帧照常返回）。"""

    async def send_stream(self, reply_id, stream_id, content, *, finish):
        if content.startswith("<think>"):
            await asyncio.Event().wait()
        await super().send_stream(reply_id, stream_id, content, finish=finish)


async def test_b401_stalled_status_send_is_bounded_and_never_blocks_the_body():
    """设计 §3.4：计时不得阻塞真实结果——慢/挂住的状态发送必须被**有界**放弃。"""
    sdk = StallingSdk()
    reply = ReplySession(
        client=lambda: sdk, reply_ref="stalled", flush_interval_sec=0, status_timeout_sec=0.05
    )
    await asyncio.wait_for(reply.update_status("🔵 思考中 · 已执行 00:01"), 1)
    await asyncio.wait_for(reply.stream("答案"), 1)
    await asyncio.wait_for(reply.finish(), 1)
    assert [frame[2] for frame in sdk.frames] == ["答案", "答案"]
    assert sdk.frames[-1][3] is True


async def test_b402_post_finish_text_degrades_to_independent_reply():
    """收尾后仍收到的文本（停机收尾竞态）走独立会话回复：不静默丢，也不重开正文流。"""
    sdk = Sdk()
    reply = ReplySession(client=lambda: sdk, reply_ref="late", flush_interval_sec=0)
    await reply.stream("正文")
    await reply.finish()
    frames_before = list(sdk.frames)
    await reply.send("迟到的文本")
    assert sdk.replied_texts == [("late", "迟到的文本")]
    assert sdk.frames == frames_before


class ClosingIterator:
    """非生成器的 `AsyncIterator`：端口只保证 `__anext__`，但实现同样可能持有连接要关。"""

    def __init__(self) -> None:
        self._events = [SseEvent("model.started", {})]
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self) -> SseEvent:
        if not self._events:
            raise StopAsyncIteration
        return self._events.pop(0)

    async def aclose(self) -> None:
        self.closed = True


async def test_b401_ticks_close_upstream_even_when_it_is_not_a_generator():
    upstream = ClosingIterator()
    assert [item async for item in iter_with_ticks(upstream, interval=1.0)] != []
    assert upstream.closed is True


def _artifact_ref(storage_key: str) -> AttachmentRef:
    return AttachmentRef(
        storage_key=storage_key,
        kind="DOCUMENT",  # type: ignore[arg-type]
        media_type="text/markdown",
        size=7,
        filename="汇总.md",
        checksum="sha256:" + "0" * 64,
        artifact_id=uuid4(),
    )


def _inbound(message_id: str, *, reply_id: str) -> WeComInboundMessage:
    return WeComInboundMessage(
        bot_id=PROGRESS_BOT,
        message_id=message_id,
        external_user_id=PROGRESS_EXTERNAL_USER,
        external_conversation_id="conv-progress",
        message_type="text",
        text="检查设备",
        reply_id=reply_id,
    )


async def test_b402_artifact_replies_to_the_message_that_started_its_run(tmp_path: Path) -> None:
    """后到的入站消息只顶掉路由级「最新回调」，产物必须回到**起这个 Run 的那条消息**上。

    没有它，长任务的产物会挂到同会话后来那条消息的回调上（spec「新入站消息不能覆盖旧任务的
    回复目标」）。键里没有 Run（后台任务投递）时才退回最新回调。
    """
    factory = FakeWeComSdkFactory()
    store = NfsArtifactStore(tmp_path)
    adapter = WeComAdapter(
        sdk_factory=factory,
        bots=[
            BotSnapshotItem(
                bot_account_id=uuid4(),
                bot_id=PROGRESS_BOT,
                secret="progress-secret",
                agent_id=uuid4(),
            )
        ],
        artifact_store=store,
        backoff_base_sec=0.01,
        backoff_max_sec=0.02,
    )
    await adapter.start()
    try:
        client = factory.latest()
        client.push_message(_inbound("first", reply_id="req-first"))
        client.push_message(_inbound("second", reply_id="req-second"))
        route = DeliveryRouteInput(
            channel="WECOM",
            bot_id=PROGRESS_BOT,
            external_user_id=PROGRESS_EXTERNAL_USER,
            external_conversation_id="conv-progress",
        )
        session = adapter.open_reply(route, "first")
        session.bind_run("run-1")
        storage_key = "outbound/run-1/artifact-1/v1"
        target = store.resolve(storage_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"payload")

        with_run = await adapter.deliver_artifact(
            route, _artifact_ref(storage_key), tenant_id="tenant-1", run_id="run-1"
        )
        without_run = await adapter.deliver_artifact(
            route, _artifact_ref(storage_key), tenant_id="tenant-1"
        )

        assert with_run.outcome == ARTIFACT_DELIVERED
        assert without_run.outcome == ARTIFACT_DELIVERED
        assert [row[0] for row in client.replied_media] == ["req-first", "req-second"]
        assert client.sent_media == []
    finally:
        await adapter.stop()


async def test_b402_a_finished_runs_artifact_does_not_jump_to_another_message(
    tmp_path: Path,
) -> None:
    """Run **收尾之后**，它的产物仍然回到起它的那条消息（2026-10-06）。

    原实现里会话一收尾就从活跃集合消失，`_reply_target` 于是退回"本路由最新回调" —— 实测：
    old-run 的交付目标变成了后来那条消息的 req_id（老任务的产物挂到了别人的消息上）。
    保留窗口内的映射仍然有效；真过期了就**明确降级为主动投递**，而不是冒充某条消息的回调。
    """
    factory = FakeWeComSdkFactory()
    store = NfsArtifactStore(tmp_path)
    adapter = WeComAdapter(
        sdk_factory=factory,
        bots=[
            BotSnapshotItem(
                bot_account_id=uuid4(),
                bot_id=PROGRESS_BOT,
                secret="progress-secret",
                agent_id=uuid4(),
            )
        ],
        artifact_store=store,
        backoff_base_sec=0.01,
        backoff_max_sec=0.02,
    )
    await adapter.start()
    try:
        client = factory.latest()
        client.push_message(_inbound("first", reply_id="req-first"))
        client.push_message(_inbound("second", reply_id="req-second"))
        route = DeliveryRouteInput(
            channel="WECOM",
            bot_id=PROGRESS_BOT,
            external_user_id=PROGRESS_EXTERNAL_USER,
            external_conversation_id="conv-progress",
        )
        session = adapter.open_reply(route, "first")
        session.bind_run("run-1")
        await session.finish()  # Run 收尾：会话结束

        storage_key = "outbound/run-1/artifact-1/v1"
        target = store.resolve(storage_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"payload")

        closed_run = await adapter.deliver_artifact(
            route, _artifact_ref(storage_key), tenant_id="tenant-1", run_id="run-1"
        )
        assert closed_run.outcome == ARTIFACT_DELIVERED
        assert [row[0] for row in client.replied_media] == ["req-first"], (
            "收尾后的产物必须回到起这个 Run 的那条消息，而不是本路由最新回调"
        )

        # 映射已过期（不认识这个 Run）：**降级为主动投递**，不冒充任何一条消息的回调
        expired = await adapter.deliver_artifact(
            route, _artifact_ref(storage_key), tenant_id="tenant-1", run_id="run-unknown"
        )
        assert expired.outcome == ARTIFACT_DELIVERED
        assert [row[0] for row in client.replied_media] == ["req-first"], "不得挂到别的消息上"
        assert [row[0] for row in client.sent_media] == ["conv-progress"], "走主动投递（发给会话）"
    finally:
        await adapter.stop()


async def test_b402_a_failed_finish_frame_is_retried_instead_of_written_off() -> None:
    """终帧发送失败 ⇒ **不关闭会话、有界重试**（2026-10-06）。

    原实现先置 `_closed = True` 再发终帧：发送一失败，会话已关，之后任何 `finish()` 都直接返回
    —— 一次瞬时断线就让这条回复永远停在未收尾状态（客户端一直挂着占位），而且没有任何补偿路径
    （实测：终帧总共只尝试过一次）。
    """
    client = Sdk()
    attempts: list[bool] = []
    original = client.send_stream

    async def flaky(reply_id, stream_id, content, *, finish):  # type: ignore[no-untyped-def]
        attempts.append(finish)
        if finish and attempts.count(True) == 1:
            raise RuntimeError("transient disconnect")
        await original(reply_id, stream_id, content, finish=finish)

    client.send_stream = flaky  # type: ignore[method-assign]
    reply = ReplySession(
        client=lambda: client,
        reply_ref="req",
        flush_interval_sec=0,
        finish_retry_delay_sec=0,
    )
    await reply.stream("answer")

    await reply.finish()

    assert attempts.count(True) == 2, "终帧必须重试一次并成功"
    assert client.frames[-1][2] == "answer" and client.frames[-1][3] is True


async def test_b402_a_finish_that_never_succeeds_stays_reported_not_silently_closed() -> None:
    """重试仍失败 ⇒ **不假装已收尾**（不置 `_closed`），并把失败显式抛给调用方。"""
    client = Sdk()

    async def always_fail(reply_id, stream_id, content, *, finish):  # type: ignore[no-untyped-def]
        if finish:
            raise RuntimeError("still down")
        client.frames.append((reply_id, stream_id, content, finish))

    client.send_stream = always_fail  # type: ignore[method-assign]
    reply = ReplySession(
        client=lambda: client,
        reply_ref="req",
        flush_interval_sec=0,
        finish_retry_delay_sec=0,
    )
    await reply.stream("answer")

    with pytest.raises(ChannelAdapterUnavailable):
        await reply.finish()

    assert reply._closed is False  # noqa: SLF001 - 收尾没成功就不算关闭
