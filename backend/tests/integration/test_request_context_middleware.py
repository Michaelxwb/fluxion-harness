"""RequestContextMiddleware 纯 ASGI 透传（asgi-passthrough TASK-001）验收测试。

真实边界：真实 Starlette app＋真实中间件＋httpx ASGITransport。
S-MW-02 为核心 RED：流式首字节必须在 body 收齐前到达（BaseHTTPMiddleware
缓冲下首字节晚到 → 超时失败）。
"""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from fluxion.api.middleware import RequestContextMiddleware


def _build_app() -> Starlette:
    async def _json(_request):  # type: ignore[no-untyped-def]
        return JSONResponse(
            {"ok": True}, headers={"X-Biz-Code": "0", "X-Publish-ID": "pub-1"}
        )

    async def _boom(_request):  # type: ignore[no-untyped-def]
        raise RuntimeError("downstream boom")

    async def _stream(_request):  # type: ignore[no-untyped-def]
        async def _gen():  # type: ignore[no-untyped-def]
            yield b"event: started\ndata: {}\n\n"
            # handler 自带超时自放行：任何实现下 ≤5s 必结束，测试永不死锁。
            try:
                await asyncio.wait_for(_gate.release.wait(), timeout=5)
            except TimeoutError:
                pass
            yield b"event: completed\ndata: {}\n\n"

        return StreamingResponse(_gen(), media_type="text/event-stream")

    app = Starlette(routes=[Route("/json", _json), Route("/boom", _boom), Route("/stream", _stream)])
    app.add_middleware(RequestContextMiddleware, require_identity=False)
    return app


class _gate:
    release: asyncio.Event = asyncio.Event()


def _authed() -> dict[str, str]:
    return {"X-Tenant-ID": "tenant-a", "X-Actor-ID": "admin-a"}


@pytest.mark.asyncio
async def test_S_MW_01_identity_headers_and_echo() -> None:
    """S-MW-01：身份头透传、请求 ID 回显、业务码头透传。"""
    app = _build_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as client:
        resp = await client.get(
            "/json",
            headers={**_authed(), "X-Request-ID": "req_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
        )
        assert resp.status_code == 200
        assert resp.headers["X-Request-ID"] == "req_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        assert resp.headers["X-Trace-ID"]
        assert resp.json() == {"ok": True}


@pytest.mark.asyncio
async def test_S_MW_01_missing_identity_rejected_in_strict_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S-MW-01：非 dev 非宽容模式缺身份头 → 401（与改前一致）。"""
    from starlette.applications import Starlette as _Starlette
    from starlette.routing import Route as _Route

    monkeypatch.delenv("FLUXION_DEV_MODE", raising=False)

    async def _json(_request):  # type: ignore[no-untyped-def]
        return JSONResponse({"ok": True})

    strict = _Starlette(routes=[_Route("/json", _json)])
    strict.add_middleware(RequestContextMiddleware)
    async with AsyncClient(
        transport=ASGITransport(app=strict), base_url="http://t"
    ) as client:
        resp = await client.get("/json")
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_S_MW_02_first_byte_before_body_complete() -> None:
    """S-MW-02：流式首字节在 body 收齐前到达（真增量；缓冲实现下超时 RED）。

    必须走真实 TCP loopback（ASGITransport 自己也缓冲，测不出增量）。
    整体计时（含建连）：缓冲实现 handler 5s 自放行才有字节 → 3s 预算超时失败；
    透传实现首字节即时到达 → 通过。
    """
    from httpx import AsyncClient as _AsyncClient

    from tests.e2e.network_runtime_helpers import TcpAppServer

    _gate.release = asyncio.Event()
    app = _build_app()
    async with TcpAppServer(app) as server, _AsyncClient() as client, client.stream(
        "GET", f"{server.base_url}/stream", headers=_authed()
    ) as resp:
        assert resp.status_code == 200

        async def _read_first() -> str:
            async for chunk in resp.aiter_text():
                return chunk
            raise AssertionError("stream ended without bytes")

        try:
            first = await asyncio.wait_for(_read_first(), timeout=3)
        finally:
            _gate.release.set()
    assert "started" in first


@pytest.mark.asyncio
async def test_E_MW_01_downstream_error_is_500_and_context_reset() -> None:
    """E-MW-01：下游抛错 → 500；上下文复位（后续请求 ID 独立、无污染）。"""
    app = _build_app()
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        resp = await client.get("/boom", headers=_authed())
        assert resp.status_code == 500
        first_id = resp.headers.get("X-Request-ID")
        ok_resp = await client.get("/json", headers=_authed())
        assert ok_resp.status_code == 200
        assert ok_resp.headers.get("X-Request-ID")
        assert ok_resp.headers.get("X-Request-ID") != first_id
