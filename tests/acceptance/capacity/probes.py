"""容量验收真实协议探针（TASK-106；设计 §2.5 验收条件 / §4.3 分层验收与报告）。

三类探针，全部是**真实 socket 服务**，不含服务方法替身、不连 PG/Redis：

1. `ModelProbeServer` —— OpenAI 兼容的**真实 HTTP 模型探针**：可编程延迟、SSE 增量、
   usage、429（带 Retry-After）、5xx、断流（无收尾信号）与应答丢失（连接关闭、零响应），
   并按物理 Key 计数/限流（真实 HTTP Key 计数服务）。每次注入逐调用记入 `faults`。
2. `WeComProbeServer` —— **企微真实 SDK 协议探针**（真实 TLS/WSS）：认证帧、心跳、消息/
   事件回调、会话内回复、主动发送、上传三步；注入认证拒绝、断线、发送失败、限流与 ACK
   丢失，并观测帧/回执/连接。
3. `FaultProxy` —— **故障协议探针**：真实 TCP 转发器，可对任意上游（PG/Redis/HTTP）
   注入延迟、连接中断与拒绝连接，并记录连接/字节/丢弃计数。

子进程模式：`python -m tests.acceptance.capacity.probes --port N` 在真实监听后打印
`{"url": ...}`，并以真实 HTTP 控制面（`/__probe__/behavior`、`/__probe__/key-limits`、
`/__probe__/reset`、`/__probe__/state`）供跨进程驱动与观测。控制面只回 Key 指纹，
不回凭据原文；故障注入一律显式计数，不静默吞掉。

验收注意：本机若配置了系统/环境代理（macOS 经 `urllib.request.getproxies()` 生效），
httpx 默认 `trust_env=True` 会**经代理**访问探针，断流/应答丢失会被代理改写成 502 ——
调用方（用例与容量栈）须显式 `trust_env=False`（或给 Provider 传
`httpx.AsyncHTTPTransport(trust_env=False)`）才能测到探针的真实 socket 行为。
`latency_ms` 只作用于成功响应；429/5xx/断流/应答丢失是即时注入的故障形态。
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import socket
import threading
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from enum import StrEnum
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from tests.e2e.wecom_probe_app import (
    CMD_RESPONSE,
    CMD_SEND_MSG,
    WeComProbe,
)

__all__ = [
    "FaultProxy",
    "FaultProxyMode",
    "FaultProxyState",
    "ModelProbeBehavior",
    "ModelProbeCall",
    "ModelProbeFault",
    "ModelProbeServer",
    "WeComAck",
    "WeComProbeServer",
    "key_fingerprint",
]

CHAT_COMPLETIONS_PATH = "/v1/chat/completions"
MODELS_PATH = "/v1/models"
HEALTH_PATH = "/healthz"
CONTROL_BEHAVIOR_PATH = "/__probe__/behavior"
CONTROL_KEY_LIMITS_PATH = "/__probe__/key-limits"
CONTROL_RESET_PATH = "/__probe__/reset"
CONTROL_STATE_PATH = "/__probe__/state"
DEFAULT_MODEL = "gpt-4o-mini"
KEY_QUOTA_RETRY_AFTER_SEC = 1
CONNECT_TIMEOUT_SEC = 5.0
_PUMP_CHUNK_BYTES = 65536
_BEHAVIOR_FIELDS = (
    "status_code",
    "retry_after_sec",
    "latency_ms",
    "chunk_interval_ms",
    "deltas",
    "usage_prompt_tokens",
    "usage_completion_tokens",
    "cutoff_after_deltas",
    "drop_response",
)


def key_fingerprint(api_key: str) -> str:
    """物理 Key 的稳定指纹：观测快照与控制面只回指纹，不回凭据原文。"""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------------------
# 模型 HTTP 探针
# --------------------------------------------------------------------------------------


class ModelProbeFault(StrEnum):
    """可注入的模型侧故障类别；每次注入都记入 `ModelProbeServer.faults`。"""

    RATE_LIMITED = "RATE_LIMITED"
    SERVER_ERROR = "SERVER_ERROR"
    STREAM_CUTOFF = "STREAM_CUTOFF"
    RESPONSE_LOST = "RESPONSE_LOST"
    KEY_QUOTA_EXCEEDED = "KEY_QUOTA_EXCEEDED"


@dataclass(frozen=True, slots=True)
class ModelProbeBehavior:
    """一次模型调用的可编程行为（严格校验，拒绝自相矛盾的组合）。

    语义：
    - `latency_ms` 是首块前的延迟（真实 sleep），`chunk_interval_ms` 是增量之间的节拍；
    - `deltas` 是 SSE 正文增量（按序下发），`usage_*` 进终止块的 `usage`；
    - `status_code != 200` ⇒ 429（带 `retry_after_sec`）/5xx 等错误响应；
    - `cutoff_after_deltas` ⇒ 断流：下发 N 块后直接关连接，不发终止块与 `[DONE]`；
    - `drop_response` ⇒ 应答丢失：请求已记录，连接关闭且不写任何响应。
    """

    status_code: int = 200
    retry_after_sec: int | None = None
    latency_ms: int = 0
    chunk_interval_ms: int = 0
    deltas: tuple[str, ...] = ("pong",)
    usage_prompt_tokens: int = 5
    usage_completion_tokens: int = 3
    cutoff_after_deltas: int | None = None
    drop_response: bool = False

    def __post_init__(self) -> None:
        for name in (
            "status_code",
            "latency_ms",
            "chunk_interval_ms",
            "usage_prompt_tokens",
            "usage_completion_tokens",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"{name} must be an int")
        if not 100 <= self.status_code < 600:
            raise ValueError("status_code must be a valid HTTP status")
        for name in ("latency_ms", "chunk_interval_ms", "usage_prompt_tokens", "usage_completion_tokens"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative")
        if self.retry_after_sec is not None and self.retry_after_sec < 0:
            raise ValueError("retry_after_sec must be non-negative")
        if not self.deltas or not all(isinstance(delta, str) for delta in self.deltas):
            raise ValueError("deltas must be a non-empty sequence of strings")
        if self.cutoff_after_deltas is not None:
            if not isinstance(self.cutoff_after_deltas, int) or isinstance(
                self.cutoff_after_deltas, bool
            ):
                raise ValueError("cutoff_after_deltas must be an int")
            if not 0 <= self.cutoff_after_deltas <= len(self.deltas):
                raise ValueError("cutoff_after_deltas must be within the delta count")
        if self.drop_response and self.cutoff_after_deltas is not None:
            raise ValueError("drop_response and cutoff_after_deltas are mutually exclusive")
        if self.drop_response and self.status_code != 200:
            raise ValueError("drop_response requires status_code 200")


@dataclass(frozen=True, slots=True)
class ModelProbeCall:
    """探针真实收到的一次模型调用（含注入类别；`api_key` 仅进程内可读）。"""

    index: int
    path: str
    api_key: str | None
    stream: bool
    body: Mapping[str, Any]
    at: float
    fault: ModelProbeFault | None


def _delta_payload(model: str, text: str) -> dict[str, Any]:
    return {
        "id": "chatcmpl-capacity-probe",
        "object": "chat.completion.chunk",
        "model": model,
        "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
    }


def _final_payload(model: str, behavior: ModelProbeBehavior) -> dict[str, Any]:
    return {
        "id": "chatcmpl-capacity-probe",
        "object": "chat.completion.chunk",
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        "usage": _usage_payload(behavior),
    }


def _usage_payload(behavior: ModelProbeBehavior) -> dict[str, int]:
    return {
        "prompt_tokens": behavior.usage_prompt_tokens,
        "completion_tokens": behavior.usage_completion_tokens,
        "total_tokens": behavior.usage_prompt_tokens + behavior.usage_completion_tokens,
    }


def _completion_payload(model: str, behavior: ModelProbeBehavior) -> dict[str, Any]:
    return {
        "id": "chatcmpl-capacity-probe",
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "".join(behavior.deltas)},
                "finish_reason": "stop",
            }
        ],
        "usage": _usage_payload(behavior),
    }


def _bearer(header: str | None) -> str | None:
    if not header:
        return None
    prefix = "Bearer "
    return header[len(prefix) :] if header.startswith(prefix) else header


class ModelProbeServer:
    """真实 HTTP 模型探针：可编程响应/延迟/故障 + 真实 Key 计数与观测。"""

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        behavior: ModelProbeBehavior | None = None,
        key_limits: Mapping[str, int] | None = None,
    ) -> None:
        self._host = host
        self._requested_port = port
        self._lock = threading.Lock()
        self._default_behavior = behavior or ModelProbeBehavior()
        self._key_behaviors: dict[str, ModelProbeBehavior] = {}
        self._key_limits: dict[str, int] = {}
        self._calls: list[ModelProbeCall] = []
        self._faults: Counter[ModelProbeFault] = Counter()
        self._key_calls: Counter[str] = Counter()
        self._inflight = 0
        self._max_concurrency = 0
        self._broken_pipes = 0
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        for api_key, limit in (key_limits or {}).items():
            self.set_key_limit(api_key, limit)
        self._build_server()

    # ---- 生命周期 ---------------------------------------------------------

    def _build_server(self) -> None:
        endpoint = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: object) -> None:
                return

            def do_GET(self) -> None:  # noqa: N802
                endpoint._handle_get(self)

            def do_POST(self) -> None:  # noqa: N802
                endpoint._handle_post(self)

        server = ThreadingHTTPServer((self._host, self._requested_port), Handler)
        server.daemon_threads = True
        self._server = server

    def start(self) -> str:
        server = self._server
        if server is None:
            raise RuntimeError("model probe server was already stopped")
        self._thread = threading.Thread(target=server.serve_forever, daemon=True)
        self._thread.start()
        return self.url

    def stop(self) -> None:
        server, self._server = self._server, None
        if server is None:
            return
        server.shutdown()
        server.server_close()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=5.0)

    def __enter__(self) -> ModelProbeServer:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    @property
    def url(self) -> str:
        server = self._server
        if server is None:
            raise RuntimeError("model probe server is not listening")
        host, port = server.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def listening(self) -> bool:
        return self._server is not None

    # ---- 可编程行为与观测 -------------------------------------------------

    def set_behavior(self, behavior: ModelProbeBehavior, *, api_key: str | None = None) -> None:
        if not isinstance(behavior, ModelProbeBehavior):
            raise TypeError("behavior must be a ModelProbeBehavior")
        with self._lock:
            if api_key is None:
                self._default_behavior = behavior
            else:
                self._key_behaviors[api_key] = behavior

    def behavior_for(self, api_key: str | None) -> ModelProbeBehavior:
        with self._lock:
            return self._key_behaviors.get(api_key or "", self._default_behavior)

    def set_key_limit(self, api_key: str, limit: int) -> None:
        if not api_key:
            raise ValueError("api_key must not be empty")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("key limit must be a positive int")
        with self._lock:
            self._key_limits[api_key] = limit

    def reset(self) -> None:
        with self._lock:
            self._calls.clear()
            self._faults.clear()
            self._key_calls.clear()
            self._key_limits.clear()
            self._key_behaviors.clear()
            self._max_concurrency = 0
            self._broken_pipes = 0

    @property
    def calls(self) -> tuple[ModelProbeCall, ...]:
        with self._lock:
            return tuple(self._calls)

    @property
    def faults(self) -> Mapping[ModelProbeFault, int]:
        with self._lock:
            return dict(self._faults)

    @property
    def key_calls(self) -> Mapping[str, int]:
        with self._lock:
            return dict(self._key_calls)

    @property
    def max_concurrency(self) -> int:
        with self._lock:
            return self._max_concurrency

    @property
    def broken_pipes(self) -> int:
        """客户端提前断开导致的写失败次数（可观测，不静默吞掉）。"""
        with self._lock:
            return self._broken_pipes

    def state(self) -> dict[str, Any]:
        """控制面观测快照：只含 Key 指纹，不含凭据原文。"""
        with self._lock:
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
                    key_fingerprint(api_key): limit
                    for api_key, limit in self._key_limits.items()
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

    # ---- HTTP 路由 --------------------------------------------------------

    def _handle_get(self, handler: BaseHTTPRequestHandler) -> None:
        if handler.path == HEALTH_PATH:
            self._write_json(handler, 200, {"status": "ok"})
            return
        if handler.path == MODELS_PATH:
            self._write_json(handler, 200, {"data": [{"id": DEFAULT_MODEL, "object": "model"}]})
            return
        if handler.path == CONTROL_STATE_PATH:
            self._write_json(handler, 200, self.state())
            return
        self._write_json(handler, 404, {"error": "not found"})

    def _handle_post(self, handler: BaseHTTPRequestHandler) -> None:
        body = self._read_json(handler)
        if body is None:
            return
        if handler.path == CHAT_COMPLETIONS_PATH:
            self._handle_chat(handler, body)
            return
        if handler.path == CONTROL_BEHAVIOR_PATH:
            self._handle_control_behavior(handler, body)
            return
        if handler.path == CONTROL_KEY_LIMITS_PATH:
            self._handle_control_key_limits(handler, body)
            return
        if handler.path == CONTROL_RESET_PATH:
            self.reset()
            self._write_json(handler, 200, {"reset": True})
            return
        self._write_json(handler, 404, {"error": "not found"})

    def _read_json(self, handler: BaseHTTPRequestHandler) -> dict[str, Any] | None:
        raw = handler.rfile.read(int(handler.headers.get("Content-Length", "0") or "0"))
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except ValueError:
            self._write_json(handler, 400, {"error": "request body is not valid JSON"})
            return None
        if not isinstance(parsed, dict):
            self._write_json(handler, 400, {"error": "request body must be an object"})
            return None
        return parsed

    def _handle_control_behavior(
        self, handler: BaseHTTPRequestHandler, body: dict[str, Any]
    ) -> None:
        api_key = body.get("api_key")
        if api_key is not None and not isinstance(api_key, str):
            self._write_json(handler, 400, {"error": "api_key must be a string or null"})
            return
        try:
            behavior = _behavior_from_payload(body.get("behavior"))
        except (TypeError, ValueError) as exc:
            self._write_json(handler, 400, {"error": str(exc)})
            return
        self.set_behavior(behavior, api_key=api_key)
        self._write_json(handler, 200, {"behavior": asdict(behavior), "api_key": api_key})

    def _handle_control_key_limits(
        self, handler: BaseHTTPRequestHandler, body: dict[str, Any]
    ) -> None:
        limits = body.get("key_limits")
        if not isinstance(limits, Mapping):
            self._write_json(handler, 400, {"error": "key_limits must be an object"})
            return
        try:
            for api_key, limit in limits.items():
                self.set_key_limit(str(api_key), limit)
        except (TypeError, ValueError) as exc:
            self._write_json(handler, 400, {"error": str(exc)})
            return
        self._write_json(
            handler, 200, {"key_limits": {key_fingerprint(key): value for key, value in limits.items()}}
        )

    # ---- 模型调用 ---------------------------------------------------------

    def _handle_chat(self, handler: BaseHTTPRequestHandler, body: dict[str, Any]) -> None:
        api_key = _bearer(handler.headers.get("Authorization"))
        with self._lock:
            self._inflight += 1
            self._max_concurrency = max(self._max_concurrency, self._inflight)
            self._key_calls[api_key or ""] += 1
            key_calls = self._key_calls[api_key or ""]
            behavior = self._key_behaviors.get(api_key or "", self._default_behavior)
            limit = self._key_limits.get(api_key or "")
        try:
            fault = _classify(behavior, key_calls=key_calls, limit=limit)
            with self._lock:
                if fault is not None:
                    self._faults[fault] += 1
                self._calls.append(
                    ModelProbeCall(
                        index=len(self._calls) + 1,
                        path=handler.path,
                        api_key=api_key,
                        stream=body.get("stream") is True,
                        body=body,
                        at=time.monotonic(),
                        fault=fault,
                    )
                )
            self._respond_chat(handler, body, behavior, fault)
        finally:
            with self._lock:
                self._inflight -= 1

    def _respond_chat(
        self,
        handler: BaseHTTPRequestHandler,
        body: dict[str, Any],
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
            headers = (
                {"Retry-After": str(behavior.retry_after_sec or KEY_QUOTA_RETRY_AFTER_SEC)}
                if behavior.status_code == 429
                else {}
            )
            self._write_json(
                handler,
                behavior.status_code,
                {"error": {"message": f"probe injected status {behavior.status_code}"}},
                headers=headers,
            )
            return
        if behavior.drop_response:
            # 应答丢失：请求已记录，连接直接关闭且不写任何响应字节。
            handler.close_connection = True
            return
        if behavior.latency_ms:
            time.sleep(behavior.latency_ms / 1000)
        if body.get("stream") is True:
            self._write_stream(handler, behavior, model)
            return
        self._write_json(handler, 200, _completion_payload(model, behavior))

    def _write_stream(
        self,
        handler: BaseHTTPRequestHandler,
        behavior: ModelProbeBehavior,
        model: str,
    ) -> None:
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Connection", "close")
        handler.end_headers()
        handler.close_connection = True
        for index, delta in enumerate(behavior.deltas):
            if not self._write_sse(handler, _delta_payload(model, delta)):
                return
            if behavior.cutoff_after_deltas is not None and index + 1 >= behavior.cutoff_after_deltas:
                return  # 断流：不发终止块、不发 [DONE]
            if behavior.chunk_interval_ms:
                time.sleep(behavior.chunk_interval_ms / 1000)
        if not self._write_sse(handler, _final_payload(model, behavior)):
            return
        self._write_sse(handler, "[DONE]")

    def _write_sse(self, handler: BaseHTTPRequestHandler, payload: object) -> bool:
        data = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        return self._write(handler, f"data: {data}\n\n".encode())

    def _write_json(
        self,
        handler: BaseHTTPRequestHandler,
        status: int,
        payload: Mapping[str, Any],
        *,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        for name, value in (headers or {}).items():
            handler.send_header(name, value)
        handler.end_headers()
        self._write(handler, body)

    def _write(self, handler: BaseHTTPRequestHandler, data: bytes) -> bool:
        try:
            handler.wfile.write(data)
            handler.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            with self._lock:
                self._broken_pipes += 1
            return False
        return True


def _classify(
    behavior: ModelProbeBehavior, *, key_calls: int, limit: int | None
) -> ModelProbeFault | None:
    if limit is not None and key_calls > limit:
        return ModelProbeFault.KEY_QUOTA_EXCEEDED
    if behavior.status_code == 429:
        return ModelProbeFault.RATE_LIMITED
    if behavior.status_code >= 500:
        return ModelProbeFault.SERVER_ERROR
    if behavior.drop_response:
        return ModelProbeFault.RESPONSE_LOST
    if behavior.cutoff_after_deltas is not None:
        return ModelProbeFault.STREAM_CUTOFF
    return None


def _behavior_from_payload(raw: Any) -> ModelProbeBehavior:
    if not isinstance(raw, Mapping):
        raise ValueError("behavior must be an object")
    unknown = sorted(set(raw) - set(_BEHAVIOR_FIELDS))
    if unknown:
        raise ValueError(f"unknown behavior field(s): {', '.join(unknown)}")
    values: dict[str, Any] = dict(raw)
    if "deltas" in values:
        deltas = values["deltas"]
        if not isinstance(deltas, (list, tuple)) or not all(
            isinstance(delta, str) for delta in deltas
        ):
            raise ValueError("deltas must be a list of strings")
        values["deltas"] = tuple(deltas)
    return ModelProbeBehavior(**values)


# --------------------------------------------------------------------------------------
# 企微真实 SDK 协议探针（在既有帧级探针上增加容量观测与故障注入）
# --------------------------------------------------------------------------------------

THROTTLE_ERRCODE = 45009


@dataclass(frozen=True, slots=True)
class WeComAck:
    """探针在真实 socket 上回出的一个协议回执。"""

    bot_id: str
    cmd: str
    errcode: int
    at: float


@dataclass
class WeComProbeServer(WeComProbe):
    """企微协议探针（容量口径）：认证/心跳/收发/上传 + 限流、ACK 丢失注入与观测。

    继承 `tests.e2e.wecom_probe_app.WeComProbe` 的帧级协议实现（认证、心跳、回调、
    回复、主动发送、上传三步、认证拒绝/断线/发送失败注入），只增加容量验收需要的：
    限流回执（`errcode=45009`）、ACK 丢失（帧到达但无回执）、回执时刻与帧计数观测。
    """

    ack_loss_bots: set[str] = field(default_factory=set)
    throttle_bots: set[str] = field(default_factory=set)
    acks: list[WeComAck] = field(default_factory=list)
    ack_losses: int = 0
    throttled: int = 0

    async def __aenter__(self) -> WeComProbeServer:
        await self.start()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.stop()

    def _respond(self, frame: dict[str, Any], index: int) -> dict[str, Any] | None:
        response = super()._respond(frame, index)
        if response is None:
            return None
        cmd = str(frame.get("cmd") or "")
        bot_id = self.connection_bots.get(index, "") or str(
            (frame.get("body") or {}).get("bot_id") or ""
        )
        if cmd in (CMD_RESPONSE, CMD_SEND_MSG) and bot_id in self.ack_loss_bots:
            self.ack_losses += 1
            return None
        if cmd in (CMD_RESPONSE, CMD_SEND_MSG) and bot_id in self.throttle_bots:
            self.throttled += 1
            response = {**response, "errcode": THROTTLE_ERRCODE, "errmsg": "rate limited"}
        self.acks.append(
            WeComAck(bot_id=bot_id, cmd=cmd, errcode=int(response.get("errcode") or 0), at=time.monotonic())
        )
        return response

    def clear_injections(self) -> None:
        super().clear_injections()
        self.ack_loss_bots.clear()
        self.throttle_bots.clear()

    def stats(self) -> dict[str, Any]:
        counts = Counter(str(item.frame.get("cmd") or "") for item in self.received)
        return {
            "frames": dict(counts),
            "replies": len(self.replies),
            "acks": len(self.acks),
            "ack_losses": self.ack_losses,
            "throttled": self.throttled,
            "auth_failures": len(self.auth_failures),
            "connections": len(self.connections),
            "uploads": len(self.uploads),
        }


# --------------------------------------------------------------------------------------
# 故障协议探针：真实 TCP 转发 + 故障注入
# --------------------------------------------------------------------------------------


class FaultProxyMode(StrEnum):
    """故障注入形态：直通、延迟、连接中断、拒绝连接。"""

    PASS_THROUGH = "PASS_THROUGH"
    DELAY = "DELAY"
    DROP = "DROP"
    REFUSE = "REFUSE"


@dataclass(frozen=True, slots=True)
class FaultProxyState:
    mode: FaultProxyMode
    delay_ms: int
    listening: bool


class FaultProxy:
    """真实 TCP 故障代理：上游可为任意 TCP 端点（PG/Redis/HTTP）。

    注入形态（真实网络行为，不做协议层替身）：
    - `DELAY`：转发前 sleep，观测端到端延迟；
    - `DROP`：切断全部在飞连接，并丢弃后续连接（连接中断）；
    - `REFUSE`：关闭监听，客户端得到真实 ECONNREFUSED（拒绝连接）；
    - `PASS_THROUGH`：恢复直通。
    """

    def __init__(
        self,
        upstream_host: str,
        upstream_port: int,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
    ) -> None:
        if not upstream_host:
            raise ValueError("upstream_host must not be empty")
        if not isinstance(upstream_port, int) or isinstance(upstream_port, bool):
            raise ValueError("upstream_port must be an int")
        if not 0 < upstream_port < 65536:
            raise ValueError("upstream_port must be a valid TCP port")
        self._upstream = (upstream_host, upstream_port)
        self._host = host
        self._requested_port = port
        self._lock = threading.Lock()
        self._mode = FaultProxyMode.PASS_THROUGH
        self._delay_ms = 0
        self._listener: socket.socket | None = None
        self._port = 0
        self._thread: threading.Thread | None = None
        self._stopping = False
        self._active: set[socket.socket] = set()
        self._connections = 0
        self._dropped = 0
        self._forwarded_bytes = 0
        self._upstream_failures = 0

    # ---- 生命周期 ---------------------------------------------------------

    def start(self) -> str:
        if self._listener is not None:
            raise RuntimeError("fault proxy is already started")
        self._stopping = False
        listener = self._bind()
        self._listener = listener
        self._port = int(listener.getsockname()[1])
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()
        return self.url

    def stop(self) -> None:
        with self._lock:
            self._stopping = True
            listener, self._listener = self._listener, None
            self._close_active()
        if listener is not None:
            listener.close()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=5.0)

    def __enter__(self) -> FaultProxy:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    def _bind(self) -> socket.socket:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((self._host, self._port or self._requested_port))
        listener.listen(64)
        return listener

    @property
    def address(self) -> tuple[str, int]:
        if not self._port:
            raise RuntimeError("fault proxy is not listening")
        return self._host, self._port

    @property
    def url(self) -> str:
        host, port = self.address
        return f"tcp://{host}:{port}"

    # ---- 故障注入与观测 ---------------------------------------------------

    def set_fault(self, mode: FaultProxyMode, *, delay_ms: int = 0) -> None:
        if not isinstance(mode, FaultProxyMode):
            raise TypeError("mode must be a FaultProxyMode")
        if not isinstance(delay_ms, int) or isinstance(delay_ms, bool) or delay_ms < 0:
            raise ValueError("delay_ms must be a non-negative int")
        if mode is FaultProxyMode.DELAY and delay_ms <= 0:
            raise ValueError("DELAY requires delay_ms > 0")
        with self._lock:
            self._mode = mode
            self._delay_ms = delay_ms
            listener = self._listener
            if mode is FaultProxyMode.REFUSE:
                self._listener = None
                self._close_active()
            else:
                if self._listener is None:
                    self._listener = self._bind()
                if mode is FaultProxyMode.DROP:
                    self._close_active()
        if mode is FaultProxyMode.REFUSE and listener is not None:
            listener.close()

    @property
    def mode(self) -> FaultProxyMode:
        with self._lock:
            return self._mode

    @property
    def listening(self) -> bool:
        with self._lock:
            return self._listener is not None

    @property
    def connections(self) -> int:
        with self._lock:
            return self._connections

    @property
    def dropped_connections(self) -> int:
        with self._lock:
            return self._dropped

    @property
    def forwarded_bytes(self) -> int:
        with self._lock:
            return self._forwarded_bytes

    @property
    def upstream_failures(self) -> int:
        with self._lock:
            return self._upstream_failures

    @property
    def active_connections(self) -> int:
        with self._lock:
            return len(self._active)

    def state(self) -> FaultProxyState:
        with self._lock:
            return FaultProxyState(self._mode, self._delay_ms, self._listener is not None)

    # ---- 转发实现 ---------------------------------------------------------

    def _accept_loop(self) -> None:
        while True:
            with self._lock:
                if self._stopping:
                    return
                listener = self._listener
            if listener is None:
                time.sleep(0.01)
                continue
            try:
                client, _ = listener.accept()
            except OSError:
                time.sleep(0.01)
                continue
            threading.Thread(target=self._serve, args=(client,), daemon=True).start()

    def _serve(self, client: socket.socket) -> None:
        with self._lock:
            self._connections += 1
            mode, delay_ms = self._mode, self._delay_ms
        if mode is not FaultProxyMode.PASS_THROUGH and mode is not FaultProxyMode.DELAY:
            with self._lock:
                self._dropped += 1
            client.close()
            return
        if delay_ms:
            time.sleep(delay_ms / 1000)
        try:
            upstream = socket.create_connection(self._upstream, timeout=CONNECT_TIMEOUT_SEC)
        except OSError:
            with self._lock:
                self._upstream_failures += 1
            client.close()
            return
        with self._lock:
            self._active.update({client, upstream})
        try:
            pump = threading.Thread(target=self._pump, args=(client, upstream), daemon=True)
            pump.start()
            self._pump(upstream, client)
            pump.join(timeout=1.0)
        finally:
            with self._lock:
                self._active.difference_update({client, upstream})
            client.close()
            upstream.close()

    def _pump(self, source: socket.socket, target: socket.socket) -> None:
        try:
            while True:
                data = source.recv(_PUMP_CHUNK_BYTES)
                if not data:
                    break
                target.sendall(data)
                with self._lock:
                    self._forwarded_bytes += len(data)
        except OSError:
            return  # 连接被注入切断/对端关闭：由 dropped/active 计数可观测
        finally:
            with contextlib.suppress(OSError):
                target.shutdown(socket.SHUT_WR)

    def _close_active(self) -> None:
        """关闭全部在飞连接（须持锁调用）。"""
        for active in self._active:
            with contextlib.suppress(OSError):
                active.close()
        self._active.clear()


# --------------------------------------------------------------------------------------
# 子进程模式：真实监听后打印地址，供验收栈跨进程驱动与观测
# --------------------------------------------------------------------------------------


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="tests.acceptance.capacity.probes")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    server = ModelProbeServer(host=args.host, port=args.port)
    server.start()
    print(json.dumps({"url": server.url}), flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
