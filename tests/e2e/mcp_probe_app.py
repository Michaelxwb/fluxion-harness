"""E2E 用真实 Streamable HTTP MCP 探针 Server（JSON-RPC：initialize / tools/list）。

行为由环境变量控制：
- MCP_PROBE_TOOLS：tools/list 返回的工具数（默认 2）
- MCP_PROBE_FAIL_LIST=1：tools/list 返回 JSON-RPC 错误
- MCP_PROBE_REQUIRE_AUTH：非空时校验 Authorization 头
"""

from __future__ import annotations

import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()

PROTOCOL_VERSION = "2025-03-26"


def _tool(index: int) -> dict[str, object]:
    return {
        "name": f"probe_tool_{index}",
        "description": f"Probe tool {index}",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    }


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/mcp")
async def mcp_endpoint(request: Request) -> JSONResponse:
    auth = os.environ.get("MCP_PROBE_REQUIRE_AUTH", "")
    if auth and request.headers.get("authorization") != f"Bearer {auth}":
        return JSONResponse(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32001, "message": "unauthorized"}},
            status_code=401,
        )
    body = await request.json()
    method = body.get("method")
    request_id = body.get("id")

    if method == "initialize":
        result = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "mcp-probe", "version": "1.0.0"},
        }
    elif method == "notifications/initialized":
        return JSONResponse(status_code=202, content={})
    elif method == "tools/list":
        if os.environ.get("MCP_PROBE_FAIL_LIST") == "1":
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32000, "message": "tools/list failed"},
                }
            )
        count = int(os.environ.get("MCP_PROBE_TOOLS", "2"))
        result = {"tools": [_tool(index) for index in range(count)]}
    else:
        return JSONResponse(
            {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "unknown method"}}
        )
    return JSONResponse({"jsonrpc": "2.0", "id": request_id, "result": result})
