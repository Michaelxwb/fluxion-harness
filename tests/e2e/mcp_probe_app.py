"""E2E 用真实 Streamable HTTP MCP 探针 Server（JSON-RPC：initialize / tools/list）。

行为由环境变量控制：
- MCP_PROBE_TOOLS：tools/list 返回的工具数（默认 2）
- MCP_PROBE_FAIL_LIST=1：tools/list 返回 JSON-RPC 错误
- MCP_PROBE_REQUIRE_AUTH：非空时校验 Authorization 头
- MCP_PROBE_SSE=1：initialize/tools/list 以 text/event-stream 返回
- MCP_PROBE_SESSION=1：initialize 返回 Mcp-Session-Id，后续请求必须携带
- MCP_PROBE_PAGE_SIZE：tools/list 每页工具数（返回 nextCursor 分页）
- MCP_PROBE_DELAY_MS：响应前延迟毫秒数（超时测试）
- MCP_PROBE_EMPTY_LIST=1：tools/list 返回 202 空响应
- MCP_PROBE_FAIL_CALL=1：tools/call 返回 isError 结果
"""

from __future__ import annotations

import asyncio
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI()

PROTOCOL_VERSION = "2025-03-26"
SESSION_ID = "probe-session-1"


def _tool(index: int) -> dict[str, object]:
    annotations: dict[str, object] = {}
    if index == 1:
        annotations = {"readOnlyHint": True}
    elif index == 2:
        annotations = {"destructiveHint": True}
    return {
        "name": f"probe_tool_{index}",
        "description": f"Probe tool {index}",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        "annotations": annotations,
    }


def _response(payload: dict[str, object], *, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(payload, headers=headers or {})


def _sse_response(payload: dict[str, object], *, headers: dict[str, str] | None = None) -> StreamingResponse:
    import json

    body = f"event: message\ndata: {json.dumps(payload)}\n\n"

    async def stream():
        yield body

    return StreamingResponse(stream(), media_type="text/event-stream", headers=headers or {})


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/mcp")
async def mcp_endpoint(request: Request):
    delay_ms = int(os.environ.get("MCP_PROBE_DELAY_MS", "0"))
    if delay_ms > 0:
        await asyncio.sleep(delay_ms / 1000)
    auth = os.environ.get("MCP_PROBE_REQUIRE_AUTH", "")
    if auth and request.headers.get("authorization") != f"Bearer {auth}":
        return JSONResponse(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32001, "message": "unauthorized"}},
            status_code=401,
        )
    body = await request.json()
    method = body.get("method")
    request_id = body.get("id")
    headers: dict[str, str] = {}

    if method == "initialize":
        result = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "mcp-probe", "version": "1.0.0"},
        }
        if os.environ.get("MCP_PROBE_SESSION") == "1":
            headers["Mcp-Session-Id"] = SESSION_ID
    elif method == "notifications/initialized":
        if os.environ.get("MCP_PROBE_SESSION") == "1" and request.headers.get("mcp-session-id") != SESSION_ID:
            return JSONResponse(
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32001, "message": "missing session"}},
                status_code=401,
            )
        return JSONResponse(status_code=202, content={})
    elif method == "tools/call":
        if os.environ.get("MCP_PROBE_SESSION") == "1" and request.headers.get("mcp-session-id") != SESSION_ID:
            return JSONResponse(
                {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32001, "message": "missing session"}},
                status_code=401,
            )
        params = body.get("params") or {}
        tool_name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        if os.environ.get("MCP_PROBE_FAIL_CALL") == "1":
            result = {
                "content": [{"type": "text", "text": "probe tool failure"}],
                "isError": True,
            }
        else:
            result = {
                "content": [
                    {
                        "type": "text",
                        "text": f"probe-result:{tool_name}:{arguments.get('query', '')}",
                    }
                ]
            }
    elif method == "tools/list":
        if os.environ.get("MCP_PROBE_SESSION") == "1" and request.headers.get("mcp-session-id") != SESSION_ID:
            return JSONResponse(
                {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32001, "message": "missing session"}},
                status_code=401,
            )
        if os.environ.get("MCP_PROBE_EMPTY_LIST") == "1":
            return JSONResponse(status_code=202, content={})
        if os.environ.get("MCP_PROBE_FAIL_LIST") == "1":
            payload = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32000, "message": "tools/list failed"},
            }
            if os.environ.get("MCP_PROBE_SSE") == "1":
                return _sse_response(payload, headers=headers)
            return _response(payload, headers=headers)
        count = int(os.environ.get("MCP_PROBE_TOOLS", "2"))
        cursor = (body.get("params") or {}).get("cursor")
        page_size = int(os.environ.get("MCP_PROBE_PAGE_SIZE", "0"))
        start = int(cursor) if cursor else 0
        if page_size > 0:
            end = min(start + page_size, count)
            result = {"tools": [_tool(index) for index in range(start, end)]}
            if end < count:
                result["nextCursor"] = str(end)
        else:
            result = {"tools": [_tool(index) for index in range(count)]}
    else:
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": "unknown method"},
        }
        return _response(payload, headers=headers)
    payload = {"jsonrpc": "2.0", "id": request_id, "result": result}
    if os.environ.get("MCP_PROBE_SSE") == "1":
        return _sse_response(payload, headers=headers)
    return _response(payload, headers=headers)
