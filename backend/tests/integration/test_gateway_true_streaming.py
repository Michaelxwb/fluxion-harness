"""FEAT-01 网关真流式：增量转发验收（P0）。

真实边界：真实 httpx（含自定义增量 Transport，块间延迟下发）。
"""

from __future__ import annotations

import asyncio
import time

import pytest
from httpx import AsyncBaseTransport, AsyncClient, Request, Response

from fluxion.services.http_runtime_gateway import HttpRuntimeGateway

from tests.integration.test_http_runtime_gateway import _run_request


class _ChunkedTransport(AsyncBaseTransport):
    """按 (chunk, delay) 增量下发 SSE 的 Transport，模拟慢上游。"""

    def __init__(self, chunks: list[tuple[bytes, float]]) -> None:
        self._chunks = chunks

    async def handle_async_request(self, request: Request) -> Response:
        chunks = self._chunks

        async def _aiter():  # type: ignore[no-untyped-def]
            for payload, delay in chunks:
                await asyncio.sleep(delay)
                yield payload

        return Response(
            200, content=_aiter(), headers={"content-type": "text/event-stream"}
        )


def _blocks() -> list[tuple[bytes, float]]:
    return [
        (b'event: started\ndata: {"request_id": "req-s01"}\n\n', 0),
        (b'event: token\ndata: {"content": "hi"}\n\n', 0.5),
    ]


@pytest.mark.asyncio
async def test_s01_first_event_arrives_before_full_body() -> None:
    """S-01：首事件到达早于全收（真增量），两事件都收到。"""
    transport = _ChunkedTransport(_blocks())
    async with AsyncClient(transport=transport, base_url="http://runtime") as raw:
        gateway = HttpRuntimeGateway(base_url="http://runtime", client=raw)
        started = time.perf_counter()
        seen: list[tuple[str, float]] = []
        async for event in gateway.stream(_run_request()):
            seen.append((event.event, time.perf_counter() - started))
    assert [name for name, _ in seen] == ["started", "token"]
    assert seen[0][1] < 0.4, f"首事件被缓冲：{seen[0][1]:.2f}s（伪流式）"


@pytest.mark.asyncio
async def test_b01_borrowed_client_survives_stream() -> None:
    """B-01：外借 client 在 stream 结束后仍可用（只关 response）。"""
    transport = _ChunkedTransport(_blocks())
    async with AsyncClient(transport=transport, base_url="http://runtime") as raw:
        gateway = HttpRuntimeGateway(base_url="http://runtime", client=raw)
        events = [event async for event in gateway.stream(_run_request())]
        assert [event.event for event in events] == ["started", "token"]
        assert not raw.is_closed, "外借 client 不得被网关关闭"
