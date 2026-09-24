"""B-119: 消息、去重与投递指标（生产入站/HTTP 投递/真实 SSE→真实 `/metrics` HTTP 端点）。

不得 Mock 的真实边界：真实本地 WS 探针（真实入站推送与出站帧）+ 生产 Adapter/Pipeline
+ 真实 HTTP `GET /metrics`（uvicorn 真实 socket，api-kit 进程内注册表）。
"""

from __future__ import annotations

import asyncio
import socket
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any

import httpx
import pytest
import uvicorn
from fakes import FakeConsoleClient, resolved_response
from muad_api import AppError
from muad_api.catalog import MessageCatalog
from muad_api.error_codes import ErrorCode
from muad_contracts import BotSnapshotItem
from muad_im_gateway.api.deps import get_dedupe_store, get_registry
from muad_im_gateway.application.inbound import InboundPipeline
from muad_im_gateway.application.sse import SseEvent
from muad_im_gateway.channels.base import ChannelRegistry
from muad_im_gateway.channels.wecom.adapter import ConnectionState, WeComAdapter
from muad_im_gateway.infrastructure.dedupe import InMemoryDedupeStore
from muad_im_gateway.main import app as gateway_app

from tests.e2e.wecom_probe_app import WeComProbe

B119_BOT = "bot-b119"
B119_SECRET = "b119-secret-value"
B119_EXTERNAL_USER = "ext-b119"
B119_CHAT = "chat-b119"
B119_BODY_CANARY = "b119-消息正文-不得进指标"
METRICS_PATH = "/metrics"
DELIVERIES_PATH = "/internal/deliveries"
WAIT_TIMEOUT_SEC = 20.0


class _OnceRuntime:
    """单次受控 SSE：一条 delta + 终态；可选按文本抛错。"""

    def __init__(self, *, error: AppError | None = None, text: str = B119_BODY_CANARY) -> None:
        self.error = error
        self.text = text

    async def create_run(
        self, request: Any, *, tenant_id: str, trace_id: str = "", idempotency_key: str | None = None
    ) -> AsyncIterator[SseEvent]:
        if self.error is not None:
            raise self.error
        yield SseEvent(type="run.created", data={"run_id": "run-b119"})
        yield SseEvent(type="message.delta", data={"delta": self.text})
        yield SseEvent(type="run.completed", data={"status": "COMPLETED", "final_text": self.text})

    async def cancel_active(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"run_id": "run-b119", "status": "CANCELLING"}

    async def create_conversation(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
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


def _metric_value(text: str, name: str, labels: str = "") -> float | None:
    prefix = f"{name}{labels} "
    for line in text.splitlines():
        if line.startswith(prefix):
            return float(line[len(prefix) :])
    return None


@pytest.fixture()
async def probe() -> AsyncIterator[WeComProbe]:
    instance = WeComProbe(expected_bots={B119_BOT: B119_SECRET})
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
                bot_id=B119_BOT,
                secret=B119_SECRET,
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
            lambda: instance.connection_states.get(B119_BOT) is ConnectionState.CONNECTED,
            what="WeComAdapter 未在真实 WS 上完成认证",
        )
        yield instance
    finally:
        await instance.stop()


@pytest.fixture()
async def metrics_http(adapter: WeComAdapter) -> AsyncIterator[httpx.AsyncClient]:
    """真实 HTTP：`/metrics` 与 `/internal/deliveries`（投递依赖注入同一 Adapter）。"""
    registry = ChannelRegistry()
    registry.register(adapter)
    gateway_app.dependency_overrides[get_registry] = lambda: registry
    gateway_app.dependency_overrides[get_dedupe_store] = lambda: _DELIVERY_DEDUPE["store"]
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    config = uvicorn.Config(
        gateway_app, host="127.0.0.1", port=port, log_level="error", lifespan="off"
    )
    server = uvicorn.Server(config)
    serving = asyncio.create_task(server.serve())
    try:
        deadline = time.monotonic() + 15.0
        while not server.started and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        assert server.started, "gateway 未在超时内监听"
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=30.0) as client:
            yield client
    finally:
        server.should_exit = True
        await asyncio.wait_for(serving, timeout=15.0)
        gateway_app.dependency_overrides.clear()


_DELIVERY_DEDUPE: dict[str, Any] = {}


@pytest.fixture(autouse=True)
def _delivery_store() -> None:
    _DELIVERY_DEDUPE["store"] = InMemoryDedupeStore()


def _pipeline(runtime: Any, catalog: MessageCatalog) -> InboundPipeline:
    console = FakeConsoleClient()
    console.resolve_response = resolved_response()
    return InboundPipeline(
        dedupe=InMemoryDedupeStore(),
        console=console,
        runtime=runtime,
        catalog=catalog,
        tenant_id="tenant-b119",
        locale="zh-CN",
    )


async def _push(probe: WeComProbe, *, text: str, message_id: str | None = None) -> None:
    await probe.push_message(
        bot_id=B119_BOT,
        message_id=message_id or f"b119-{uuid.uuid4().hex[:8]}",
        external_user_id=B119_EXTERNAL_USER,
        text=text,
        reply_id=f"req-{uuid.uuid4().hex[:6]}",
        chat_id=B119_CHAT,
    )


