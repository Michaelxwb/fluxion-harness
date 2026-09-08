"""Runtime 执行面 HTTP Gateway（FEAT-01）。

经 HTTP 调用独立 Runtime Service，替代 production_bundle 中本地
RuntimeApplicationService 创建。复用 ``services/channel_app.py`` 的
RuntimeGateway Protocol（run/stream 签名与返回类型一致）。

Wire 格式复用 ``api/runtime.py`` 真实路由与 §3.3.1 约定，不新增路由：
- ``POST /internal/v1/runtime-profiles/{runtime_profile_id}/runs``
- ``POST /internal/v1/runtime-profiles/{runtime_profile_id}/runs:stream``

超时间/失败策略（规则 18）：
- connect/pool 3s、write 10s；read 按执行预算 + 有界余量（默认 120s，
  构造时可覆盖）；流式另设 idle/整体 read 超时。
- 执行 POST 默认不重试；连接失败映射类型化 RuntimeApplicationError/503；
  已发 token 后不切换路由重新执行；生产远程失败不降级本地执行。

客户端侧均衡（TASK-013 / S-01 实机结论：单共享连接导致请求粘滞）：
- 每次请求解析 base host 的全部 A 记录并 RR 选 endpoint（URL host 重写为
  IP，Host 头保留原名；单 IP/ClusterIP 时零行为变化）；keep-alive 保留
  （连接池按 origin 自然分池）。
- 建连失败（ConnectError，含 ConnectTimeout：请求尚未发出）换下一个
  endpoint 重试一次；读/写/池超时与业务错误不重试（防重复执行）。
- DNS 解析失败 → 回落单 endpoint（base host 本身），行为退化为改前。
"""

from __future__ import annotations

import asyncio
import json
import socket
import traceback
from collections.abc import AsyncIterator
from urllib.parse import quote, urlsplit

import httpx

from fluxion.errors.console import SUCCESS
from fluxion.observability.logging import emit_runtime_error_log
from fluxion.services.runtime_contracts import (
    RunRuntimeRequest,
    RunRuntimeResult,
    RuntimeApplicationError,
    RuntimeStreamEvent,
)

_DEFAULT_CONNECT_TIMEOUT = 3.0
_DEFAULT_POOL_TIMEOUT = 3.0
_DEFAULT_WRITE_TIMEOUT = 10.0
_DEFAULT_READ_TIMEOUT = 120.0
_DEFAULT_STREAM_READ_TIMEOUT = 300.0

# TASK-013：建连失败重试预算——首选 + 换一个 endpoint，共最多 2 次尝试。
_MAX_ENDPOINT_ATTEMPTS = 2


async def _resolve_endpoint_ips(host: str, port: int) -> list[str]:
    """解析 base host 的全部 A/AAAA 记录（去重保序）。

    每次请求调用：成员变化（扩缩容/摘除）即时感知。解析失败抛异常，
    由调用方回落单 endpoint（改前行为）。
    """
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    ips: list[str] = []
    for _, _, _, _, sockaddr in infos:
        ip = str(sockaddr[0])
        if ip not in ips:
            ips.append(ip)
    if not ips:
        raise OSError(f"no addresses for {host}")
    return ips


def _bracketed(ip: str) -> str:
    return f"[{ip}]" if ":" in ip and not ip.startswith("[") else ip


