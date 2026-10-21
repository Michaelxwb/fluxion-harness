"""asyncio 版模型探针的协议级验证（容量测量面）。

为什么单独验：它替换的是容量压测中的模型侧测量面，必须与同步探针同协议（SSE 增量、
usage、tool_call、429/Retry-After、断流）同控制面（behavior/key-limits/reset/state/calls），
否则压测结果不可与被测系统的延迟分布直接比对。
"""

from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from tests.acceptance.capacity.asgi_probe import AsyncModelProbe, create_app
from tests.acceptance.capacity.probes import ModelProbeBehavior

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
CHAT_PATH = "/v1/chat/completions"
KEY_A = "sk-capacity-probe-a"


def _client(app: object) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),  # type: ignore[arg-type]
        base_url="http://probe",
        timeout=10.0,
    )


def _sse_payloads(text: str) -> list[object]:
    payloads: list[object] = []
    for line in text.splitlines():
        if not line.startswith("data: "):
            continue
        raw = line[len("data: ") :]
        payloads.append(raw if raw == "[DONE]" else json.loads(raw))
    return payloads


def _chat_body(stream: bool = True) -> dict[str, object]:
    return {
        "model": "gpt-4o-mini",
        "stream": stream,
        "messages": [{"role": "user", "content": "hello"}],
    }


async def test_streams_deltas_usage_and_done() -> None:
    app = create_app()
    async with _client(app) as client:
        response = await client.post(
            CHAT_PATH, json=_chat_body(), headers={"Authorization": f"Bearer {KEY_A}"}
        )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    payloads = _sse_payloads(response.text)
    assert payloads[-1] == "[DONE]"
    deltas = [
        chunk["choices"][0]["delta"]["content"]
        for chunk in payloads[:-1]
        if isinstance(chunk, dict) and chunk["choices"][0]["delta"].get("content")
    ]
    assert deltas == ["pong"]
    final = payloads[-2]
    assert final["choices"][0]["finish_reason"] == "stop"
    assert final["usage"] == {
        "prompt_tokens": 5,
        "completion_tokens": 3,
        "total_tokens": 8,
    }


async def test_tool_call_only_on_first_model_call() -> None:
    app = create_app()
    probe: AsyncModelProbe = app.probe  # type: ignore[attr-defined]
    probe.set_behavior(
        ModelProbeBehavior(
            tool_call={"name": "execute_skill", "arguments": {"skill_key": "s-1"}}
        )
    )
    async with _client(app) as client:
        first = await client.post(CHAT_PATH, json=_chat_body())
        second = await client.post(
            CHAT_PATH,
            json={
                "model": "gpt-4o-mini",
                "stream": True,
                "messages": [
                    {"role": "user", "content": "hello"},
                    {"role": "assistant", "content": None, "tool_calls": []},
                    {"role": "tool", "tool_call_id": "call_capacity_probe", "content": "ok"},
                ],
            },
        )
    first_payloads = _sse_payloads(first.text)
    assert isinstance(first_payloads[0], dict)
    tool_calls = first_payloads[0]["choices"][0]["delta"]["tool_calls"]
    assert tool_calls[0]["function"] == {
        "name": "execute_skill",
        "arguments": '{"skill_key": "s-1"}',
    }
    assert first_payloads[-2]["choices"][0]["finish_reason"] == "tool_calls"
    second_payloads = _sse_payloads(second.text)
    assert second_payloads[0]["choices"][0]["delta"]["content"] == "pong"


async def test_control_plane_sets_behavior_and_reset_restores_baseline() -> None:
    app = create_app()
    async with _client(app) as client:
        response = await client.post(
            "/__probe__/behavior",
            json={"behavior": {"latency_ms": 5, "deltas": ["a", "b"], "chunk_interval_ms": 1}},
        )
        assert response.status_code == 200
        stream = await client.post(CHAT_PATH, json=_chat_body())
        state = (await client.get("/__probe__/state")).json()
        assert state["call_count"] == 1
        assert state["calls"][0]["stream"] is True
        assert state["behaviors"]["default"]["deltas"] == ["a", "b"]
        await client.post("/__probe__/reset")
        state = (await client.get("/__probe__/state")).json()
        assert state["call_count"] == 0
        assert state["behaviors"]["default"]["deltas"] == ["pong"]
        again = await client.post(CHAT_PATH, json=_chat_body())
    assert response.status_code == 200
    assert stream.status_code == 200
    assert _sse_payloads(again.text)[0]["choices"][0]["delta"]["content"] == "pong"