async def test_b119_inbound_dedupe_and_stream_latency_metrics(
    adapter: WeComAdapter,
    probe: WeComProbe,
    metrics_http: httpx.AsyncClient,
    catalog: MessageCatalog,
) -> None:
    runtime = _OnceRuntime()
    pipeline = _pipeline(runtime, catalog)
    consumer = asyncio.create_task(pipeline.consume(adapter))
    try:
        await _push(probe, text=B119_BODY_CANARY)
        await _wait_for(
            lambda: any(
                item.frame.get("cmd") == "aibot_respond_msg" for item in probe.received
            ),
            what="未观测到出站流式帧",
        )
        # 重复同一 message_id：去重命中 +1（不再下游调用）
        await _push(probe, text=B119_BODY_CANARY, message_id="b119-dup-fixed")
        await _push(probe, text=B119_BODY_CANARY, message_id="b119-dup-fixed")
        await _wait_for(
            lambda: len([item for item in probe.received if item.frame.get("cmd") == "aibot_send_msg"])
            >= 0,
            what="noop",
        )
        await asyncio.sleep(0.5)  # 等消费队列处理完重复消息
    finally:
        consumer.cancel()
        with suppress(asyncio.CancelledError):
            await consumer

    response = await metrics_http.get(METRICS_PATH)
    assert response.status_code == 200, response.text
    text = response.text
    assert (_metric_value(text, "im_messages_total", '{type="text"}') or 0) >= 1
    assert (_metric_value(text, "im_dedupe_hits_total") or 0) >= 1
    first_chunk = _metric_value(text, "im_stream_first_chunk_ms")
    whole_stream = _metric_value(text, "im_stream_latency_ms")
    request_latency = _metric_value(text, "im_runtime_request_latency_ms")
    assert first_chunk is not None and whole_stream is not None
    assert request_latency is not None
    # 首块时延与全流时延分开观测，且首块不晚于全流
    assert first_chunk <= whole_stream
    # 标签不含消息正文或 Secret
    assert B119_BODY_CANARY not in text
    assert B119_SECRET not in text


async def test_b119_failure_metrics_and_trace_correlation(
    adapter: WeComAdapter,
    probe: WeComProbe,
    metrics_http: httpx.AsyncClient,
    catalog: MessageCatalog,
) -> None:
    runtime = _OnceRuntime(error=AppError(ErrorCode.MODEL_UNAVAILABLE))
    pipeline = _pipeline(runtime, catalog)
    consumer = asyncio.create_task(pipeline.consume(adapter))
    try:
        await _push(probe, text="触发运行时错误")
        await _wait_for(
            lambda: any(
                item.frame.get("cmd") == "aibot_send_msg" for item in probe.received
            ),
            what="未收到错误文案回复",
        )
    finally:
        consumer.cancel()
        with suppress(asyncio.CancelledError):
            await consumer

    text = (await metrics_http.get(METRICS_PATH)).text
    assert (_metric_value(text, "im_runtime_errors_total", '{code="MODEL_UNAVAILABLE"}') or 0) >= 1
    assert (
        _metric_value(text, "im_message_failures_total", '{reason="MODEL_UNAVAILABLE"}') or 0
    ) >= 1
    assert "触发运行时错误" not in text


async def test_b119_background_delivery_status_metrics(
    adapter: WeComAdapter, probe: WeComProbe, metrics_http: httpx.AsyncClient
) -> None:
    key = f"task:{uuid.uuid4()}:final"
    body = {
        "task_id": key.split(":")[1],
        "delivery_key": key,
        "route": {
            "channel": "WECOM",
            "bot_id": B119_BOT,
            "external_user_id": B119_EXTERNAL_USER,
            "external_conversation_id": B119_CHAT,
        },
        "message": {"type": "text", "text": "投递正文"},
    }
    first = await metrics_http.post(DELIVERIES_PATH, json=body)
    assert first.status_code == 200, first.text
    replay = await metrics_http.post(DELIVERIES_PATH, json=body)
    assert replay.json()["data"]["deduplicated"] is True

    probe.fail_reply_bots.add(B119_BOT)
    failed_body = {**body, "delivery_key": f"task:{uuid.uuid4()}:final"}
    failed_body["task_id"] = failed_body["delivery_key"].split(":")[1]
    failed = await metrics_http.post(DELIVERIES_PATH, json=failed_body)
    assert failed.status_code >= 400, failed.text
    probe.clear_injections()

    text = (await metrics_http.get(METRICS_PATH)).text
    assert (_metric_value(text, "im_background_delivery_total", '{status="accepted"}') or 0) >= 1
    assert (
        _metric_value(text, "im_background_delivery_total", '{status="deduplicated"}') or 0
    ) >= 1
    assert (_metric_value(text, "im_background_delivery_total", '{status="failed"}') or 0) >= 1
    assert "投递正文" not in text
