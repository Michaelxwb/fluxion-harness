"""Streamable HTTP MCP 探针：要求调用方发送配置的请求头（TASK-001 自定义头联调）。

`MCP_HEADER_PROBE_REQUIRE` 形如 `X-App-Id=app-1;X-Api-Key=secret`；为空时不校验。
"""

from __future__ import annotations

import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()

PROTOCOL_VERSION = "2025-03-26"


def _required_headers() -> dict[str, str]:
    raw = os.environ.get("MCP_HEADER_PROBE_REQUIRE", "")
    required: dict[str, str] = {}
    for pair in raw.split(";"):
        if not pair:
            continue
        name, _, value = pair.partition("=")
        if name:
            required[name.strip().lower()] = value
    return required


def _unauthorized() -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "id": None, "error": {"code": -32001, "message": "unauthorized"}},
        status_code=401,
    )


@app.post("/mcp")
async def mcp_endpoint(request: Request):
    for name, expected in _required_headers().items():
        if request.headers.get(name) != expected:
            return _unauthorized()
    body = await request.json()
    method = body.get("method")
    request_id = body.get("id")
    if method == "initialize":
        result: dict[str, object] = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "mcp-header-probe", "version": "1.0.0"},
        }
    elif method == "notifications/initialized":
        return JSONResponse(status_code=202, content={})
    elif method == "tools/list":
        result = {
            "tools": [
                {
                    "name": "header_probe_tool",
                    "description": "Header probe tool",
                    "inputSchema": {"type": "object", "properties": {}},
                }
            ]
        }
    else:
        return JSONResponse(
            {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "unknown method"}}
        )
    return JSONResponse({"jsonrpc": "2.0", "id": request_id, "result": result})
