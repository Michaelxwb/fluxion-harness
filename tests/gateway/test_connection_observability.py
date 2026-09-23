"""B-118: 连接状态指标与脱敏日志（真实连接迁移→生产日志 + 真实 `/metrics` HTTP 端点）。

不得 Mock 的真实边界：生产 `WeComAdapter` → 官方 SDK → 真实本地 WS 探针（真实 TLS 连接
迁移）+ 真实 HTTP `GET /metrics`（uvicorn 真实 socket，api-kit 注册表）。
"""

from __future__ import annotations

import asyncio
import logging
import socket
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import uvicorn
from muad_api.context import set_request_context
from muad_contracts import BotSnapshotItem
from muad_im_gateway.channels.wecom.adapter import ConnectionState, WeComAdapter
from muad_im_gateway.channels.wecom.sdk_port import WeComSdkConnectionError
from muad_im_gateway.main import app

from tests.e2e.wecom_probe_app import WeComProbe

B118_GOOD_BOT = "bot-b118-good"
B118_BAD_BOT = "bot-b118-bad"
B118_SECRET = "b118-secret-value"
SECRET_CANARY = "b118-secret-canary-do-not-log"
METRICS_PATH = "/metrics"
GAUGE_NAME = "wecom_ws_connected"
WAIT_TIMEOUT_SEC = 20.0
LOG_PREFIX = "wecom_bot_state_changed"
ADAPTER_LOGGER = "muad_im_gateway.channels.wecom.adapter"


def _bot(bot_id: str, secret: str | None = B118_SECRET) -> BotSnapshotItem:
    return BotSnapshotItem(
        bot_account_id=uuid.uuid4(), bot_id=bot_id, secret=secret, agent_id=uuid.uuid4()
    )


async def _wait_for(predicate: Any, *, what: str, timeout: float = WAIT_TIMEOUT_SEC) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(what)


@pytest.fixture()
async def b118_probe() -> AsyncIterator[WeComProbe]:
    probe = WeComProbe(expected_bots={B118_GOOD_BOT: B118_SECRET})
    probe.reject_bot_ids.add(B118_BAD_BOT)  # 坏 bot：握手被拒（单 bot 故障隔离）
    await probe.start()
    try:
        yield probe
    finally:
        await probe.stop()


@pytest.fixture()
async def b118_adapter(
    b118_probe: WeComProbe, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[WeComAdapter]:
    monkeypatch.setenv("WECOM_WS_URL", b118_probe.ws_url)
    monkeypatch.setenv("WECOM_WS_CA_FILE", str(b118_probe.cert_path))
    adapter = WeComAdapter(
        bots=[_bot(B118_GOOD_BOT), _bot(B118_BAD_BOT)],
        backoff_base_sec=0.2,
        backoff_max_sec=0.5,
        liveness_interval_sec=0.2,
    )
    try:
        yield adapter
    finally:
        await adapter.stop()


@pytest.fixture()
async def b118_metrics_http() -> AsyncIterator[httpx.AsyncClient]:
    """真实 HTTP `/metrics` 端点（uvicorn 真实 socket；lifespan 关闭以保留进程内注册表）。"""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="off")
    server = uvicorn.Server(config)
    serving = asyncio.create_task(server.serve())
    try:
        deadline = time.monotonic() + 15.0
        while not server.started and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        assert server.started, "metrics 端点未在超时内监听"
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=10.0) as client:
            yield client
    finally:
        server.should_exit = True
        await asyncio.wait_for(serving, timeout=15.0)


def _gauge_value(text: str, bot_id: str) -> float | None:
    prefix = f'{GAUGE_NAME}{{bot_id="{bot_id}"}} '
    for line in text.splitlines():
        if line.startswith(prefix):
            return float(line[len(prefix) :])
    return None


async def _gauge_over_http(client: httpx.AsyncClient, bot_id: str) -> float | None:
    response = await client.get(METRICS_PATH)
    assert response.status_code == 200, response.text
    return _gauge_value(response.text, bot_id)