class HttpRuntimeGateway:
    """经独立 Runtime Service 执行的 RuntimeGateway 实现。"""

    def __init__(
        self,
        base_url: str,
        *,
        client: httpx.AsyncClient | None = None,
        read_timeout_seconds: float = _DEFAULT_READ_TIMEOUT,
        stream_read_timeout_seconds: float = _DEFAULT_STREAM_READ_TIMEOUT,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._read_timeout = read_timeout_seconds
        self._stream_read_timeout = stream_read_timeout_seconds
        parts = urlsplit(self._base_url if "://" in self._base_url else f"http://{self._base_url}")
        self._scheme = parts.scheme or "http"
        self._host = parts.hostname or ""
        default_port = 443 if self._scheme == "https" else 80
        self._port = parts.port or default_port
        self._base_path = parts.path.rstrip("/")
        self._rr_index = 0
        if client is not None:
            self._client = client
            self._owns_client = False
        else:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=_DEFAULT_CONNECT_TIMEOUT,
                    read=read_timeout_seconds,
                    write=_DEFAULT_WRITE_TIMEOUT,
                    pool=_DEFAULT_POOL_TIMEOUT,
                )
            )
            self._owns_client = True

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _endpoint_order(self) -> list[str]:
        """本次请求的 endpoint 尝试顺序（TASK-013）。

        base host 的全部解析 IP，从 RR 游标起取至多 _MAX_ENDPOINT_ATTEMPTS 个
        distinct 值。DNS 失败/单 IP → 回落 [base host]（改前行为）。
        """
        try:
            ips = await _resolve_endpoint_ips(self._host, self._port)
        except OSError:
            return [self._host]
        if len(ips) <= 1:
            return ips or [self._host]
        start = self._rr_index % len(ips)
        self._rr_index += 1
        return [ips[(start + i) % len(ips)] for i in range(min(len(ips), _MAX_ENDPOINT_ATTEMPTS))]

    def _endpoint_url(self, endpoint: str, path: str) -> tuple[str, dict[str, str]]:
        """endpoint → (URL, 额外 headers)。

        未重写（endpoint 即 base host，如 IP 字面量/K8s ClusterIP）时 headers
        为空，wire 零变化；重写时 Host 头保留原名。
        """
        if endpoint == self._host:
            return f"{self._base_url}{path}", {}
        return (
            f"{self._scheme}://{_bracketed(endpoint)}:{self._port}{self._base_path}{path}",
            {"Host": self._host},
        )

    async def run(self, request: RunRuntimeRequest) -> RunRuntimeResult:
        path = f"/internal/v1/runtime-profiles/{quote(request.runtime_profile_id, safe='')}/runs"
        payload = _request_payload(request)
        base_headers = _request_headers(request)
        timeout = httpx.Timeout(
            connect=_DEFAULT_CONNECT_TIMEOUT,
            read=self._read_timeout,
            write=_DEFAULT_WRITE_TIMEOUT,
            pool=_DEFAULT_POOL_TIMEOUT,
        )
        last_error: RuntimeApplicationError | None = None
        for endpoint in await self._endpoint_order():
            url, extra_headers = self._endpoint_url(endpoint, path)
            try:
                response = await self._client.post(
                    url,
                    json=payload,
                    headers={**base_headers, **extra_headers},
                    timeout=timeout,
                )
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                # 建连失败：请求尚未发出，换下一个 endpoint 重试一次（TASK-013）。
                last_error = RuntimeApplicationError(
                    "runtime_unavailable",
                    f"runtime service unavailable: {exc} (endpoint={endpoint})",
                    status_code=503,
                )
                continue
            except httpx.TimeoutException as exc:
                error = RuntimeApplicationError(
                    "runtime_upstream_timeout", f"runtime service timeout: {exc}", status_code=503
                )
                _log_gateway_error(request, "run", error)
                raise error from exc
            except httpx.TransportError as exc:
                error = RuntimeApplicationError(
                    "runtime_unavailable", f"runtime service unavailable: {exc}", status_code=503
                )
                _log_gateway_error(request, "run", error)
                raise error from exc
            try:
                return _parse_run_result(response)
            except RuntimeApplicationError as exc:
                _log_gateway_error(request, "run", exc)
                raise
        assert last_error is not None  # endpoint_order 非空恒成立
        _log_gateway_error(request, "run", last_error)
        raise last_error

    async def stream(self, request: RunRuntimeRequest) -> AsyncIterator[RuntimeStreamEvent]:
        path = (
            "/internal/v1/runtime-profiles/"
            f"{quote(request.runtime_profile_id, safe='')}/runs:stream"
        )
        payload = _request_payload(request)
        base_headers = _request_headers(request)
        timeout = httpx.Timeout(
            connect=_DEFAULT_CONNECT_TIMEOUT,
            read=self._stream_read_timeout,
            write=_DEFAULT_WRITE_TIMEOUT,
            pool=_DEFAULT_POOL_TIMEOUT,
        )
        last_error: RuntimeApplicationError | None = None
        for endpoint in await self._endpoint_order():
            url, extra_headers = self._endpoint_url(endpoint, path)
            headers = {**base_headers, **extra_headers}
            # client.stream() 构造期不做 IO（建连在 __aenter__），异常全在 async with 内处理。
            stream_cm = self._client.stream("POST", url, json=payload, headers=headers, timeout=timeout)
            try:
                async with stream_cm as response:
                    if response.status_code != 200:
                        # 流式建连后非 200：先读完 body 再转错误，不进入事件流。
                        await response.aread()
                        error = _error_from_envelope(response, default_code="runtime_upstream_error")
                        _log_gateway_error(request, "stream", error)
                        raise error
                    async for event in _iter_sse(response):
                        yield event
                return
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                # 建连错误只发生在首字节前（连接尚未建立，无已发 token），
                # 换 endpoint 重试安全；首字节后的失败走 TransportError 分支。
                last_error = RuntimeApplicationError(
                    "runtime_unavailable",
                    f"runtime service unavailable: {exc} (endpoint={endpoint})",
                    status_code=503,
                )
                continue
            except RuntimeApplicationError:
                raise
            except httpx.TimeoutException as exc:
                error = RuntimeApplicationError(
                    "runtime_upstream_timeout", f"runtime service timeout: {exc}", status_code=503
                )
                _log_gateway_error(request, "stream", error)
                raise error from exc
            except httpx.TransportError as exc:
                error = RuntimeApplicationError(
                    "runtime_unavailable", f"runtime service unavailable: {exc}", status_code=503
                )
                _log_gateway_error(request, "stream", error)
                raise error from exc
        assert last_error is not None  # endpoint_order 非空恒成立
        _log_gateway_error(request, "stream", last_error)
        raise last_error


