"""asyncio 版模型探针：与 `ModelProbeServer` 同协议、同控制面的单事件循环实现。

为什么要有第二份实现：`ModelProbeServer` 是 thread-per-connection，千级并发下探针自己的
线程调度与 GIL 抖动会混进被测系统的延迟分布（2026-10-20 实测 600 用户时首帧 P50 被探针
拖到 61s）。容量负载需要**测量面比被测面更宽**，因此这里用 ASGI（uvicorn）在单事件循环
里等待 `latency_ms`，协议行为（SSE 增量、usage、tool_call、429/5xx、断流）与控制面
（`/__probe__/behavior|key-limits|reset|state|calls`）与同步实现保持一致。

差异（仅影响故障注入验收，不影响容量口径）：`drop_response`（零响应丢失）在 ASGI 下无法
在应用层实现，会由 uvicorn 回 500；容量负载用 `cutoff_after_deltas`（无终止块、无 `[DONE]`
的断流）即可。同步实现仍是故障注入验收的权威实现。

子进程模式：`uv run python -m tests.acceptance.capacity.asgi_probe --port 19090`。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, replace
from typing import Any

from .probes import (
    CHAT_COMPLETIONS_PATH,
    CONTROL_BEHAVIOR_PATH,
    CONTROL_CALLS_PATH,
    CONTROL_KEY_LIMITS_PATH,
    CONTROL_RESET_PATH,
    CONTROL_STATE_PATH,
    DEFAULT_MODEL,
    HEALTH_PATH,
    KEY_QUOTA_RETRY_AFTER_SEC,
    MODELS_PATH,
    ModelProbeBehavior,
    ModelProbeCall,
    ModelProbeFault,
    _bearer,
    _behavior_from_payload,
    _classify,
    _completion_payload,
    _delta_payload,
    _final_payload,
    _should_emit_tool_call,
    _tool_call_payload,
    key_fingerprint,
)

__all__ = ["AsyncModelProbe", "create_app", "main"]

Scope = dict[str, Any]
Message = dict[str, Any]
Send = Callable[[Message], Awaitable[None]]
Receive = Callable[[], Awaitable[Message]]


class AsyncModelProbe:
    """控制面状态与观测（单事件循环，无锁）；语义与 `ModelProbeServer` 一致。"""

    def __init__(
        self,
        *,
        behavior: ModelProbeBehavior | None = None,
        key_limits: Mapping[str, int] | None = None,
    ) -> None:
        self._initial_behavior = behavior or ModelProbeBehavior()
        self._default_behavior = self._initial_behavior
        self._key_behaviors: dict[str, ModelProbeBehavior] = {}
        self._key_limits: dict[str, int] = {}
        self._calls: list[ModelProbeCall] = []
        self._faults: Counter[ModelProbeFault] = Counter()
        self._key_calls: Counter[str] = Counter()
        self._inflight = 0
        self._max_concurrency = 0
        self._broken_pipes = 0
        for api_key, limit in (key_limits or {}).items():
            self.set_key_limit(api_key, limit)

    # ---- 可编程行为与观测 -------------------------------------------------

    def set_behavior(self, behavior: ModelProbeBehavior, *, api_key: str | None = None) -> None:
        if not isinstance(behavior, ModelProbeBehavior):
            raise TypeError("behavior must be a ModelProbeBehavior")
        if api_key is None:
            self._default_behavior = behavior
        else:
            self._key_behaviors[api_key] = behavior

    def behavior_for(self, api_key: str | None) -> ModelProbeBehavior:
        return self._key_behaviors.get(api_key or "", self._default_behavior)

    def set_key_limit(self, api_key: str, limit: int) -> None:
        if not api_key:
            raise ValueError("api_key must not be empty")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("key limit must be a positive int")
        self._key_limits[api_key] = limit

    def reset(self) -> None:
        """幂等**全量**复位：计数、故障、Key 限额/行为，以及默认行为（回到构造基准）。"""
        self._calls.clear()
        self._faults.clear()
        self._key_calls.clear()
        self._key_limits.clear()
        self._key_behaviors.clear()
        self._default_behavior = self._initial_behavior
        self._max_concurrency = 0
        self._broken_pipes = 0

    @property
    def calls(self) -> tuple[ModelProbeCall, ...]:
        return tuple(self._calls)

    @property
    def faults(self) -> Mapping[ModelProbeFault, int]:
        return dict(self._faults)

    @property
    def max_concurrency(self) -> int:
        return self._max_concurrency

    @property
    def broken_pipes(self) -> int:
        return self._broken_pipes

    def state(self) -> dict[str, Any]:
        return {
            "call_count": len(self._calls),
            "calls": [
                {
                    "index": call.index,
                    "path": call.path,
                    "key": key_fingerprint(call.api_key) if call.api_key else None,
                    "stream": call.stream,
                    "model": call.body.get("model"),
                    "fault": call.fault.value if call.fault else None,
                    "at": call.at,
                }
                for call in self._calls
            ],
            "faults": {fault.value: count for fault, count in self._faults.items()},
            "key_calls": {
                key_fingerprint(api_key): count
                for api_key, count in self._key_calls.items()
                if api_key
            },
            "key_limits": {
                key_fingerprint(api_key): limit for api_key, limit in self._key_limits.items()
            },
            "behaviors": {
                "default": asdict(self._default_behavior),
                "by_key": {
                    key_fingerprint(api_key): asdict(behavior)
                    for api_key, behavior in self._key_behaviors.items()
                },
            },
            "max_concurrency": self._max_concurrency,
            "broken_pipes": self._broken_pipes,
        }

    def calls_state(self) -> dict[str, Any]:
        return {
            "calls": [
                {
                    "index": call.index,
                    "path": call.path,
                    "key": key_fingerprint(call.api_key) if call.api_key else None,
                    "stream": call.stream,
                    "model": call.body.get("model"),
                    "fault": call.fault.value if call.fault else None,
                    "at": call.at,
                    "body": dict(call.body),
                }
                for call in self._calls
            ]
        }

    # ---- 调用计数 ---------------------------------------------------------

    def begin_call(
        self, *, api_key: str | None, path: str, body: Mapping[str, Any]
    ) -> tuple[ModelProbeBehavior, ModelProbeFault | None]:
        """登记一次调用并返回 (本次行为, 注入故障)；调用方在 finally 里 `end_call()`。"""
        self._inflight += 1
        self._max_concurrency = max(self._max_concurrency, self._inflight)
        self._key_calls[api_key or ""] += 1
        behavior = self._key_behaviors.get(api_key or "", self._default_behavior)
        limit = self._key_limits.get(api_key or "")
        fault = _classify(behavior, key_calls=self._key_calls[api_key or ""], limit=limit)
        if fault is not None:
            self._faults[fault] += 1
        self._calls.append(
            ModelProbeCall(
                index=len(self._calls) + 1,
                path=path,
                api_key=api_key,
                stream=body.get("stream") is True,
                body=body,
                at=time.monotonic(),
                fault=fault,
            )
        )
        return behavior, fault

    def end_call(self) -> None:
        self._inflight -= 1

    def record_broken_pipe(self) -> None:
        self._broken_pipes += 1


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers") or ():
        if key.lower() == name:
            return value.decode("latin-1")
    return None


async def _read_body(receive: Receive) -> bytes:
    chunks: list[bytes] = []
    while True:
        message = await receive()
        if message.get("type") == "http.disconnect":
            return b"".join(chunks)
        chunks.append(message.get("body", b""))
        if not message.get("more_body", False):
            return b"".join(chunks)


async def _send_json(send: Send, status: int, payload: Mapping[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


async def _send_data(
    send: Send, probe: AsyncModelProbe, payload: object, *, more_body: bool = True
) -> bool:
    """写一块 SSE 数据；客户端断开时计数并返回 False（显式观测，不静默吞掉）。"""
    data = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    try:
        await send(
            {
                "type": "http.response.body",
                "body": f"data: {data}\n\n".encode(),
                "more_body": more_body,
            }
        )
    except Exception:  # noqa: BLE001 - ASGI 服务器断连时抛出的异常类型依实现而定
        probe.record_broken_pipe()
        return False
    return True


async def _respond_chat(
    send: Send,
    probe: AsyncModelProbe,
    body: Mapping[str, Any],
    behavior: ModelProbeBehavior,
    fault: ModelProbeFault | None,
) -> None:
    model = str(body.get("model") or DEFAULT_MODEL)
    if fault is ModelProbeFault.KEY_QUOTA_EXCEEDED:
        behavior = replace(
            behavior,
            status_code=429,
            retry_after_sec=behavior.retry_after_sec or KEY_QUOTA_RETRY_AFTER_SEC,
            drop_response=False,
            cutoff_after_deltas=None,
        )
    if behavior.status_code != 200:
        headers: list[tuple[bytes, bytes]] = [(b"content-type", b"application/json")]
        if behavior.status_code == 429:
            headers.append(
                (b"retry-after", str(behavior.retry_after_sec or KEY_QUOTA_RETRY_AFTER_SEC).encode())
            )
        payload = json.dumps(
            {"error": {"message": f"probe injected status {behavior.status_code}"}}
        ).encode()
        headers.append((b"content-length", str(len(payload)).encode()))
        await send({"type": "http.response.start", "status": behavior.status_code, "headers": headers})
        await send({"type": "http.response.body", "body": payload})
        return
    if behavior.drop_response:
        # ASGI 应用层无法做到「零响应字节」；容量负载请用 cutoff_after_deltas。
        return
    if behavior.latency_ms:
        await asyncio.sleep(behavior.latency_ms / 1000)
    tool_call = (
        behavior.tool_call
        if behavior.tool_call is not None and _should_emit_tool_call(body)
        else None
    )
    if body.get("stream") is True:
        await _write_stream(send, probe, behavior, model, tool_call)
        return
    await _send_json(send, 200, _completion_payload(model, behavior, tool_call))


async def _write_stream(
    send: Send,
    probe: AsyncModelProbe,
    behavior: ModelProbeBehavior,
    model: str,
    tool_call: Mapping[str, Any] | None = None,
) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"text/event-stream"),
                (b"cache-control", b"no-store"),
            ],
        }
    )
    if tool_call is not None:
        if not await _send_data(send, probe, _tool_call_payload(model, tool_call)):
            return
        if not await _send_data(send, probe, _final_payload(model, behavior, "tool_calls")):
            return
        await _send_data(send, probe, "[DONE]", more_body=False)
        return
    for index, delta in enumerate(behavior.deltas):
        if not await _send_data(send, probe, _delta_payload(model, delta)):
            return
        if behavior.cutoff_after_deltas is not None and index + 1 >= behavior.cutoff_after_deltas:
            # 断流：不发终止块、不发 [DONE]，直接结束响应体。
            await send({"type": "http.response.body", "body": b"", "more_body": False})
            return
        if behavior.chunk_interval_ms:
            await asyncio.sleep(behavior.chunk_interval_ms / 1000)
    if not await _send_data(send, probe, _final_payload(model, behavior)):
        return
    await _send_data(send, probe, "[DONE]", more_body=False)


async def _handle_chat(send: Send, probe: AsyncModelProbe, scope: Scope, body: dict[str, Any]) -> None:
    api_key = _bearer(_header(scope, b"authorization"))
    behavior, fault = probe.begin_call(
        api_key=api_key, path=CHAT_COMPLETIONS_PATH, body=body
    )
    try:
        await _respond_chat(send, probe, body, behavior, fault)
    finally:
        probe.end_call()


async def _handle_control(
    send: Send, probe: AsyncModelProbe, path: str, body: dict[str, Any] | None
) -> bool:
    if path == CONTROL_RESET_PATH:
        probe.reset()
        await _send_json(send, 200, {"reset": True})
        return True
    if path == CONTROL_BEHAVIOR_PATH:
        assert body is not None
        api_key = body.get("api_key")
        if api_key is not None and not isinstance(api_key, str):
            await _send_json(send, 400, {"error": "api_key must be a string or null"})
            return True
        try:
            behavior = _behavior_from_payload(body.get("behavior"))
        except (TypeError, ValueError) as exc:
            await _send_json(send, 400, {"error": str(exc)})
            return True
        probe.set_behavior(behavior, api_key=api_key)
        await _send_json(send, 200, {"behavior": asdict(behavior), "api_key": api_key})
        return True
    if path == CONTROL_KEY_LIMITS_PATH:
        assert body is not None
        limits = body.get("key_limits")
        if not isinstance(limits, Mapping):
            await _send_json(send, 400, {"error": "key_limits must be an object"})
            return True
        try:
            for api_key, limit in limits.items():
                probe.set_key_limit(str(api_key), limit)
        except (TypeError, ValueError) as exc:
            await _send_json(send, 400, {"error": str(exc)})
            return True
        await _send_json(
            send,
            200,
            {"key_limits": {key_fingerprint(str(key)): value for key, value in limits.items()}},
        )
        return True
    return False


def _parse_json(raw: bytes) -> dict[str, Any] | None:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def create_app(probe: AsyncModelProbe | None = None) -> Callable[..., Awaitable[None]]:
    """构造 ASGI 应用；控制面与同步探针完全同形。"""
    bound = probe or AsyncModelProbe()

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
            return
        if scope["type"] != "http":
            return
        path = str(scope.get("path") or "")
        method = str(scope.get("method") or "GET")
        if method == "GET" or method == "HEAD":
            if path == HEALTH_PATH:
                await _send_json(send, 200, {"status": "ok"})
                return
            if path == MODELS_PATH:
                await _send_json(send, 200, {"data": [{"id": DEFAULT_MODEL, "object": "model"}]})
                return
            if path == CONTROL_STATE_PATH:
                await _send_json(send, 200, bound.state())
                return
            if path == CONTROL_CALLS_PATH:
                await _send_json(send, 200, bound.calls_state())
                return
            if path == CONTROL_RESET_PATH:
                await _handle_control(send, bound, path, None)
                return
            await _send_json(send, 404, {"error": "not found"})
            return
        if method != "POST":
            await _send_json(send, 405, {"error": "method not allowed"})
            return
        raw = await _read_body(receive)
        body = _parse_json(raw)
        if body is None:
            await _send_json(send, 400, {"error": "request body must be a JSON object"})
            return
        if path == CHAT_COMPLETIONS_PATH:
            await _handle_chat(send, bound, scope, body)
            return
        if await _handle_control(send, bound, path, body):
            return
        await _send_json(send, 404, {"error": "not found"})

    app.probe = bound  # type: ignore[attr-defined]
    return app


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="tests.acceptance.capacity.asgi_probe")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=19090)
    parser.add_argument("--backlog", type=int, default=2048)
    parser.add_argument("--log-level", default="warning")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    import uvicorn

    app = create_app()
    print(
        json.dumps({"url": f"http://{args.host}:{args.port}", "backlog": args.backlog}),
        flush=True,
    )
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        backlog=args.backlog,
        timeout_keep_alive=75,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
