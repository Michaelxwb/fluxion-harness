"""B-306：容量验收真实协议探针的协议级验证（TASK-106；设计 §2.5 / §4.3）。

真实边界（不 mock 服务方法、不连 PG/Redis）：
- **模型探针**是真实 HTTP 服务器（真实 socket、真实 SSE 分帧）：线级断言用 `httpx`，
  解析与错误映射经**生产** `OpenAICompatibleProvider`（`muad_agent_core.model`），
  子进程模式由真实 `python -m` 进程 + 真实 HTTP 控制面驱动；
- **企微探针**是真实 WSS 服务器（真实 TLS），由**官方 SDK**（`wecom-aibot-python-sdk`）
  按真实协议驱动：认证帧 / 心跳 / 回调推送 / 会话内回复 / 主动发送；
- **故障代理**是真实 TCP 转发器：真实 HTTP 流量经代理进出，注入延迟 / 中断 / 拒绝连接。

关键断言（B-306 契约）：HTTP 模型可设置延迟/增量/usage/429（带 Retry-After）/5xx/断流/
应答丢失，企微走实际 SDK 协议探针；所有错误注入可观测。
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import ssl
import subprocess
import sys
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest
from muad_agent_core.model import (
    ModelMessage,
    ModelRateLimitedError,
    ModelRequest,
    ModelResponse,
    ModelRole,
    ModelUnavailableError,
    OpenAICompatibleProvider,
)

from tests.acceptance.capacity.probes import (
    FaultProxy,
    FaultProxyMode,
    ModelProbeBehavior,
    ModelProbeFault,
    ModelProbeServer,
    WeComProbeServer,
    key_fingerprint,
)

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
CHAT_PATH = "/v1/chat/completions"
KEY_A = "sk-capacity-probe-a"
KEY_B = "sk-capacity-probe-b"
WECOM_BOT = "bot-capacity-probe"
WECOM_SECRET = "capacity-probe-secret"
WECOM_USER = "capacity-probe-user"
WECOM_CHAT = "capacity-probe-chat"


# --------------------------------------------------------------------------------------
# 真实 HTTP 客户端与生产模型 Provider 的驱动助手
# --------------------------------------------------------------------------------------


def _payload(*, model: str = "probe-model", stream: bool = True) -> dict[str, Any]:
    return {
        "model": model,
        "stream": stream,
        "messages": [{"role": "user", "content": "ping"}],
    }


def _auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


async def _wire_stream(
    base_url: str, *, key: str = KEY_A, model: str = "probe-model"
) -> tuple[int, list[str], list[float]]:
    """线级 SSE：返回状态码、`data:` 行与每个正文增量的到达时刻（真实 socket）。

    `trust_env=False`：直连探针的真实 socket，绕开本机系统/环境代理（httpx 默认
    `trust_env=True` 会经代理访问，断流/应答丢失会被代理改写成 502，测的就不是探针了）。
    """
    lines: list[str] = []
    arrivals: list[float] = []
    async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
        async with client.stream(
            "POST", f"{base_url}{CHAT_PATH}", json=_payload(model=model), headers=_auth(key)
        ) as response:
            status = response.status_code
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                lines.append(line)
                if _is_delta_line(line):
                    arrivals.append(time.monotonic())
    return status, lines, arrivals


def _is_delta_line(line: str) -> bool:
    if line == "data: [DONE]":
        return False
    payload = json.loads(line.removeprefix("data:").strip())
    return any(choice["delta"].get("content") for choice in payload.get("choices", []))


async def _post_chat(
    base_url: str, *, key: str = KEY_A, stream: bool = False, model: str = "probe-model"
) -> httpx.Response:
    async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
        return await client.post(
            f"{base_url}{CHAT_PATH}",
            json=_payload(model=model, stream=stream),
            headers=_auth(key),
        )


def _provider(base_url: str, *, key: str = KEY_A) -> OpenAICompatibleProvider:
    """生产 Provider 直连探针：显式关掉环境/系统代理，测的是探针的真实 HTTP 行为。"""
    return OpenAICompatibleProvider(
        base_url=f"{base_url}/v1",
        model="probe-model",
        api_key=key,
        transport=httpx.AsyncHTTPTransport(trust_env=False),
    )


def _model_request() -> ModelRequest:
    return ModelRequest(
        model_id="probe-model",
        messages=(ModelMessage(role=ModelRole.USER, content="ping"),),
    )


async def _stream(provider: OpenAICompatibleProvider) -> ModelResponse:
    deltas: list[str] = []

    async def on_delta(text: str) -> None:
        deltas.append(text)

    return await provider.stream(_model_request(), on_delta)


def _deltas_of(lines: list[str]) -> list[str]:
    payloads = [
        json.loads(line.removeprefix("data:").strip())
        for line in lines
        if line != "data: [DONE]"
    ]
    return [
        choice["delta"]["content"]
        for payload in payloads
        for choice in payload["choices"]
        if choice["delta"].get("content")
    ]


def _payloads_of(lines: list[str]) -> list[dict[str, Any]]:
    return [
        json.loads(line.removeprefix("data:").strip())
        for line in lines
        if line != "data: [DONE]"
    ]


# --------------------------------------------------------------------------------------
# 模型 HTTP 探针：延迟 / 增量 / usage / 429 / 5xx / 断流 / 应答丢失 / Key 计数
# --------------------------------------------------------------------------------------


async def test_b306_model_probe_streams_deltas_usage_and_records_real_calls() -> None:
    behavior = ModelProbeBehavior(
        deltas=("Hel", "lo ", "world"), usage_prompt_tokens=11, usage_completion_tokens=7
    )
    with ModelProbeServer(behavior=behavior) as probe:
        status, lines, _ = await _wire_stream(probe.url, model="probe-model")

        assert status == 200
        assert _deltas_of(lines) == ["Hel", "lo ", "world"], "增量按顺序逐块下发"
        assert lines[-1] == "data: [DONE]", "正常收尾必须有 [DONE]"
        final = _payloads_of(lines)[-1]
        assert final["choices"][0]["finish_reason"] == "stop"
        assert final["usage"] == {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}
        assert {payload["model"] for payload in _payloads_of(lines)} == {"probe-model"}

        assert len(probe.calls) == 1
        call = probe.calls[0]
        assert call.api_key == KEY_A and call.stream is True and call.fault is None
        assert call.body["model"] == "probe-model"
        assert call.path == CHAT_PATH
        assert probe.key_calls[KEY_A] == 1
        assert probe.max_concurrency == 1
        assert probe.faults == {}, "未注入故障时故障计数为空"


async def test_b306_model_probe_emits_a_tool_call_only_before_any_tool_result() -> None:
    """工具回合注入：有工具结果前的调用返回 tool_calls，续跑（已带 tool 消息）返回正文。"""
    behavior = ModelProbeBehavior(
        deltas=("done",),
        tool_call={"name": "execute_skill", "arguments": {"skill_key": "s", "input": {}}},
    )

    async def stream(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        lines: list[str] = []
        async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
            async with client.stream(
                "POST",
                f"{probe.url}{CHAT_PATH}",
                json={"model": "probe-model", "messages": messages, "stream": True},
                headers=_auth(KEY_A),
            ) as response:
                assert response.status_code == 200
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        lines.append(line)
        return _payloads_of(lines)

    with ModelProbeServer(behavior=behavior) as probe:
        first = await stream([{"role": "user", "content": "go"}])
        tool_calls = [
            call
            for payload in first
            for choice in payload["choices"]
            for call in choice["delta"].get("tool_calls") or []
        ]
        assert [call["function"]["name"] for call in tool_calls] == ["execute_skill"]
        assert first[-1]["choices"][0]["finish_reason"] == "tool_calls"

        second = await stream(
            [
                {"role": "user", "content": "go"},
                {"role": "tool", "tool_call_id": "call_capacity_probe", "content": "{}"},
            ]
        )
        assert _deltas_of(
            [f"data: {json.dumps(payload, ensure_ascii=False)}" for payload in second]
        ) == ["done"]
        assert second[-1]["choices"][0]["finish_reason"] == "stop"


async def test_b306_model_probe_latency_and_chunk_pacing_are_real() -> None:
    behavior = ModelProbeBehavior(deltas=("a", "b", "c"), latency_ms=250, chunk_interval_ms=120)
    with ModelProbeServer(behavior=behavior) as probe:
        started = time.monotonic()
        status, lines, arrivals = await _wire_stream(probe.url)
        elapsed = time.monotonic() - started

        assert status == 200
        assert elapsed >= 0.25, "首字延迟真实生效（先 sleep 再写首块）"
        assert _deltas_of(lines) == ["a", "b", "c"]
        assert len(arrivals) == 3
        assert arrivals[-1] - arrivals[0] >= 0.2, "增量按 chunk_interval_ms 真实分块（2×120ms）"
        assert probe.behavior_for(KEY_A) == behavior


async def test_b306_model_probe_rate_limit_and_server_error_are_observable() -> None:
    with ModelProbeServer() as probe:
        probe.set_behavior(ModelProbeBehavior(status_code=429, retry_after_sec=7))
        response = await _post_chat(probe.url)
        assert response.status_code == 429
        assert response.headers["retry-after"] == "7", "429 必须带 Retry-After（真实恢复预算）"
        assert probe.faults[ModelProbeFault.RATE_LIMITED] == 1

        rate_limited = _provider(probe.url)
        try:
            with pytest.raises(ModelRateLimitedError) as error:
                await _stream(rate_limited)
        finally:
            await rate_limited.aclose()
        assert error.value.retry_after == 7.0, "生产恢复策略据 Retry-After 退避"

        probe.set_behavior(ModelProbeBehavior(status_code=503))
        response = await _post_chat(probe.url)
        assert response.status_code == 503
        assert probe.faults[ModelProbeFault.SERVER_ERROR] == 1

        unavailable = _provider(probe.url)
        try:
            with pytest.raises(ModelUnavailableError):
                await _stream(unavailable)
        finally:
            await unavailable.aclose()

        assert [call.fault for call in probe.calls] == [
            ModelProbeFault.RATE_LIMITED,
            ModelProbeFault.RATE_LIMITED,
            ModelProbeFault.SERVER_ERROR,
            ModelProbeFault.SERVER_ERROR,
        ], "每次注入逐调用可观测"


async def test_b306_model_probe_stream_cutoff_is_detected_as_unfinished() -> None:
    behavior = ModelProbeBehavior(deltas=("x", "y", "z"), cutoff_after_deltas=2)
    with ModelProbeServer(behavior=behavior) as probe:
        status, lines, _ = await _wire_stream(probe.url)

        assert status == 200
        assert _deltas_of(lines) == ["x", "y"], "断流后不再下发第 3 块"
        assert "data: [DONE]" not in lines, "断流没有收尾信号"
        assert probe.faults[ModelProbeFault.STREAM_CUTOFF] == 1

        provider = _provider(probe.url)
        try:
            response = await _stream(provider)
        finally:
            await provider.aclose()
        assert response.content == "xy"
        assert response.finish_reason == "", "无 finish_reason/[DONE] ⇒ 生产解析为未正常收尾"


async def test_b306_model_probe_response_loss_keeps_the_call_provable() -> None:
    with ModelProbeServer(behavior=ModelProbeBehavior(drop_response=True)) as probe:
        with pytest.raises(httpx.RemoteProtocolError):
            await _post_chat(probe.url)

        assert len(probe.calls) == 1, "请求已到达探针：应答丢失 ≠ 请求未发出"
        assert probe.calls[0].fault is ModelProbeFault.RESPONSE_LOST

        provider = _provider(probe.url)
        try:
            with pytest.raises(ModelUnavailableError):
                await _stream(provider)
        finally:
            await provider.aclose()
        assert probe.faults[ModelProbeFault.RESPONSE_LOST] == 2


async def test_b306_model_probe_counts_per_key_and_enforces_quota() -> None:
    with ModelProbeServer(key_limits={KEY_A: 2}) as probe:
        assert (await _post_chat(probe.url, key=KEY_A)).status_code == 200
        assert (await _post_chat(probe.url, key=KEY_A)).status_code == 200

        limited = await _post_chat(probe.url, key=KEY_A)
        assert limited.status_code == 429
        assert limited.headers["retry-after"] == "1"
        assert (await _post_chat(probe.url, key=KEY_B)).status_code == 200, "别的 Key 不受影响"

        assert probe.key_calls[KEY_A] == 3 and probe.key_calls[KEY_B] == 1
        assert probe.faults[ModelProbeFault.KEY_QUOTA_EXCEEDED] == 1

        state = probe.state()
        assert state["key_calls"] == {key_fingerprint(KEY_A): 3, key_fingerprint(KEY_B): 1}
        assert KEY_A not in json.dumps(state), "原始 Key 不进控制面/观测快照"


def _drive_subprocess_probe(url: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """经真实 HTTP 控制面驱动子进程探针：返回注入后的观测快照与 reset 后的快照。"""
    with httpx.Client(timeout=10.0, trust_env=False) as client:
        assert client.get(f"{url}/healthz").json() == {"status": "ok"}
        updated = client.post(
            f"{url}/__probe__/behavior",
            json={"api_key": None, "behavior": {"status_code": 503}},
        )
        assert updated.status_code == 200
        assert updated.json()["behavior"]["status_code"] == 503
        assert client.get(f"{url}/v1/models").json()["data"][0]["id"] == "gpt-4o-mini"
        bad = client.post(
            f"{url}/__probe__/behavior", json={"api_key": None, "behavior": {"nope": 1}}
        )
        assert bad.status_code == 400 and "unknown behavior field" in bad.json()["error"]
        limits = client.post(f"{url}/__probe__/key-limits", json={"key_limits": {KEY_A: 1}})
        assert limits.status_code == 200
        assert limits.json()["key_limits"] == {key_fingerprint(KEY_A): 1}
        injected = client.post(
            f"{url}{CHAT_PATH}", json=_payload(stream=False), headers=_auth(KEY_A)
        )
        assert injected.status_code == 503
        state = client.get(f"{url}/__probe__/state").json()

    with httpx.Client(timeout=10.0, trust_env=False) as client:
        assert client.post(f"{url}/__probe__/reset").json() == {"reset": True}
        cleared = client.get(f"{url}/__probe__/state").json()
    return state, cleared


def test_b306_model_probe_runs_as_subprocess_with_control_surface() -> None:
    process = subprocess.Popen(
        [sys.executable, "-m", "tests.acceptance.capacity.probes", "--host", "127.0.0.1", "--port", "0"],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        url = json.loads(process.stdout.readline())["url"]
        state, cleared = _drive_subprocess_probe(url)

        assert state["faults"] == {"SERVER_ERROR": 1}
        assert state["call_count"] == 1
        assert state["key_calls"] == {key_fingerprint(KEY_A): 1}
        assert state["key_limits"] == {key_fingerprint(KEY_A): 1}
        assert KEY_A not in json.dumps(state), "原始 Key 不进控制面/观测快照"

        assert cleared["call_count"] == 0 and cleared["faults"] == {}
        assert cleared["key_calls"] == {} and cleared["key_limits"] == {}
    finally:
        process.terminate()
        process.wait(timeout=10.0)


def test_b306_probe_programming_rejects_invalid_inputs_explicitly() -> None:
    with pytest.raises(ValueError, match="status_code"):
        ModelProbeBehavior(status_code="500")
    with pytest.raises(ValueError, match="mutually exclusive"):
        ModelProbeBehavior(status_code=200, drop_response=True, cutoff_after_deltas=1)
    with pytest.raises(ValueError, match="drop_response requires"):
        ModelProbeBehavior(status_code=503, drop_response=True)
    with pytest.raises(ValueError, match="cutoff_after_deltas"):
        ModelProbeBehavior(deltas=("a",), cutoff_after_deltas=9)

    with ModelProbeServer() as probe:
        with pytest.raises(TypeError):
            probe.set_behavior("not-a-behavior")  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="positive int"):
            probe.set_key_limit(KEY_A, 0)
        proxy = FaultProxy("127.0.0.1", 1)
        with pytest.raises(ValueError, match="DELAY requires"):
            proxy.set_fault(FaultProxyMode.DELAY)
        with pytest.raises(ValueError, match="non-negative"):
            proxy.set_fault(FaultProxyMode.DELAY, delay_ms=-1)
        with pytest.raises(TypeError):
            proxy.set_fault("DROP")  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------
# 企微协议探针：官方 SDK 经真实 TLS/WSS 认证、收发与故障注入
# --------------------------------------------------------------------------------------


async def _wait_until(predicate: Callable[[], bool], *, timeout: float = 5.0, message: str = "") -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError(message or f"条件未在 {timeout}s 内满足")


@pytest.fixture()
def sdk_factory(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., Any]]:
    """官方 SDK 客户端工厂：只把 ws_url 指向本地探针并信任其自签证书。"""
    import aibot.ws as sdk_ws  # type: ignore[import-untyped]

    monkeypatch.setattr(sdk_ws, "_SSL_CONTEXT", ssl._create_unverified_context())
    clients: list[Any] = []

    def make(
        *, ws_url: str, bot_id: str, secret: str, ack_timeout_sec: float = 0.4
    ) -> Any:
        from aibot import WSClient, WSClientOptions  # type: ignore[import-untyped]

        client = WSClient(
            WSClientOptions(
                bot_id=bot_id,
                secret=secret,
                ws_url=ws_url,
                max_reconnect_attempts=0,
                heartbeat_interval=200,
                request_timeout=2000,
                reconnect_interval=50,
            )
        )
        # 只压缩「等 ACK 超时」的墙钟窗口；超时判定仍由 SDK 自己的实现完成。
        client._ws_manager._reply_ack_timeout = ack_timeout_sec
        clients.append(client)
        return client

    try:
        yield make
    finally:
        for client in clients:
            client.disconnect()
        time.sleep(0.05)


async def _authenticate(client: Any) -> None:
    authenticated = asyncio.Event()
    client.on("authenticated", authenticated.set)
    await client.connect()
    await asyncio.wait_for(authenticated.wait(), timeout=10.0)


async def _push_text(
    probe: WeComProbeServer, client: Any, *, message_id: str, text: str
) -> dict[str, Any]:
    frames: list[dict[str, Any]] = []
    client.on("message.text", frames.append)
    await probe.push_message(
        bot_id=WECOM_BOT,
        message_id=message_id,
        external_user_id=WECOM_USER,
        text=text,
        reply_id=f"req-{message_id}",
    )
    await _wait_until(lambda: bool(frames))
    return frames[0]


async def test_b306_wecom_probe_authenticates_and_exchanges_frames_with_real_sdk(
    sdk_factory: Callable[..., Any],
) -> None:
    async with WeComProbeServer(expected_bots={WECOM_BOT: WECOM_SECRET}) as probe:
        client = sdk_factory(ws_url=probe.ws_url, bot_id=WECOM_BOT, secret=WECOM_SECRET)
        await _authenticate(client)

        subscribe = probe.frames_of("aibot_subscribe")
        assert len(subscribe) == 1
        assert subscribe[0]["body"] == {"bot_id": WECOM_BOT, "secret": WECOM_SECRET}
        assert probe.auth_failures == []
        await _wait_until(
            lambda: bool(probe.frames_of("ping")), message="心跳经真实 socket 到达探针"
        )

        frame = await _push_text(probe, client, message_id="msg-1", text="容量验收")
        assert frame["body"]["text"]["content"] == "容量验收"

        ack = await client.reply_stream(frame, stream_id="stream-1", content="pong", finish=True)
        assert ack["errcode"] == 0
        reply = probe.frames_of("aibot_respond_msg")[-1]
        assert reply["headers"]["req_id"] == "req-msg-1"
        assert reply["body"]["stream"] == {"id": "stream-1", "finish": True, "content": "pong"}

        sent = await client.send_message(
            WECOM_CHAT, {"msgtype": "markdown", "markdown": {"content": "active"}}
        )
        assert sent["errcode"] == 0
        assert probe.frames_of("aibot_send_msg")[-1]["body"]["markdown"]["content"] == "active"

        stats = probe.stats()
        assert stats["frames"]["aibot_respond_msg"] == 1
        assert stats["frames"]["aibot_send_msg"] == 1
        assert stats["ack_losses"] == 0 and stats["throttled"] == 0
        acked = Counter(ack.cmd for ack in probe.acks)
        assert acked["aibot_subscribe"] == 1, "认证回执可观测"
        assert acked["aibot_respond_msg"] == 1 and acked["aibot_send_msg"] == 1
        assert all(ack.errcode == 0 for ack in probe.acks)


async def test_b306_wecom_probe_injects_auth_rejection_and_connection_drop(
    sdk_factory: Callable[..., Any],
) -> None:
    async with WeComProbeServer(
        expected_bots={WECOM_BOT: WECOM_SECRET}, reject_bot_ids={WECOM_BOT}
    ) as rejecting:
        client = sdk_factory(ws_url=rejecting.ws_url, bot_id=WECOM_BOT, secret=WECOM_SECRET)
        errors: list[Exception] = []
        client.on("error", errors.append)
        await client.connect()
        await _wait_until(lambda: bool(errors))
        assert "Authentication failed" in str(errors[0])
        assert len(rejecting.auth_failures) == 1
        assert rejecting.stats()["connections"] == 1

    async with WeComProbeServer(expected_bots={WECOM_BOT: WECOM_SECRET}) as dropping:
        client = sdk_factory(ws_url=dropping.ws_url, bot_id=WECOM_BOT, secret=WECOM_SECRET)
        await _authenticate(client)
        assert dropping.connection_bots == {0: WECOM_BOT}

        await dropping.drop_connection(WECOM_BOT)
        # SDK 不保证服务端断线回调（生产 Adapter 亦以存活探测兜底）：这里断言 SDK 侧
        # 连接**不再可用**这一真实可观测事实，而不是某个回调恰好被触发。
        await _wait_until(
            lambda: client.is_connected is False, message="服务端断线后 SDK 连接不再可用"
        )
        assert dropping.stats()["connections"] == 1


async def test_b306_wecom_probe_injects_send_failure_throttle_and_ack_loss(
    sdk_factory: Callable[..., Any],
) -> None:
    async with WeComProbeServer(
        expected_bots={WECOM_BOT: WECOM_SECRET}, fail_reply_bots={WECOM_BOT}
    ) as probe:
        client = sdk_factory(ws_url=probe.ws_url, bot_id=WECOM_BOT, secret=WECOM_SECRET)
        await _authenticate(client)
        frame = await _push_text(probe, client, message_id="msg-fail", text="fail")

        with pytest.raises(RuntimeError) as failed:
            await client.reply_stream(frame, stream_id="s-fail", content="x", finish=True)
        assert "50001" in str(failed.value), "发送失败回执（真实 errcode）可观测"
        assert len(probe.frames_of("aibot_respond_msg")) == 1, "失败 ≠ 未发出"

        probe.fail_reply_bots.discard(WECOM_BOT)
        probe.throttle_bots.add(WECOM_BOT)
        with pytest.raises(RuntimeError) as throttled:
            await client.send_message(
                WECOM_CHAT, {"msgtype": "markdown", "markdown": {"content": "t"}}
            )
        assert "45009" in str(throttled.value), "限流回执（真实 errcode）可观测"
        assert probe.throttled == 1

        probe.throttle_bots.discard(WECOM_BOT)
        probe.ack_loss_bots.add(WECOM_BOT)
        with pytest.raises(TimeoutError):
            await client.send_message(
                WECOM_CHAT, {"msgtype": "markdown", "markdown": {"content": "lost"}}
            )
        assert probe.ack_losses == 1, "ACK 丢失：帧已到达但无回执"
        frames = probe.stats()["frames"]
        assert frames["aibot_respond_msg"] == 1 and frames["aibot_send_msg"] == 2
        acked = [
            ack.errcode
            for ack in probe.acks
            if ack.cmd in ("aibot_respond_msg", "aibot_send_msg")
        ]
        assert acked == [50001, 45009], "ACK 丢失不产生回执记录"


# --------------------------------------------------------------------------------------
# 故障协议探针：真实 TCP 转发 + 延迟/中断/拒绝连接注入
# --------------------------------------------------------------------------------------


def _health_upstream() -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args: object) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802
            body = json.dumps({"status": "ok"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _get(endpoint: str) -> httpx.Response:
    """每次都用新连接请求：故障代理的延迟/丢弃发生在**连接**粒度（真实 TCP 语义）。"""
    with httpx.Client(timeout=5.0, trust_env=False) as client:
        return client.get(endpoint)


def _get_after_recovery(endpoint: str, *, timeout_sec: float = 5.0) -> httpx.Response:
    """恢复直通后重试到首个 200：监听 socket 从 REFUSE 重新 bind 是异步生效的。"""
    deadline = time.monotonic() + timeout_sec
    last_error: httpx.HTTPError | None = None
    while time.monotonic() < deadline:
        try:
            response = _get(endpoint)
            if response.status_code == 200:
                return response
        except httpx.HTTPError as exc:
            last_error = exc
        time.sleep(0.02)
    raise AssertionError(f"fault proxy 在 {timeout_sec}s 内未恢复直通：{last_error!r}") from last_error


def _expect_connection_refused(endpoint: str, *, timeout_sec: float = 2.0) -> None:
    """REFUSE 是「监听已关闭」：关闭前已进入 backlog 的连接可能先回 RST；重试到真正的 ECONNREFUSED。"""
    deadline = time.monotonic() + timeout_sec
    last_error: httpx.HTTPError | None = None
    while time.monotonic() < deadline:
        try:
            _get(endpoint)
        except httpx.ConnectError as exc:
            if "refused" in str(exc).lower():
                return
            last_error = exc
        except httpx.HTTPError as exc:
            last_error = exc
        time.sleep(0.02)
    raise AssertionError(f"fault proxy 未在 {timeout_sec}s 内进入拒绝连接状态：{last_error!r}") from last_error


def test_b306_fault_proxy_forwards_real_traffic_and_injects_faults() -> None:
    upstream = _health_upstream()
    proxy = FaultProxy("127.0.0.1", int(upstream.server_address[1]))
    proxy.start()
    try:
        endpoint = f"http://{proxy.address[0]}:{proxy.address[1]}/healthz"
        assert _get_after_recovery(endpoint).json() == {"status": "ok"}, "直通真实上游"

        proxy.set_fault(FaultProxyMode.DELAY, delay_ms=250)
        started = time.monotonic()
        assert _get(endpoint).status_code == 200
        assert time.monotonic() - started >= 0.25, "延迟注入真实生效（新连接）"

        with httpx.Client(timeout=5.0, trust_env=False) as client:
            assert client.get(endpoint).status_code == 200
            proxy.set_fault(FaultProxyMode.DROP)
            with pytest.raises(httpx.HTTPError):
                client.get(endpoint)  # 在飞连接被切断
        with pytest.raises(httpx.HTTPError):
            _get(endpoint)

        proxy.set_fault(FaultProxyMode.REFUSE)
        _expect_connection_refused(endpoint)
        assert proxy.listening is False

        proxy.set_fault(FaultProxyMode.PASS_THROUGH)
        assert _get_after_recovery(endpoint).status_code == 200, "恢复后可继续转发"

        assert proxy.connections >= 4
        assert proxy.dropped_connections >= 2
        assert proxy.forwarded_bytes > 0
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and proxy.active_connections:
            time.sleep(0.02)
        assert proxy.active_connections == 0, "请求结束后在飞连接归还（回收有界）"
    finally:
        proxy.stop()
        upstream.shutdown()
        upstream.server_close()


def test_b306_fault_proxy_reports_unreachable_upstream() -> None:
    proxy = FaultProxy("127.0.0.1", 1)  # 127.0.0.1:1 未监听
    proxy.start()
    try:
        with pytest.raises(httpx.HTTPError):
            _get(f"http://{proxy.address[0]}:{proxy.address[1]}/healthz")
        assert proxy.upstream_failures == 1, "上游不可达显式计数，不静默吞掉"
        assert proxy.forwarded_bytes == 0
    finally:
        proxy.stop()


def test_b306_capacity_probe_fixtures_are_exported_from_conftest() -> None:
    module = importlib.import_module("tests.acceptance.capacity.conftest")
    for name in ("model_probe", "wecom_probe", "fault_proxy_factory"):
        fixture = getattr(module, name)
        marker = getattr(fixture, "_fixture_function_marker", None)
        assert marker is not None and fixture.name == name, f"{name} 必须是 pytest fixture"

    with ModelProbeServer() as probe:
        assert probe.listening is True and probe.url.startswith("http://127.0.0.1:")
    assert probe.listening is False, "上下文退出后真实停止监听"
