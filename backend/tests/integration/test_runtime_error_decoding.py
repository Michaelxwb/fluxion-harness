"""Gateway 错误解码和透传（TASK-021 / ADR-A015 §5）验收测试。

覆盖 B-ERR-01 / E-ERR-02：
- 真实边界：真实 HTTP Gateway → 真实/故障响应边界；
  真实 Gateway HTTP/SSE 解码 → RuntimeApplicationError。
- 故障边界经 MockTransport 构造（repo 既有模式）；传输与解码逻辑全真实。
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient, MockTransport, Request, Response

from fluxion.services.http_runtime_gateway import HttpRuntimeGateway
from fluxion.services.runtime_app import RunRuntimeRequest
from fluxion.services.runtime_contracts import RuntimeApplicationError


def _request() -> RunRuntimeRequest:
    return RunRuntimeRequest(
        tenant_id="tenant-a",
        user_id="user-a",
        runtime_profile_id="assistant",
        session_id="session-a",
        input_message="hello",
        agent_definition_id="assistant",
        request_id="req_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        trace_id="trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        execution_id="exec_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )


def _envelope(code: int, message: str, error: str | None = None) -> dict:
    body: dict = {"code": code, "message": message, "data": None, "request_id": "req_x"}
    if error is not None:
        body["error"] = error
    return body


@pytest.mark.asyncio
async def test_B_ERR_01_legacy_envelope_compatible() -> None:
    """B-ERR-01：旧载荷（无 error 字段）兼容；缺失 slug 按兼容矩阵回落。"""
    seen: list[Request] = []

    def handler(request: Request) -> Response:
        seen.append(request)
        return Response(200, json=_envelope(40_001, "老 message 文案"))

    async with AsyncClient(
        transport=MockTransport(handler), base_url="http://runtime"
    ) as raw:
        gateway = HttpRuntimeGateway(base_url="http://runtime", client=raw)
        with pytest.raises(RuntimeApplicationError) as exc_info:
            await gateway.run(_request())
    error = exc_info.value
    assert error.upstream_code == 40_001
    assert error.upstream_error == "unknown_error"
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_B_ERR_01_malformed_becomes_stable_gateway_error() -> None:
    """B-ERR-01：非 JSON / 缺字段 / 畸形类型变为稳定网关错误（不抛原始解析异常）。"""
    cases = [
        Response(502, text="not json at all"),
        Response(200, json={"message": "no code"}),
        Response(200, json={"code": "not-an-int", "message": "x", "data": None}),
        Response(200, json={"code": 40_001, "message": "x"}),
    ]
    for index, mocked in enumerate(cases):
        def handler(request: Request, response: Response = mocked) -> Response:
            return response

        async with AsyncClient(
            transport=MockTransport(handler), base_url="http://runtime"
        ) as raw:
            gateway = HttpRuntimeGateway(base_url="http://runtime", client=raw)
            with pytest.raises(RuntimeApplicationError) as exc_info:
                await gateway.run(_request())
            assert exc_info.value.status_code in (200, 502), f"case {index}"


@pytest.mark.asyncio
async def test_E_ERR_02_upstream_slug_preserved_no_message_match_no_retry() -> None:
    """E-ERR-02：上游 slug 保留；不按 message 匹配；无自动重试（单次 POST）。"""
    seen: list[Request] = []

    def handler(request: Request) -> Response:
        seen.append(request)
        return Response(
            502, json=_envelope(40_001, "文案 A（换文案不影响解码）", error="model_provider_timeout")
        )

    async with AsyncClient(
        transport=MockTransport(handler), base_url="http://runtime"
    ) as raw:
        gateway = HttpRuntimeGateway(base_url="http://runtime", client=raw)
        with pytest.raises(RuntimeApplicationError) as exc_info:
            await gateway.run(_request())
    error = exc_info.value
    # 本地职责：稳定网关码；上游职责：整数码 + slug 保留。
    assert error.code == "runtime_upstream_error"
    assert error.upstream_code == 40_001
    assert error.upstream_error == "model_provider_timeout"
    assert error.status_code == 502
    # 执行 POST 不自动重放。
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_E_ERR_02_http200_sse_error_event_preserved() -> None:
    """E-ERR-02：HTTP200 内嵌 SSE error 事件透传（slug 不断链）。"""
    body = (
        'event: started\ndata: {"request_id": "req_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}\n\n'
        'event: error\ndata: {"code": 40001, "error": "model_provider_timeout", '
        '"message": "slow", "request_id": "req_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}\n\n'
    )

    def handler(request: Request) -> Response:
        return Response(
            200, content=body.encode(), headers={"content-type": "text/event-stream"}
        )

    async with AsyncClient(
        transport=MockTransport(handler), base_url="http://runtime"
    ) as raw:
        gateway = HttpRuntimeGateway(base_url="http://runtime", client=raw)
        events = [event async for event in gateway.stream(_request())]
    kinds = [event.event for event in events]
    assert kinds[0] == "started"
    error_event = next(event for event in events if event.event == "error")
    assert error_event.data["error"] == "model_provider_timeout"
    assert error_event.data["code"] == 40001
