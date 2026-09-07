"""Runtime 执行面 HTTP Gateway（FEAT-01）。

经 HTTP 调用独立 Runtime Service，替代 production_bundle 中本地
RuntimeApplicationService 创建。复用 ``services/channel_app.py`` 的
RuntimeGateway Protocol（run/stream 签名与返回类型一致）。

Wire 格式复用 ``api/runtime.py`` 真实路由与 §3.3.1 约定，不新增路由：
- ``POST /internal/v1/runtime-profiles/{runtime_profile_id}/runs``
- ``POST /internal/v1/runtime-profiles/{runtime_profile_id}/runs:stream``

超时/失败策略（规则 18）：
- connect/pool 3s、write 10s；read 按执行预算 + 有界余量（默认 120s，
  构造时可覆盖）；流式另设 idle/整体 read 超时。
- 执行 POST 默认不重试；连接失败映射类型化 RuntimeApplicationError/503；
  已发 token 后不切换路由重新执行；生产远程失败不降级本地执行。
"""

from __future__ import annotations

import json
import traceback
from collections.abc import AsyncIterator
from urllib.parse import quote

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

    async def run(self, request: RunRuntimeRequest) -> RunRuntimeResult:
        url = f"{self._base_url}/internal/v1/runtime-profiles/{quote(request.runtime_profile_id, safe='')}/runs"
        try:
            response = await self._client.post(
                url,
                json=_request_payload(request),
                headers=_request_headers(request),
                timeout=httpx.Timeout(
                    connect=_DEFAULT_CONNECT_TIMEOUT,
                    read=self._read_timeout,
                    write=_DEFAULT_WRITE_TIMEOUT,
                    pool=_DEFAULT_POOL_TIMEOUT,
                ),
            )
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

    async def stream(self, request: RunRuntimeRequest) -> AsyncIterator[RuntimeStreamEvent]:
        url = (
            f"{self._base_url}/internal/v1/runtime-profiles/"
            f"{quote(request.runtime_profile_id, safe='')}/runs:stream"
        )
        # client.stream() 构造期不做 IO（建连在 __aenter__），异常全在 async with 内处理。
        stream_cm = self._client.stream(
            "POST",
            url,
            json=_request_payload(request),
            headers=_request_headers(request),
            timeout=httpx.Timeout(
                connect=_DEFAULT_CONNECT_TIMEOUT,
                read=self._stream_read_timeout,
                write=_DEFAULT_WRITE_TIMEOUT,
                pool=_DEFAULT_POOL_TIMEOUT,
            ),
        )
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


def _request_payload(request: RunRuntimeRequest) -> dict[str, object]:
    payload: dict[str, object] = {
        "tenant_id": request.tenant_id,
        "user_id": request.user_id,
        "session_id": request.session_id,
        "input": request.input_message,
        "runtime_profile_version_selector": request.runtime_profile_version_selector,
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
        return RuntimeApplicationError(
            default_code,
            message,
            status_code=response.status_code,
            upstream_code=upstream_code if isinstance(upstream_code, int) else None,
            upstream_error=str(upstream_error) if isinstance(upstream_error, str) else None,
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
