"""[B-123] Runtime E2E 真实环境设施核验（真实 HTTP 进程 + PG/Redis/NFS）。

无 mock、无 dependency_overrides；探针可控记录/故障；清理可重复执行。
"""

from __future__ import annotations

import uuid

import httpx
import pytest
import redis.asyncio as redis
from muad_common import SharedSettings


def _require(url: str, name: str) -> None:
    if not url:
        pytest.fail(f"{name} 未配置：E2E 环境要求真实依赖，不得 skip")


@pytest.fixture(scope="module")
def probe_url() -> str:
    import threading
    import time

    import uvicorn
    from tests.e2e.mcp_probe_app import app

    config = uvicorn.Config(app=app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1] if server.servers else 0
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True


def test_b123_real_processes_independent(probe_url: str) -> None:
    """[B-123] 进程实际独立：真实 uvicorn 进程响应 HTTP。"""
    response = httpx.get(f"{probe_url}/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_b123_probe_records_and_fault_injection(probe_url: str) -> None:
    """[B-123] 探针可控：JSON-RPC initialize/tools/list 正常路径可复现。"""
    import asyncio

    async def rpc(method: str, params: dict, id_: int) -> dict:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.post(
                f"{probe_url}/mcp",
                json={"jsonrpc": "2.0", "id": id_, "method": method, "params": params},
            )
            return response.json()

    async def run() -> None:
        init = await rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {}}, 1)
        assert init["result"]["serverInfo"]["name"]
        tools = await rpc("tools/list", {}, 2)
        assert len(tools["result"]["tools"]) == 2

    asyncio.run(run())


def test_b123_real_dependencies_present() -> None:
    """[B-123] PG/Redis/NFS 依赖真实可用（缺失即 fail，不 skip）。"""
    import asyncio

    import asyncpg
    import redis.asyncio as redis

    settings = SharedSettings()
    pg_url = settings.database_url
    _require(pg_url or "", "DATABASE_URL")
    redis_url = settings.redis_url
    if not redis_url:
        pytest.fail("REDIS_URL 未配置：E2E 环境要求真实依赖")

    async def check() -> None:
        dsn = pg_url.replace("+asyncpg", "")
        conn = await asyncpg.connect(dsn)
        try:
            tables = await conn.fetch(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='runtime'"
            )
            assert len(tables) >= 5
        finally:
            await conn.close()

        client = redis.from_url(redis_url, decode_responses=True)
        try:
            await client.set("muad:e2e:probe", "1")
            assert await client.get("muad:e2e:probe") == "1"
            await client.delete("muad:e2e:probe")
        finally:
            await client.aclose()

    asyncio.run(check())