def _request_payload(request: RunRuntimeRequest) -> dict[str, object]:
    payload: dict[str, object] = {
        "tenant_id": request.tenant_id,
        "user_id": request.user_id,
        "session_id": request.session_id,
        "input": request.input_message,
        "runtime_profile_version_selector": request.runtime_profile_version_selector,
        # TASK-005（ADR-A012）：三 ID 随 body 透传；Runtime 入口合并校验。
        "request_id": request.request_id,
        "trace_id": request.trace_id,
        "execution_id": request.execution_id,
        "tool_calls": [
            {"tool_id": call.tool_id, "arguments": dict(call.arguments)}
            for call in request.tool_calls
        ],
    }
    if request.agent_definition_id is not None:
        payload["agent_definition_id"] = request.agent_definition_id
    return payload


def _request_headers(request: RunRuntimeRequest) -> dict[str, str]:
    return {
        "X-Request-ID": request.request_id,
        "X-Trace-ID": request.trace_id,
        "X-Tenant-ID": request.tenant_id,
    }


def _log_gateway_error(
    request: RunRuntimeRequest, method: str, error: RuntimeApplicationError
) -> None:
    """失败分支结构化日志（RULE-backend-logging-001）。

    仅记录 ID/路由/错误类型与堆栈；请求体（含用户输入）与 Secret 永不入日志。
    """
    emit_runtime_error_log(
        request_id=request.request_id,
        trace_id=request.trace_id,
        tenant_id=request.tenant_id,
        execution_id=request.execution_id,
        runtime_profile_id=request.runtime_profile_id,
        error_type=f"http_runtime_gateway.{method}",
        error_code=error.code,
        message=str(error),
        stack=traceback.format_exc(),
    )


def _parse_run_result(response: httpx.Response) -> RunRuntimeResult:
    if response.status_code != 200:
        raise _error_from_envelope(response, default_code="runtime_upstream_error")
    try:
        envelope = response.json()
    except ValueError as exc:
        raise RuntimeApplicationError(
            "runtime_upstream_error",
            f"runtime service returned non-JSON: {exc}",
            status_code=502,
        ) from exc
    if not isinstance(envelope, dict) or envelope.get("code") != SUCCESS:
        raise _error_from_envelope(response, default_code="runtime_application_error")
    data = envelope.get("data")
    if not isinstance(data, dict):
        raise RuntimeApplicationError(
            "runtime_upstream_error", "runtime service returned empty data", status_code=502
        )
    try:
        return RunRuntimeResult(
            request_id=str(data["request_id"]),
            trace_id=str(data["trace_id"]),
            execution_id=str(data["execution_id"]),
            service_instance_id=str(data["service_instance_id"]),
            runtime_profile_id=str(data["runtime_profile_id"]),
            runtime_profile_version=str(data["runtime_profile_version"]),
            output=str(data["output"]),
            latency_ms=float(data["latency_ms"]),
            model_provider_id=data.get("model_provider_id"),
            tool_results=tuple(data.get("tool_results", ()) or ()),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeApplicationError(
            "runtime_upstream_error",
            f"runtime service returned malformed data: {exc}",
            status_code=502,
        ) from exc


def _error_from_envelope(response: httpx.Response, *, default_code: str) -> RuntimeApplicationError:
    try:
        envelope = response.json()
    except ValueError:
        return RuntimeApplicationError(
            default_code,
            f"runtime service HTTP {response.status_code}",
            status_code=response.status_code,
        )
    if isinstance(envelope, dict):
        message = str(envelope.get("message") or f"runtime service HTTP {response.status_code}")
        upstream_code = envelope.get("code")
        upstream_error = envelope.get("error")
        # TASK-021（ADR-A015 §5 兼容矩阵）：老载荷无 error 字段时，有整数码则
        # slug 回落 unknown_error（不读 message 反推）；无码走默认网关码。
        if not isinstance(upstream_error, str):
            upstream_error = (
                "unknown_error" if isinstance(upstream_code, int) else None
            )
        return RuntimeApplicationError(
            default_code,
            message,
            status_code=response.status_code,
            upstream_code=upstream_code if isinstance(upstream_code, int) else None,
            upstream_error=upstream_error,
        )
    return RuntimeApplicationError(
        default_code,
        f"runtime service HTTP {response.status_code}",
        status_code=response.status_code,
    )


async def _iter_sse(response: httpx.Response) -> AsyncIterator[RuntimeStreamEvent]:
    buf = ""
    async for chunk in response.aiter_text():
        buf += chunk
        while "\n\n" in buf:
            block, buf = buf.split("\n\n", 1)
            event = _parse_sse_block(block)
            if event is not None:
                yield event
    if buf.strip():
        event = _parse_sse_block(buf)
        if event is not None:
            yield event


def _parse_sse_block(block: str) -> RuntimeStreamEvent | None:
    name: str | None = None
    data_lines: list[str] = []
    for line in block.splitlines():
        if line.startswith("event:"):
            name = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].lstrip(" "))
        elif line.startswith(":"):
            continue
    if name is None:
        return None
    raw = "\n".join(data_lines)
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        data = {"raw": raw}
    if not isinstance(data, dict):
        data = {"value": data}
    return RuntimeStreamEvent(event=name, data=data)


__all__ = ["HttpRuntimeGateway"]