async def test_b118_metrics_track_connection_transitions_and_isolate_bad_bot(
    b118_probe: WeComProbe,
    b118_adapter: WeComAdapter,
    b118_metrics_http: httpx.AsyncClient,
) -> None:
    response = await b118_metrics_http.get(METRICS_PATH)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/plain")

    await b118_adapter.start()
    await _wait_for(
        lambda: b118_adapter.connection_states.get(B118_GOOD_BOT) is ConnectionState.CONNECTED,
        what="好 bot 未在真实 WS 上完成认证",
    )
    rendered = await b118_metrics_http.get(METRICS_PATH)
    assert f"# TYPE {GAUGE_NAME} gauge" in rendered.text
    # 连通=1；被拒的坏 bot=0，且不影响好 bot 的连接序列
    assert _gauge_value(rendered.text, B118_GOOD_BOT) == 1.0
    await _wait_for(
        lambda: b118_adapter.connection_states.get(B118_BAD_BOT) is ConnectionState.BACKOFF,
        what="坏 bot 未进入退避",
    )
    assert await _gauge_over_http(b118_metrics_http, B118_BAD_BOT) == 0.0
    assert b118_adapter.connection_states.get(B118_GOOD_BOT) is ConnectionState.CONNECTED

    # 真实断线 → 退避期归零 → 自动重连后回 1
    await b118_probe.drop_connection(B118_GOOD_BOT)
    await _wait_for(
        lambda: b118_adapter.connection_states.get(B118_GOOD_BOT) is ConnectionState.BACKOFF,
        what="断线未被观测为退避",
    )
    assert await _gauge_over_http(b118_metrics_http, B118_GOOD_BOT) == 0.0
    await _wait_for(
        lambda: b118_adapter.connection_states.get(B118_GOOD_BOT) is ConnectionState.CONNECTED,
        what="断线后未重连",
    )
    assert await _gauge_over_http(b118_metrics_http, B118_GOOD_BOT) == 1.0

    await b118_adapter.stop()
    assert await _gauge_over_http(b118_metrics_http, B118_GOOD_BOT) == 0.0, "停止后指标必须归零"


async def test_b118_transition_logs_carry_fields_without_secret(
    b118_adapter: WeComAdapter, caplog: pytest.LogCaptureFixture
) -> None:
    set_request_context(locale="zh-CN", trace_id="trace-b118", request_id="req-b118")
    with caplog.at_level(logging.INFO, logger=ADAPTER_LOGGER):
        await b118_adapter.start()
        await _wait_for(
            lambda: b118_adapter.connection_states.get(B118_GOOD_BOT) is ConnectionState.CONNECTED,
            what="好 bot 未在真实 WS 上完成认证",
        )
        await b118_adapter.stop()

    transitions = [
        record.getMessage() for record in caplog.records if LOG_PREFIX in record.getMessage()
    ]
    assert transitions, "缺少状态迁移日志"
    assert [line for line in transitions if "to=CONNECTED" in line], transitions
    for line in transitions:
        assert "bot_id=" in line and "from=" in line and "to=" in line and "attempt=" in line
    assert any("trace_id=trace-b118" in line for line in transitions), "迁移日志必须可关联 trace"

    # 任何日志都不得出现 bot secret（含 SDK 原始异常文本）
    for record in caplog.records:
        rendered = f"{record.getMessage()} {record.args}"
        assert B118_SECRET not in rendered, record.getMessage()
        assert SECRET_CANARY not in rendered, record.getMessage()


async def test_b118_sdk_error_text_with_secret_is_not_logged(
    b118_probe: WeComProbe, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """SDK 以任意异常回传失败：日志只记异常类型，不回显原始异常文本（可能含凭据）。"""
    monkeypatch.setenv("WECOM_WS_URL", b118_probe.ws_url)
    monkeypatch.setenv("WECOM_WS_CA_FILE", str(b118_probe.cert_path))

    def failing_factory(bot_id: str, secret: str) -> Any:
        raise WeComSdkConnectionError(f"handshake failed secret={SECRET_CANARY}")

    adapter = WeComAdapter(
        bots=[_bot("bot-b118-error")],
        sdk_factory=failing_factory,
        backoff_base_sec=0.2,
        backoff_max_sec=0.5,
    )
    with caplog.at_level(logging.DEBUG, logger=ADAPTER_LOGGER):
        await adapter.start()
        await asyncio.sleep(0.3)
        await adapter.stop()

    assert caplog.records, "未产生任何连接日志"
    for record in caplog.records:
        assert SECRET_CANARY not in f"{record.getMessage()} {record.args}"
