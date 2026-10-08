"""[S-07][E-14][RULE-mcp-001] MCP 请求关联：singleflight initialize、ID 唯一、响应严格匹配与失败关连接。

不得 Mock 的真实边界：真实 uvicorn 127.0.0.1 的 Streamable HTTP JSON-RPC 探针（真实 TCP、真实
`Mcp-Session-Id`）+ Runtime `McpRuntimeAdapter`（真实 httpx 客户端）。探针逐请求记账，测试据
initialize 次数、请求 ID 集合、会话头与 TCP 客户端端口断言协议事实；错误路径用真实 RPC 错误 /
`isError` / 超时 / 取消驱动。
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from muad_agent_runtime.application.mcp_runtime_adapter import (
    McpRuntimeAdapter,
    McpServerDefinition,
    McpToolDefinition,
    McpToolError,
)

pytestmark = pytest.mark.integration

PROTOCOL_VERSION = "2025-03-26"
SERVER_KEY = "probe"
TOOL_NAME = "probe_tool"


class ProbeState:
    """探针记账：所有断言只依赖这些真实观察到的事实。"""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.initialize_count = 0
        self.call_count = 0
        self.initialize_ports: list[int] = []
        self.initialized_sessions: set[str] = set()
        self.session_seq = 0
        self.fail_initialize_remaining = 0
        self.fail_call_is_error = False
        self.wrong_response_id = False
        self.drop_response_id = False
        self.hold_call_sec = 0.0
        self.hold_initialize_sec = 0.0

    def _response_id(self, request_id: Any) -> Any:
        if self.wrong_response_id:
            return request_id + 1000 if isinstance(request_id, int) else request_id
        if self.drop_response_id:
            return None
        return request_id

    def methods(self) -> list[str]:
        return [request["method"] for request in self.requests]

    def request_ids(self) -> list[Any]:
        return [request["id"] for request in self.requests]


def _probe_app(state: ProbeState) -> FastAPI:
    app = FastAPI()

    @app.post("/mcp")
    async def mcp(request: Request):
        body = await request.json()
        method = body.get("method")
        request_id = body.get("id")
        port = request.client.port if request.client else None
        state.requests.append(
            {
                "method": method,
                "id": request_id,
                "port": port,
                "session": request.headers.get("mcp-session-id"),
            }
        )
        if method == "initialize":
            state.initialize_count += 1
            state.initialize_ports.append(port)
            if state.hold_initialize_sec > 0:
                await asyncio.sleep(state.hold_initialize_sec)
            if state.fail_initialize_remaining > 0:
                state.fail_initialize_remaining -= 1
                return JSONResponse(
                    {
                        "jsonrpc": "2.0",
                        "id": state._response_id(request_id),
                        "error": {"code": -32000, "message": "initialize failed"},
                    }
                )
            state.session_seq += 1
            session_id = f"probe-session-{state.session_seq}"
            state.initialized_sessions.add(session_id)
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": state._response_id(request_id),
                    "result": {
                        "protocolVersion": PROTOCOL_VERSION,
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "mcp-correlation-probe", "version": "1.0.0"},
                    },
                },
                headers={"Mcp-Session-Id": session_id},
            )
        if method == "notifications/initialized":
            return Response(status_code=202)
        if method == "tools/call":
            state.call_count += 1
            session_id = request.headers.get("mcp-session-id")
            if session_id not in state.initialized_sessions:
                return JSONResponse(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": -32600, "message": "session not initialized"},
                    }
                )
            if state.hold_call_sec > 0:
                await asyncio.sleep(state.hold_call_sec)
            result: dict[str, Any] = {
                "content": [{"type": "text", "text": f"probe:{body.get('params', {}).get('name')}"}]
            }
            if state.fail_call_is_error:
                result = {"content": [{"type": "text", "text": "probe tool failure"}], "isError": True}
            return JSONResponse(
                {"jsonrpc": "2.0", "id": state._response_id(request_id), "result": result}
            )
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"unsupported method: {method}"},
            }
        )

    return app


@pytest.fixture
def mcp_probe() -> Iterator[tuple[str, ProbeState]]:
    state = ProbeState()
    config = uvicorn.Config(
        _probe_app(state), host="127.0.0.1", port=0, log_level="error"
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1] if server.servers else 0
    try:
        yield f"http://127.0.0.1:{port}/mcp", state
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def _server(endpoint: str) -> McpServerDefinition:
    return McpServerDefinition(
        mcp_server_id=uuid.uuid4(),
        key=SERVER_KEY,
        endpoint=endpoint,
        catalog_revision=1,
        catalog_hash="sha256:" + "a" * 64,
        tools=[
            McpToolDefinition(
                name=TOOL_NAME,
                description="Probe tool",
                input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
                effect="READ",
            )
        ],
    )


async def _call(adapter: McpRuntimeAdapter, endpoint: str, *, query: str = "x") -> str:
    server = _server(endpoint)
    return await adapter.execute_tool(
        server=server,
        tool=server.tools[0],
        arguments={"query": query},
        policy_decision="ALLOW",
    )


async def _wait_until(predicate: Callable[[], bool], *, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("probe did not observe the expected request in time")
        await asyncio.sleep(0.01)


@pytest.mark.integration
async def test_s07_concurrent_calls_share_one_initialize_unique_ids_no_tools_list(
    mcp_probe, async_tool_database
) -> None:
    """[S-07] 并发 tools/call 仅一次 initialize；请求 ID 不复用；响应逐一匹配；无 tools/list。"""
    endpoint, state = mcp_probe
    state.hold_initialize_sec = 0.25  # 让两个调用有机会同时到达 initialize
    adapter = McpRuntimeAdapter(audit_writer=None)
    try:
        first, second = await asyncio.gather(_call(adapter, endpoint), _call(adapter, endpoint))
        assert first == f"probe:{TOOL_NAME}" and second == f"probe:{TOOL_NAME}"

        assert state.initialize_count == 1, "并发 tools/call 触发了多次 initialize"
        assert state.call_count == 2
        request_ids = [
            request["id"]
            for request in state.requests
            if request["method"] != "notifications/initialized"
        ]
        assert len(set(request_ids)) == len(request_ids), f"请求 ID 复用: {request_ids}"
        assert all(
            isinstance(request["id"], int)
            for request in state.requests
            if request["method"] in ("initialize", "tools/call")
        )
        sessions = {
            request["session"] for request in state.requests if request["method"] == "tools/call"
        }
        assert len(sessions) == 1 and None not in sessions, "tools/call 未复用同一 MCP 会话"
        assert "tools/list" not in state.methods(), "Run 内出现了 tools/list"

        # 顺序轮次继续复用已初始化会话，ID 继续递增且不复用。
        assert await _call(adapter, endpoint, query="again") == f"probe:{TOOL_NAME}"
        assert state.initialize_count == 1
        all_ids = [
            request["id"]
            for request in state.requests
            if request["method"] != "notifications/initialized"
        ]
        assert len(set(all_ids)) == len(all_ids)
        assert "tools/list" not in state.methods()
    finally:
        await adapter.aclose()


@pytest.mark.integration
@pytest.mark.parametrize("mode", ["wrong", "missing"])
async def test_e14_bad_response_id_is_protocol_error_and_forces_new_session(
    mcp_probe, async_tool_database, mode: str
) -> None:
    """[E-14] 错误/缺失响应 ID：严格匹配失败、关闭连接、后续调用重新 initialize。"""
    endpoint, state = mcp_probe
    adapter = McpRuntimeAdapter(audit_writer=None)
    try:
        assert await _call(adapter, endpoint) == f"probe:{TOOL_NAME}"
        if mode == "wrong":
            state.wrong_response_id = True
        else:
            state.drop_response_id = True
        with pytest.raises(McpToolError) as rejected:
            await _call(adapter, endpoint)
        assert rejected.value.reason == "protocol"

        state.wrong_response_id = False
        state.drop_response_id = False
        assert await _call(adapter, endpoint) == f"probe:{TOOL_NAME}"
        assert state.initialize_count == 2, "失败后未重建会话（旧会话被复用，连接未关闭）"
        assert state.initialize_ports[0] != state.initialize_ports[1], "失败后未关闭旧 TCP 连接"
    finally:
        await adapter.aclose()


@pytest.mark.integration
async def test_e14_initialize_failure_releases_lock_and_supports_retry(
    mcp_probe, async_tool_database
) -> None:
    """[E-14] 初始化失败：明确失败且关闭连接；后续调用可重试，无旧失败锁死。"""
    endpoint, state = mcp_probe
    state.fail_initialize_remaining = 1
    adapter = McpRuntimeAdapter(audit_writer=None)
    try:
        with pytest.raises(McpToolError) as rejected:
            await _call(adapter, endpoint)
        assert rejected.value.reason == "protocol"
        assert state.initialize_count == 1

        assert await _call(adapter, endpoint) == f"probe:{TOOL_NAME}"
        assert state.initialize_count == 2, "初始化失败后未重试"
        assert state.initialize_ports[0] != state.initialize_ports[1], "初始化失败后未关闭旧连接"
    finally:
        await adapter.aclose()


@pytest.mark.integration
async def test_e14_concurrent_waiters_survive_initialize_failure(
    mcp_probe, async_tool_database
) -> None:
    """[E-14] 初始化失败释放 singleflight 锁：等待方不被旧失败锁死，可继续完成调用。"""
    endpoint, state = mcp_probe
    state.fail_initialize_remaining = 1
    state.hold_initialize_sec = 0.05
    adapter = McpRuntimeAdapter(audit_writer=None)
    try:
        results = await asyncio.gather(
            _call(adapter, endpoint), _call(adapter, endpoint), return_exceptions=True
        )
        failures = [item for item in results if isinstance(item, McpToolError)]
        successes = [item for item in results if isinstance(item, str)]
        assert len(failures) == 1 and failures[0].reason == "protocol"
        assert successes == [f"probe:{TOOL_NAME}"]
        assert state.initialize_count == 2 and state.call_count == 1
    finally:
        await adapter.aclose()


@pytest.mark.integration
async def test_e14_is_error_fails_and_closes_session(mcp_probe, async_tool_database) -> None:
    """[E-14] isError：明确失败（不伪报成功）并关闭连接，重试重新 initialize。"""
    endpoint, state = mcp_probe
    adapter = McpRuntimeAdapter(audit_writer=None)
    try:
        assert await _call(adapter, endpoint) == f"probe:{TOOL_NAME}"
        state.fail_call_is_error = True
        with pytest.raises(McpToolError) as rejected:
            await _call(adapter, endpoint)
        assert rejected.value.reason == "tool_error"

        state.fail_call_is_error = False
        assert await _call(adapter, endpoint) == f"probe:{TOOL_NAME}"
        assert state.initialize_count == 2, "isError 后未关闭连接（重试复用了旧会话）"
        assert state.initialize_ports[0] != state.initialize_ports[1], "isError 后未关闭旧 TCP 连接"
    finally:
        await adapter.aclose()


@pytest.mark.integration
async def test_e14_timeout_fails_and_closes_session(mcp_probe, async_tool_database) -> None:
    """[E-14] 超时：明确失败并关闭连接，重试重新 initialize。"""
    endpoint, state = mcp_probe
    adapter = McpRuntimeAdapter(audit_writer=None, timeout_sec=0.3)
    try:
        assert await _call(adapter, endpoint) == f"probe:{TOOL_NAME}"
        state.hold_call_sec = 1.5
        with pytest.raises(McpToolError) as rejected:
            await _call(adapter, endpoint)
        assert rejected.value.reason == "timeout"

        state.hold_call_sec = 0.0
        assert await _call(adapter, endpoint) == f"probe:{TOOL_NAME}"
        assert state.initialize_count == 2, "超时后未关闭连接（重试复用了旧会话）"
    finally:
        await adapter.aclose()


@pytest.mark.integration
async def test_e14_cancellation_closes_session_and_retry_reinitializes(
    mcp_probe, async_tool_database
) -> None:
    """[E-14] 取消：明确以 CancelledError 失败并关闭连接，后续调用重新 initialize。"""
    endpoint, state = mcp_probe
    adapter = McpRuntimeAdapter(audit_writer=None)
    try:
        assert await _call(adapter, endpoint) == f"probe:{TOOL_NAME}"
        state.hold_call_sec = 1.0
        task = asyncio.create_task(_call(adapter, endpoint))
        await _wait_until(lambda: state.call_count == 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        state.hold_call_sec = 0.0
        assert await _call(adapter, endpoint) == f"probe:{TOOL_NAME}"
        assert state.initialize_count == 2, "取消后未关闭连接（重试复用了旧会话）"
    finally:
        await adapter.aclose()
