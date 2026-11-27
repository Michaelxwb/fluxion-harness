"""Worker MCP host uses the shared client: arrays are data, isError is a logical failure."""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
from muad_agent_worker.application.agentic.mcp import McpServerRuntime, McpToolBox, McpToolError
from muad_agent_worker.infrastructure.mcp_audit import DatabaseMcpAudit
from muad_contracts import SnapshotMcpServer, ToolRetrySafety
from muad_platform_sdk.egress_audit_store import EgressAuditIdentity
from muad_platform_sdk.mcp_audit import McpAuditEvent
from sqlalchemy import text

from agent_worker.conftest import TenantContext


def _server(*, secret: str = "s3cret") -> tuple[uuid.UUID, McpServerRuntime]:
    server_id = uuid.uuid4()
    snapshot = SnapshotMcpServer(
        server_id=server_id,
        key="crm",
        endpoint="http://mcp/rpc",
        headers={"X-Static": "1"},
        secret_header="Authorization",
    )
    return server_id, McpServerRuntime(snapshot, secret)


def _handler(mode: str):
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        payload = json.loads(request.content)
        if payload["method"] == "notifications/initialized":
            return httpx.Response(202)
        if payload["method"] == "initialize":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": {}})
        content = (
            {"isError": True, "content": [{"type": "text", "text": "bad"}]}
            if mode == "error"
            else {"content": [{"type": "text", "text": mode}]}
        )
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": payload["id"], "result": content},
        )

    return handle, seen


async def test_worker_mcp_uses_shared_client_and_sends_auth() -> None:
    server_id, runtime = _server()
    handler, seen = _handler("[1,2,3]")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        box = McpToolBox({server_id: runtime}, http)
        result = await box.call(server_id, "lookup", {}, call_id="c1")

    assert result.text == "[1,2,3]"
    assert seen[0].headers["authorization"] == "s3cret"
    assert seen[0].headers["X-Static"] == "1"


async def test_worker_mcp_is_error_is_a_logical_failure() -> None:
    server_id, runtime = _server()
    handler, _ = _handler("error")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        box = McpToolBox({server_id: runtime}, http)
        with pytest.raises(McpToolError) as caught:
            await box.call(
                server_id,
                "write",
                {},
                retry_safety=ToolRetrySafety.NON_RETRYABLE,
                call_id="c1",
            )

    assert caught.value.code == "MCP_TOOL_FAILED"
    assert caught.value.sent
    assert caught.value.outcome_known


async def test_worker_mcp_bearer_auth_sends_authorization_header() -> None:
    server_id = uuid.uuid4()
    runtime = McpServerRuntime(
        SnapshotMcpServer(
            server_id=server_id,
            key="bearer",
            endpoint="http://mcp/rpc",
            headers={},
            secret_header=None,
        ),
        "s3cret",
    )
    handler, seen = _handler("pong")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        box = McpToolBox({server_id: runtime}, http)
        result = await box.call(server_id, "lookup", {}, call_id="c1")
        assert result.text == "pong"

    assert seen[0].headers["authorization"] == "Bearer s3cret"


async def test_worker_mcp_audit_is_written_to_egress_audit(
    tenant: TenantContext,
) -> None:
    task_id = uuid.uuid4()
    identity = EgressAuditIdentity(tenant.tenant_id, uuid.uuid4(), None, task_id)
    audit = DatabaseMcpAudit(tenant.session_factory, identity)
    event = McpAuditEvent(
        server_key="crm",
        tool_name="lookup",
        call_id="c1",
        rpc_method="tools/call",
        request_id="1",
        http_status=200,
        rpc_error_code=None,
        is_error=False,
        sent=True,
        outcome_known=True,
        result_status="OK",
        error_code=None,
        latency_ms=3,
        attempt_id=uuid.uuid4().hex,
    )
    try:
        await audit.record(event)
        async with tenant.session_factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT target_type, target, operation, result_status, task_id "
                        "FROM runtime.egress_audit "
                        "WHERE tenant_id = :tenant AND task_id = :task"
                    ),
                    {"tenant": tenant.tenant_id, "task": task_id},
                )
            ).mappings().one()
    finally:
        async with tenant.session_factory.begin() as session:
            await session.execute(
                text("DELETE FROM runtime.egress_audit WHERE tenant_id = :tenant"),
                {"tenant": tenant.tenant_id},
            )

    assert row["target_type"] == "MCP"
    assert row["target"] == "mcp://crm/lookup"
    assert row["operation"] == "mcp.tool"
    assert row["result_status"] == "OK"


async def test_worker_image_content_gets_an_artifact_id() -> None:
    server_id, runtime = _server()

    class _Writer:
        async def write(self, artifact) -> str:
            assert artifact.media_type == "image/png"
            return "artifact-image-1"

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload["method"] == "notifications/initialized":
            return httpx.Response(202)
        if payload["method"] == "initialize":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": {}})
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {
                    "content": [
                        {"type": "image", "mimeType": "image/png", "data": "aGVsbG8="}
                    ]
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        box = McpToolBox({server_id: runtime}, http, artifact_writer=_Writer())
        result = await box.call(server_id, "image", {}, call_id="c1")

    assert result.artifacts[0].artifact_id == "artifact-image-1"
    assert "[artifact: artifact-image-1]" in result.text
