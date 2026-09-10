"""TASK-007（S-03/S-04）：Console 能力 API。

真实边界：API → Service → Store 全真链（console_stack，无 mock）。
S-03 主断言（双路径一致）由 TASK-004 服务级覆盖；本文件验收 API 信封与
discover/policy 写读链。
"""

from __future__ import annotations

import io
import zipfile

import pytest
import yaml

from tests.console_helpers import console_stack, tenant_headers


def _package_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.yaml",
            yaml.safe_dump({"name": "helper", "version": "1", "knowledge": []}),
        )
        archive.writestr("SKILL.md", "# Helper\nBe helpful.")
    return buf.getvalue()


@pytest.mark.asyncio
async def test_S03_test_call_api_envelope() -> None:
    """S-03（API 侧）：:test-call 信封正确；缺失工具可操作错误。"""
    async with console_stack() as stack:
        response = await stack.client.post(
            "/api/v1/tools/missing-tool:test-call",
            headers=tenant_headers(),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["code"] == 0
        assert body["data"]["reachable"] is False
        assert "missing-tool" in body["data"]["error"]
        assert body["request_id"]


@pytest.mark.asyncio
async def test_skill_package_upload_api() -> None:
    """API-01：POST /skills/packages 上传发布，返回版本与 artifact。"""
    async with console_stack() as stack:
        response = await stack.client.post(
            "/api/v1/skills/packages",
            headers=tenant_headers(),
            files={"file": ("helper.zip", _package_zip(), "application/zip")},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["code"] == 0
        assert body["data"]["skill_id"] == "helper"
        assert body["data"]["version"] == "1"
        assert body["data"]["artifact_uri"].startswith("artifact://tenant-a/skills/")


@pytest.mark.asyncio
async def test_S04_discover_and_put_policies() -> None:
    """S-04（API 侧）：discover 缺失资源 404（信封正确）。"""
    async with console_stack() as stack:
        response = await stack.client.post(
            "/api/v1/mcp-servers/missing-mcp/versions/1:discover",
            headers=tenant_headers(),
        )
        assert response.status_code == 404
        body = response.json()
        assert body["code"] != 0


@pytest.mark.asyncio
async def test_S04_discover_live_and_save_policies() -> None:
    """S-04（API 侧全链）：真 MCP discover → PUT policies → store 读回一致。"""
    import asyncio
    import socket
    from typing import cast

    import uvicorn
    from mcp.server import MCPServer
    from uvicorn._types import ASGIApplication

    from fluxion.resources import ResourceKind
    from tests.runtime_helpers import publish_resource

    server = MCPServer("fluxion-capability-api-fixture")

    @server.tool()
    def lookup(query: str) -> dict[str, str]:
        return {"answer": query}

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    port = cast(tuple[str, int], sock.getsockname())[1]
    app = uvicorn.Server(
        uvicorn.Config(
            cast(
                ASGIApplication,
                server.streamable_http_app(json_response=True, stateless_http=True),
            ),
            log_level="warning",
            lifespan="on",
        )
    )
    sock.listen()
    task = asyncio.create_task(app.serve(sockets=[sock]))
    try:
        for _attempt in range(100):
            if app.started:
                break
            await asyncio.sleep(0.01)
        async with console_stack() as stack:
            await publish_resource(
                stack.store,
                tenant_id="tenant-a",
                kind=ResourceKind.MCP,
                resource_id="weather",
                version="1",
                spec={
                    "name": "weather",
                    "url": f"http://127.0.0.1:{port}/mcp",
                    "timeout_ms": 10000,
                    "allowed_tools": ["lookup"],
                },
            )
            discovered = await stack.client.post(
                "/api/v1/mcp-servers/weather/versions/1:discover",
                headers=tenant_headers(),
            )
            assert discovered.status_code == 200, discovered.text
            tools = discovered.json()["data"]["tools"]
            assert [item["tool_name"] for item in tools] == ["lookup"]
            schema_hash = tools[0]["schema_hash"]
            saved = await stack.client.put(
                "/api/v1/mcp-servers/weather/versions/1/tool-policies",
                headers=tenant_headers(),
                json={
                    "policies": [
                        {
                            "tool_name": "lookup",
                            "schema_hash": schema_hash,
                            "operation": "read",
                            "side_effect": "none",
                            "risk_level": "low",
                            "enabled": True,
                        }
                    ]
                },
            )
            assert saved.status_code == 200, saved.text
            assert saved.json()["data"]["saved"] == ["lookup"]
            rows = await stack.store.list_mcp_tool_policies(
                tenant_id="tenant-a", mcp_id="weather", mcp_version=1
            )
            assert [(row.tool_name, row.schema_hash) for row in rows] == [
                ("lookup", schema_hash)
            ]
            # 非法行被拒绝。
            bad = await stack.client.put(
                "/api/v1/mcp-servers/weather/versions/1/tool-policies",
                headers=tenant_headers(),
                json={"policies": [{"tool_name": "x", "risk_level": "critical"}]},
            )
            assert bad.status_code == 400
    finally:
        app.should_exit = True
        await asyncio.wait_for(task, timeout=3)
        sock.close()


@pytest.mark.asyncio
async def test_skill_package_info_api() -> None:
    """TASK-014：上传后可读回包信息；缺失返回 404。"""
    async with console_stack() as stack:
        upload = await stack.client.post(
            "/api/v1/skills/packages",
            headers=tenant_headers(),
            files={"file": ("helper.zip", _package_zip(), "application/zip")},
        )
        assert upload.status_code == 200, upload.text
        info = await stack.client.get(
            "/api/v1/skills/helper/versions/1/package", headers=tenant_headers()
        )
        assert info.status_code == 200, info.text
        data = info.json()["data"]
        assert data["skill_id"] == "helper"
        assert data["artifact_uri"].startswith("artifact://tenant-a/skills/")
        assert data["artifact_hash"]
        assert data["knowledge_manifest"] == {"files": []}
        missing = await stack.client.get(
            "/api/v1/skills/ghost/versions/1/package", headers=tenant_headers()
        )
        assert missing.status_code == 404
