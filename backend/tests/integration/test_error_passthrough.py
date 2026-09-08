"""FEAT-03 错误透传不断链（P1）。

真实边界：真实 httpx MockTransport（上游 40001 信封 / 流中 error 帧）。
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient, MockTransport, Request, Response

from fluxion.services.http_runtime_gateway import HttpRuntimeGateway
from fluxion.services.runtime_contracts import RuntimeApplicationError

from tests.integration.test_http_runtime_gateway import _run_request


def _gateway(handler) -> tuple[HttpRuntimeGateway, AsyncClient]:  # type: ignore[no-untyped-def]
    transport = MockTransport(handler)
    raw = AsyncClient(transport=transport, base_url="http://runtime")
    return HttpRuntimeGateway(base_url="http://runtime", client=raw), raw


@pytest.mark.asyncio
async def test_s03_upstream_code_survives_gateway() -> None:
    """S-03：上游 40001+slug 经网关后可见（upstream_code/error 回填）。"""
    def _handler(request: Request) -> Response:
        return Response(
            400,
            json={
                "code": 40001,
                "error": "agent_not_found",
                "message": "agent_not_found: assistant",
                "request_id": "req-up",
            },
        )

    gateway, raw = _gateway(_handler)
    try:
        with pytest.raises(RuntimeApplicationError) as exc_info:
            await gateway.run(_run_request())
    finally:
        await raw.aclose()
    error = exc_info.value
    assert error.upstream_code == 40001
    assert error.upstream_error == "agent_not_found"
    assert "agent_not_found" in str(error)


@pytest.mark.asyncio
async def test_e03_channel_keeps_upstream_slug() -> None:
    """E-03：流中途远端错误经 channel 透传且保留 slug，不变裸 INTERNAL_ERROR。"""
    import json

    from fluxion.api.channel import ChatAccessMessagePayload, _access_events

    class _StubService:
        async def stream_chat_access(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeApplicationError(
                "runtime_upstream_timeout",
                "runtime service timeout: boom",
                status_code=503,
                upstream_code=40001,
                upstream_error="agent_not_found",
            )
            yield  # pragma: no cover - 使其成为异步生成器

    frames = [
        frame
        async for frame in _access_events(
            _StubService(),  # type: ignore[arg-type]
            ChatAccessMessagePayload(
                conversation_id="c1", message_id="m1", content="hi"
            ),
            token="tok",
        )
    ]
    assert len(frames) == 1 and frames[0].startswith("event: error")
    data = json.loads(frames[0].split("data:", 1)[1])
    # TASK-022（S-ERR-02）：slug 优先上游原始值，不断链。
    assert data["error"] == "agent_not_found"
    assert "timeout" in data["message"]


@pytest.mark.asyncio
async def test_e04_local_runtime_failure_is_user_facing() -> None:
    """E-04：本地执行失败（无上游，如存量 profile 漂移的 ValidationError）
    不回传内部原文，给中文兜底文案 + request_id。"""
    import json

    from fluxion.api.channel import ChatAccessMessagePayload, _access_events

    class _StubService:
        async def stream_chat_access(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeApplicationError(
                "snapshot_build_failed",
                "2 validation errors for RuntimeProfile\nrequest_timeout_ms\n  Extra inputs are not permitted",
                status_code=400,
            )
            yield  # pragma: no cover - 使其成为异步生成器

    frames = [
        frame
        async for frame in _access_events(
            _StubService(),  # type: ignore[arg-type]
            ChatAccessMessagePayload(
                conversation_id="c1", message_id="m1", content="hi"
            ),
            token="tok",
        )
    ]
    assert len(frames) == 1 and frames[0].startswith("event: error")
    data = json.loads(frames[0].split("data:", 1)[1])
    assert "request_timeout_ms" not in data["message"]
    assert "ValidationError" not in data["message"]
    assert "服务暂时不可用" in data["message"]
    assert "request_id=m1" in data["message"]


@pytest.mark.asyncio
async def test_e04_unexpected_error_is_user_facing() -> None:
    """E-04：未知异常同样走中文兜底，不回传 internal error 英文原文。"""
    import json

    from fluxion.api.channel import ChatAccessMessagePayload, _access_events

    class _StubService:
        async def stream_chat_access(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise ValueError("boom")
            yield  # pragma: no cover - 使其成为异步生成器

    frames = [
        frame
        async for frame in _access_events(
            _StubService(),  # type: ignore[arg-type]
            ChatAccessMessagePayload(
                conversation_id="c1", message_id="m1", content="hi"
            ),
            token="tok",
        )
    ]
    assert len(frames) == 1 and frames[0].startswith("event: error")
    data = json.loads(frames[0].split("data:", 1)[1])
    assert data["message"] != "internal error"
    assert "服务暂时不可用" in data["message"]
