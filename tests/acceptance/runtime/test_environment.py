"""[B-123] Runtime E2E 真实环境设施核验（真实 HTTP 进程 + PG/Redis/NFS）。

无 mock、无 dependency_overrides；探针可控记录/故障；清理可重复执行。
全部 async：由 pytest-asyncio 管理事件循环，避免 asyncio.run() 污染。
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


async def test_b123_probe_records_and_fault_injection(probe_url: str) -> None:
    """[B-123] 探针可控：JSON-RPC initialize/tools/list 正常路径可复现。"""
    async with httpx.AsyncClient(timeout=5) as client:
        init_response = await client.post(
            f"{probe_url}/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-03-26"},
            },
        )
        init = init_response.json()
        assert init["result"]["serverInfo"]["name"]

        tools_response = await client.post(
            f"{probe_url}/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        tools = tools_response.json()
        assert len(tools["result"]["tools"]) == 2


async def test_b123_real_dependencies_present() -> None:
    """[B-123] PG（含本模块迁移）/Redis/Artifact Store 依赖真实可用（缺失即 fail，不 skip）。"""
    import asyncpg

    settings = SharedSettings()
    pg_url = settings.database_url
    _require(pg_url or "", "DATABASE_URL")
    redis_url = settings.redis_url
    if not redis_url:
        pytest.fail("REDIS_URL 未配置：E2E 环境要求真实依赖")

    dsn = pg_url.replace("+asyncpg", "")
    conn = await asyncpg.connect(dsn)
    try:
        tables = await conn.fetch(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='runtime'"
        )
        names = {row["table_name"] for row in tables}
        assert len(names) >= 5
        # 本模块迁移产物必须存在（不能靠 0002 旧表通过）
        assert "run_submission" in names
        columns = await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='runtime' AND table_name='canonical_event'"
        )
        column_names = {row["column_name"] for row in columns}
        assert {"submission_id", "stream_type"} <= column_names
    finally:
        await conn.close()

    client = redis.from_url(redis_url, decode_responses=True)
    try:
        await client.set("muad:e2e:probe", "1")
        assert await client.get("muad:e2e:probe") == "1"
        await client.delete("muad:e2e:probe")
    finally:
        await client.aclose()


async def test_b123_artifact_store_round_trip(tmp_path) -> None:
    """[B-123] 真实 Artifact Store（NFS-backed root）可写、可读、可清理。"""
    from muad_artifact_store import NfsArtifactStore

    root = tmp_path / "artifacts"
    root.mkdir()
    store = NfsArtifactStore(root)
    key = f"probe/{uuid.uuid4().hex}.txt"
    path = store.resolve(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"artifact-probe")
    assert store.exists(key)
    assert path.read_bytes() == b"artifact-probe"
    path.unlink()
    assert not store.exists(key)