async def test_injected_429_carries_retry_after_and_is_counted() -> None:
    app = create_app()
    probe: AsyncModelProbe = app.probe  # type: ignore[attr-defined]
    probe.set_behavior(
        ModelProbeBehavior(status_code=429, retry_after_sec=2)
    )
    async with _client(app) as client:
        response = await client.post(CHAT_PATH, json=_chat_body())
        state = (await client.get("/__probe__/state")).json()
    assert response.status_code == 429
    assert response.headers["retry-after"] == "2"
    assert state["faults"] == {"RATE_LIMITED": 1}


async def test_cutoff_stream_ends_without_done() -> None:
    app = create_app()
    probe: AsyncModelProbe = app.probe  # type: ignore[attr-defined]
    probe.set_behavior(
        ModelProbeBehavior(deltas=("a", "b"), cutoff_after_deltas=1)
    )
    async with _client(app) as client:
        response = await client.post(CHAT_PATH, json=_chat_body())
    payloads = _sse_payloads(response.text)
    assert [chunk["choices"][0]["delta"]["content"] for chunk in payloads] == ["a"]
    assert "[DONE]" not in payloads


async def test_key_limit_exceeded_returns_429_with_fingerprint_counting() -> None:
    app = create_app()
    async with _client(app) as client:
        await client.post("/__probe__/key-limits", json={"key_limits": {KEY_A: 1}})
        first = await client.post(
            CHAT_PATH, json=_chat_body(), headers={"Authorization": f"Bearer {KEY_A}"}
        )
        second = await client.post(
            CHAT_PATH, json=_chat_body(), headers={"Authorization": f"Bearer {KEY_A}"}
        )
        state = (await client.get("/__probe__/state")).json()
    assert first.status_code == 200
    assert second.status_code == 429
    assert state["faults"] == {"KEY_QUOTA_EXCEEDED": 1}
    assert list(state["key_calls"].values()) == [2]


async def test_non_stream_completion_shape() -> None:
    app = create_app()
    async with _client(app) as client:
        response = await client.post(CHAT_PATH, json=_chat_body(stream=False))
    payload = response.json()
    assert payload["object"] == "chat.completion"
    assert payload["choices"][0]["message"] == {"role": "assistant", "content": "pong"}


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def test_subprocess_mode_serves_control_plane() -> None:
    port = _free_port()
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "tests.acceptance.capacity.asgi_probe",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        deadline = time.monotonic() + 15.0
        while True:
            try:
                with httpx.Client(timeout=1.0, trust_env=False) as client:
                    health = client.get(f"http://127.0.0.1:{port}/healthz")
                if health.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() > deadline:
                raise AssertionError("asgi probe did not start")
            time.sleep(0.05)
        with httpx.Client(timeout=5.0, trust_env=False) as client:
            behavior = client.post(
                f"http://127.0.0.1:{port}/__probe__/behavior",
                json={"behavior": {"deltas": ["x"]}},
            )
            state = client.get(f"http://127.0.0.1:{port}/__probe__/state")
        assert behavior.status_code == 200
        assert state.json()["behaviors"]["default"]["deltas"] == ["x"]
    finally:
        process.terminate()
        try:
            process.wait(timeout=10.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5.0)


def test_asyncio_probe_tracks_max_concurrency() -> None:
    """并发调用要能观测到真实重叠（容量结论里的探针侧并发上界）。"""

    async def scenario() -> int:
        app = create_app()
        probe: AsyncModelProbe = app.probe  # type: ignore[attr-defined]
        probe.set_behavior(
            ModelProbeBehavior(latency_ms=50)
        )
        async with _client(app) as client:
            await asyncio.gather(*(client.post(CHAT_PATH, json=_chat_body()) for _ in range(8)))
        return probe.max_concurrency

    assert asyncio.run(scenario()) >= 4
