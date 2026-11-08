from __future__ import annotations

import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from muad_agent_runtime.application.mcp_runtime_adapter import (
    McpRuntimeAdapter,
    McpServerDefinition,
    McpToolDefinition,
)
from muad_contracts.egress_budget import resolve_egress_budget
from muad_platform_sdk.platform_transport import McpBudgetScope
from muad_platform_sdk.request_budget import create_budget_controller


@pytest.mark.asyncio
async def test_mcp_budget_counts_initialize_notification_and_tool_and_preserves_429():
    calls: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            calls.append(payload["method"])
            if payload["method"] == "initialize":
                body = json.dumps({"jsonrpc": "2.0", "id": payload["id"], "result": {}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if payload["method"] == "notifications/initialized":
                self.send_response(202)
                self.end_headers()
                return
            body = b'{"error":"proxy"}'
            self.send_response(429)
            self.send_header("Retry-After", "7")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = httpx.AsyncClient()
    budget = create_budget_controller(
        resolve_egress_budget({"execution": {"max_requests": 3}}), segment_id="run"
    )
    adapter = McpRuntimeAdapter(
        audit_writer=None,
        client=client,
        budget=McpBudgetScope(budget),
    )
    server_def = McpServerDefinition(
        uuid.uuid4(),
        "knowledge",
        f"http://127.0.0.1:{server.server_port}/mcp",
        tools=[McpToolDefinition("search", "", {})],
    )
    try:
        with pytest.raises(Exception) as failure:
            await adapter.execute_tool(
                server=server_def,
                tool=server_def.tools[0],
                arguments={},
                policy_decision="ALLOW",
            )
        assert getattr(failure.value, "reason", None) == "upstream_http"
        assert getattr(failure.value, "status_code", None) == 429
        assert getattr(failure.value, "retry_after", None) == "7"
        assert calls == ["initialize", "notifications/initialized", "tools/call"]
        assert budget is not None and budget.used == 3
    finally:
        await client.aclose()
        server.shutdown()
        server.server_close()
        thread.join()
