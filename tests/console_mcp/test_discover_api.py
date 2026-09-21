"""[S-01][S-03][E-01][E-04][E-05][B-02] discover-tools 与工具目录 API。

真实边界：uvicorn 真实探针 MCP Server（协议/失败模式环境变量控制）+ 真实 PostgreSQL；不 mock MCP 协议。
"""

from __future__ import annotations

import httpx
import pytest

from console_mcp.conftest import create_mcp, tenant_headers


async def _register(
    client: httpx.AsyncClient, env: dict[str, object], probe_url: str
) -> str:
    created = await create_mcp(client, env, endpoint=probe_url)
    assert created.status_code == 200, created.text
    return str(created.json()["data"]["mcp_id"])


async def test_s01_refresh_updates_catalog_revision_and_hash(
    client: httpx.AsyncClient, env: dict[str, object], probe_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """[S-01] 刷新工具目录：快照持久化，内容变化时 hash/revision 更新；未变化时 changed:false。"""
    mcp_id = await _register(client, env, probe_url)
    first = await client.post(
        f"/api/v1/mcp-servers/{mcp_id}/discover-tools", headers=tenant_headers(env)
    )
    assert first.status_code == 200, first.text
    data = first.json()["data"]
    assert data["connection_status"] == "AVAILABLE"
    assert data["tool_catalog_revision"] == 1
    assert data["tool_count"] == 2
    assert data["tool_catalog_hash"]
    assert data["changed"] is True

    unchanged = await client.post(
        f"/api/v1/mcp-servers/{mcp_id}/discover-tools", headers=tenant_headers(env)
    )
    assert unchanged.status_code == 200
    unchanged_data = unchanged.json()["data"]
    assert unchanged_data["changed"] is False
    assert unchanged_data["tool_catalog_revision"] == 1  # 目录未变 revision 不变
    assert unchanged_data["tool_catalog_hash"] == data["tool_catalog_hash"]

    # 目录内容变化：工具数 2 → 3，hash 与 revision 必须变化
    monkeypatch.setenv("MCP_PROBE_TOOLS", "3")
    grown = await client.post(
        f"/api/v1/mcp-servers/{mcp_id}/discover-tools", headers=tenant_headers(env)
    )
    assert grown.status_code == 200
    grown_data = grown.json()["data"]
    assert grown_data["changed"] is True
    assert grown_data["tool_catalog_revision"] == 2
    assert grown_data["tool_count"] == 3
    assert grown_data["tool_catalog_hash"] != data["tool_catalog_hash"]

    # 目录缩回：再次变化
    monkeypatch.setenv("MCP_PROBE_TOOLS", "2")
    shrunk = await client.post(
        f"/api/v1/mcp-servers/{mcp_id}/discover-tools", headers=tenant_headers(env)
    )
    assert shrunk.json()["data"]["tool_catalog_revision"] == 3

    detail = (await client.get(f"/api/v1/mcp-servers/{mcp_id}", headers=tenant_headers(env))).json()["data"]
    assert detail["last_discovered_at"] is not None
    tools = (
        await client.get(f"/api/v1/mcp-servers/{mcp_id}/tools", headers=tenant_headers(env))
    ).json()["data"]
    assert tools["total"] == 2
    assert {item["name"] for item in tools["items"]} == {"probe_tool_0", "probe_tool_1"}


async def test_s03_register_test_discover_flow(
    client: httpx.AsyncClient, env: dict[str, object], probe_url: str
) -> None:
    """[S-03] 注册→连接测试→刷新目录：AVAILABLE，revision/hash 更新。"""
    mcp_id = await _register(client, env, probe_url)
    tested = await client.post(f"/api/v1/mcp-servers/{mcp_id}/test", headers=tenant_headers(env))
    assert tested.status_code == 200
    assert tested.json()["data"]["connection_status"] == "AVAILABLE"

    discovered = await client.post(
        f"/api/v1/mcp-servers/{mcp_id}/discover-tools", headers=tenant_headers(env)
    )
    assert discovered.status_code == 200
    data = discovered.json()["data"]
    assert data["connection_status"] == "AVAILABLE"
    assert data["tool_catalog_revision"] == 1
    assert data["tool_catalog_hash"]


async def test_e01_tools_list_failure_discovery_failed_preserves_catalog(
    client: httpx.AsyncClient,
    env: dict[str, object],
    probe_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[E-01] tools/list 协议失败：MCP_DISCOVERY_FAILED；保留上一成功 Catalog。"""
    mcp_id = await _register(client, env, probe_url)
    ok_response = await client.post(
        f"/api/v1/mcp-servers/{mcp_id}/discover-tools", headers=tenant_headers(env)
    )
    assert ok_response.status_code == 200
    good_hash = ok_response.json()["data"]["tool_catalog_hash"]

    monkeypatch.setenv("MCP_PROBE_FAIL_LIST", "1")
    try:
        failed = await client.post(
            f"/api/v1/mcp-servers/{mcp_id}/discover-tools", headers=tenant_headers(env)
        )
        assert failed.status_code == 502
        assert failed.json()["code"] == "MCP_DISCOVERY_FAILED"
    finally:
        monkeypatch.delenv("MCP_PROBE_FAIL_LIST")

    detail = (await client.get(f"/api/v1/mcp-servers/{mcp_id}", headers=tenant_headers(env))).json()["data"]
    assert detail["connection_status"] == "DISCOVERY_FAILED"
    assert detail["last_discovery_error"]
    assert detail["tool_catalog_hash"] == good_hash  # 上一成功 Catalog 保留
    assert detail["tool_catalog_revision"] == 1

    tools = (
        await client.get(f"/api/v1/mcp-servers/{mcp_id}/tools", headers=tenant_headers(env))
    ).json()["data"]
    assert tools["total"] == 2  # 目录快照仍可读


async def test_e04_connection_failure_marks_connection_failed_and_preserves_catalog(
    client: httpx.AsyncClient, env: dict[str, object], probe_url: str
) -> None:
    """[E-04] 发现时连接失败：MCP_DISCOVERY_FAILED；保留上一成功 Catalog。"""
    mcp_id = await _register(client, env, probe_url)
    ok_response = await client.post(
        f"/api/v1/mcp-servers/{mcp_id}/discover-tools", headers=tenant_headers(env)
    )
    assert ok_response.status_code == 200
    good_hash = ok_response.json()["data"]["tool_catalog_hash"]

    # endpoint 改为不可达地址（编辑语义，不触发 discovery）
    edited = await client.put(
        f"/api/v1/mcp-servers/{mcp_id}",
        json={"endpoint": "http://127.0.0.1:9/mcp"},
        headers=tenant_headers(env),
    )
    assert edited.status_code == 200

    failed = await client.post(
        f"/api/v1/mcp-servers/{mcp_id}/discover-tools", headers=tenant_headers(env)
    )
    assert failed.status_code == 502
    assert failed.json()["code"] == "MCP_DISCOVERY_FAILED"

    detail = (await client.get(f"/api/v1/mcp-servers/{mcp_id}", headers=tenant_headers(env))).json()["data"]
    assert detail["connection_status"] == "DISCOVERY_FAILED"
    assert detail["last_discovery_error"]
    assert detail["tool_catalog_hash"] == good_hash
    assert detail["tool_catalog_revision"] == 1
    tools = (
        await client.get(f"/api/v1/mcp-servers/{mcp_id}/tools", headers=tenant_headers(env))
    ).json()["data"]
    assert tools["total"] == 2


async def test_e05_tool_limit_preserves_catalog(
    client: httpx.AsyncClient,
    env: dict[str, object],
    probe_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[E-05] 探针真实返回 201 个工具（默认上限 200）：发现失败并保留上一成功 Catalog。"""
    mcp_id = await _register(client, env, probe_url)
    ok_response = await client.post(
        f"/api/v1/mcp-servers/{mcp_id}/discover-tools", headers=tenant_headers(env)
    )
    assert ok_response.status_code == 200
    good_hash = ok_response.json()["data"]["tool_catalog_hash"]
    assert ok_response.json()["data"]["tool_count"] == 2

    monkeypatch.setenv("MCP_PROBE_TOOLS", "201")
    over_limit = await client.post(
        f"/api/v1/mcp-servers/{mcp_id}/discover-tools", headers=tenant_headers(env)
    )
    assert over_limit.status_code == 502
    assert over_limit.json()["code"] == "MCP_DISCOVERY_FAILED"

    detail = (await client.get(f"/api/v1/mcp-servers/{mcp_id}", headers=tenant_headers(env))).json()["data"]
    assert detail["tool_catalog_hash"] == good_hash
    assert detail["tool_catalog_revision"] == 1
    assert detail["connection_status"] == "DISCOVERY_FAILED"
    assert "limit" in (detail["last_discovery_error"] or "")
    tools = (
        await client.get(f"/api/v1/mcp-servers/{mcp_id}/tools", headers=tenant_headers(env))
    ).json()["data"]
    assert tools["total"] == 2


async def test_b02_discover_is_only_catalog_writer(
    client: httpx.AsyncClient, env: dict[str, object], probe_url: str
) -> None:
    """[B-02] discover-tools 是唯一目录入口：CRUD/连接测试均不修改 tool_catalog_json。"""
    mcp_id = await _register(client, env, probe_url)

    tested = await client.post(f"/api/v1/mcp-servers/{mcp_id}/test", headers=tenant_headers(env))
    assert tested.status_code == 200
    after_test = (
        await client.get(f"/api/v1/mcp-servers/{mcp_id}", headers=tenant_headers(env))
    ).json()["data"]
    assert after_test["tool_catalog_revision"] == 0
    assert after_test["tool_catalog_hash"] is None
    assert after_test["tool_count"] == 0

    edited = await client.put(
        f"/api/v1/mcp-servers/{mcp_id}",
        json={"name": "Still Empty Catalog"},
        headers=tenant_headers(env),
    )
    assert edited.status_code == 200
    after_edit = (
        await client.get(f"/api/v1/mcp-servers/{mcp_id}", headers=tenant_headers(env))
    ).json()["data"]
    assert after_edit["tool_catalog_revision"] == 0

    tools = (
        await client.get(f"/api/v1/mcp-servers/{mcp_id}/tools", headers=tenant_headers(env))
    ).json()["data"]
    assert tools["total"] == 0  # 未发现过 → 目录为空

    discovered = await client.post(
        f"/api/v1/mcp-servers/{mcp_id}/discover-tools", headers=tenant_headers(env)
    )
    assert discovered.status_code == 200
    after_discover = (
        await client.get(f"/api/v1/mcp-servers/{mcp_id}", headers=tenant_headers(env))
    ).json()["data"]
    assert after_discover["tool_catalog_revision"] == 1


async def test_tool_detail_from_catalog_snapshot(
    client: httpx.AsyncClient, env: dict[str, object], probe_url: str
) -> None:
    """[API-09] 工具详情读快照：input_schema/effect/revision/hash；不存在 404。"""
    mcp_id = await _register(client, env, probe_url)
    await client.post(f"/api/v1/mcp-servers/{mcp_id}/discover-tools", headers=tenant_headers(env))

    detail = await client.get(
        f"/api/v1/mcp-servers/{mcp_id}/tools/probe_tool_0", headers=tenant_headers(env)
    )
    assert detail.status_code == 200
    body = detail.json()["data"]
    assert body["name"] == "probe_tool_0"
    assert body["input_schema"]["type"] == "object"
    assert body["effect"] == "WRITE"
    assert body["catalog_revision"] == 1
    assert body["catalog_hash"]

    missing = await client.get(
        f"/api/v1/mcp-servers/{mcp_id}/tools/no_such_tool", headers=tenant_headers(env)
    )
    assert missing.status_code == 404
    assert missing.json()["code"] == "COMMON_NOT_FOUND"


class _RecordingCache:
    def __init__(self) -> None:
        self.calls: list[tuple[object, int]] = []

    async def invalidate(self, *, server_id: object, revision: int) -> None:
        self.calls.append((server_id, revision))


async def test_discover_invalidates_previous_revision_cache(
    client: httpx.AsyncClient,
    env: dict[str, object],
    probe_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[RULE-mcp-001] 目录变化时失效旧 revision 的 Runtime 缓存；未变化/首次不失效。"""
    import uuid as uuid_module

    from muad_console_platform.api.deps import get_mcp_catalog_cache
    from muad_console_platform.main import app

    recorder = _RecordingCache()
    app.dependency_overrides[get_mcp_catalog_cache] = lambda: recorder
    try:
        mcp_id = await _register(client, env, probe_url)
        first = await client.post(
            f"/api/v1/mcp-servers/{mcp_id}/discover-tools", headers=tenant_headers(env)
        )
        assert first.status_code == 200
        assert recorder.calls == []  # 首次发现无旧 revision 可失效

        monkeypatch.setenv("MCP_PROBE_TOOLS", "3")
        second = await client.post(
            f"/api/v1/mcp-servers/{mcp_id}/discover-tools", headers=tenant_headers(env)
        )
        assert second.json()["data"]["tool_catalog_revision"] == 2
        assert recorder.calls == [(uuid_module.UUID(mcp_id), 1)]

        unchanged = await client.post(
            f"/api/v1/mcp-servers/{mcp_id}/discover-tools", headers=tenant_headers(env)
        )
        assert unchanged.json()["data"]["changed"] is False
        assert len(recorder.calls) == 1

        removed = await client.delete(
            f"/api/v1/mcp-servers/{mcp_id}", headers=tenant_headers(env)
        )
        assert removed.status_code == 200
        assert recorder.calls[-1] == (uuid_module.UUID(mcp_id), 2)
    finally:
        app.dependency_overrides.pop(get_mcp_catalog_cache, None)
        monkeypatch.delenv("MCP_PROBE_TOOLS")
