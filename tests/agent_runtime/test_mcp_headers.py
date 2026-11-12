"""[S-02][S-04][RULE-mcp-001] Runtime MCP 请求头三态与快照边界。

真实边界：Runtime `McpRuntimeAdapter`（真实 httpx 客户端与头合并）+ `httpx.MockTransport`
（逐请求记录实际发出的请求头）；不替换 adapter 的会话/头组装逻辑。
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
import pytest
from muad_agent_runtime.application.executor import _mcp_server_definition
from muad_agent_runtime.application.mcp_runtime_adapter import (
    McpRuntimeAdapter,
    McpServerDefinition,
    McpToolDefinition,
    McpToolError,
)
from muad_agent_runtime.application.run_service import build_snapshot
from muad_contracts import (
    ResolveDefinitionResponse,
    ResolvedAgent,
    ResolvedMcpServer,
    ResolvedModel,
    ResolvedSkill,
    SkillSource,
    WorkspaceRef,
    build_task_snapshot,
    snapshot_hash,
)

pytestmark = pytest.mark.unit

PROTOCOL_VERSION = "2025-03-26"
SERVER_KEY = "headers-probe"
TOOL_NAME = "probe_tool"
SECRET = "canary-secret-value"


class HeaderProbe:
    """记录每个 JSON-RPC 请求实际携带的头，并像真实 server 一样校验会话。"""

    def __init__(self) -> None:
        self.requests: list[tuple[str, httpx.Headers]] = []
        self.session_seq = 0
        self.initialized_sessions: set[str] = set()
        self.fail_is_error = False
        self.fail_initialize = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        method = str(body.get("method"))
        self.requests.append((method, request.headers))
        if method == "initialize":
            if self.fail_initialize:
                return _error(body, -32000, "initialize failed")
            self.session_seq += 1
            session_id = f"probe-session-{self.session_seq}"
            self.initialized_sessions.add(session_id)
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": body.get("id"),
                    "result": {
                        "protocolVersion": PROTOCOL_VERSION,
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "mcp-header-probe", "version": "1.0.0"},
                    },
                },
                headers={"Mcp-Session-Id": session_id},
            )
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "tools/call":
            if request.headers.get("mcp-session-id") not in self.initialized_sessions:
                return _error(body, -32600, "session not initialized")
            result: dict[str, Any] = {
                "content": [
                    {
                        "type": "text",
                        "text": f"probe:{body.get('params', {}).get('name')}",
                    }
                ]
            }
            if self.fail_is_error:
                result = {
                    "content": [{"type": "text", "text": "probe tool failure"}],
                    "isError": True,
                }
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": body.get("id"), "result": result}
            )
        return _error(body, -32601, f"unsupported method: {method}")

    def headers_for(self, method: str) -> list[httpx.Headers]:
        return [headers for name, headers in self.requests if name == method]


def _error(body: dict[str, Any], code: int, message: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "jsonrpc": "2.0",
            "id": body.get("id"),
            "error": {"code": code, "message": message},
        },
    )


def _server(
    *,
    headers: dict[str, str] | None = None,
    secret_header: str | None = None,
    auth_secret: str | None = None,
) -> McpServerDefinition:
    return McpServerDefinition(
        mcp_server_id=uuid.uuid4(),
        key=SERVER_KEY,
        endpoint="http://mcp-probe/mcp",
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
        auth_secret=auth_secret,
        headers=headers or {},
        secret_header=secret_header,
    )


def _adapter(probe: HeaderProbe) -> McpRuntimeAdapter:
    return McpRuntimeAdapter(audit_writer=None, transport=httpx.MockTransport(probe))


async def _call(adapter: McpRuntimeAdapter, server: McpServerDefinition) -> str:
    return await adapter.execute_tool(
        server=server,
        tool=server.tools[0],
        arguments={"query": "x"},
        policy_decision="ALLOW",
    )


async def test_s02_static_and_secret_headers_reach_initialize_and_tools_call() -> None:
    """[S-02] 自定义静态头 + 密钥头在 initialize 与 tools/call 上完全一致。"""
    probe = HeaderProbe()
    server = _server(
        headers={"X-App-Id": "sdspmcp", "X-Platform-Uid": "53842"},
        secret_header="X-Api-Key",
        auth_secret=SECRET,
    )
    adapter = _adapter(probe)
    try:
        assert await _call(adapter, server) == f"probe:{TOOL_NAME}"
    finally:
        await adapter.aclose()

    for method in ("initialize", "tools/call"):
        sent = probe.headers_for(method)
        assert len(sent) == 1, f"{method} 请求数异常"
        assert sent[0]["x-app-id"] == "sdspmcp"
        assert sent[0]["x-platform-uid"] == "53842"
        assert sent[0]["x-api-key"] == SECRET, "密钥头必须是 auth_secret 原始值"
        assert "authorization" not in sent[0], "自定义密钥头模式不得再发 Authorization"
        assert sent[0]["mcp-protocol-version"] == PROTOCOL_VERSION
        assert sent[0]["accept"] == "application/json, text/event-stream"
    assert probe.headers_for("tools/call")[0]["mcp-session-id"] == "probe-session-1"


async def test_s02_bearer_is_default_when_secret_header_is_absent() -> None:
    """[S-02] 未配置 secret_header 时 auth_secret 仍走 `Authorization: Bearer`。"""
    probe = HeaderProbe()
    server = _server(headers={"X-App-Id": "app-1"}, auth_secret="bearer-token")
    adapter = _adapter(probe)
    try:
        assert await _call(adapter, server) == f"probe:{TOOL_NAME}"
    finally:
        await adapter.aclose()

    for method in ("initialize", "tools/call"):
        sent = probe.headers_for(method)[0]
        assert sent["authorization"] == "Bearer bearer-token"
        assert sent["x-app-id"] == "app-1"


async def test_s04_no_credentials_sends_no_authorization() -> None:
    """[S-04] 无认证 MCP：只发静态头，不凭空发 Authorization。"""
    probe = HeaderProbe()
    server = _server(headers={"X-App-Id": "app-1"})
    adapter = _adapter(probe)
    try:
        assert await _call(adapter, server) == f"probe:{TOOL_NAME}"
    finally:
        await adapter.aclose()

    for method in ("initialize", "tools/call"):
        sent = probe.headers_for(method)[0]
        assert "authorization" not in sent
        assert "x-api-key" not in sent
        assert sent["x-app-id"] == "app-1"


async def test_e14_session_rebuild_resends_same_headers() -> None:
    """[E-14] 会话重建后新 initialize/tools/call 仍携带同一组头。"""
    probe = HeaderProbe()
    server = _server(
        headers={"X-App-Id": "sdspmcp"},
        secret_header="X-Api-Key",
        auth_secret=SECRET,
    )
    adapter = _adapter(probe)
    try:
        assert await _call(adapter, server) == f"probe:{TOOL_NAME}"
        probe.fail_is_error = True
        with pytest.raises(McpToolError) as rejected:
            await _call(adapter, server)
        assert rejected.value.reason == "tool_error"

        probe.fail_is_error = False
        assert await _call(adapter, server) == f"probe:{TOOL_NAME}"
    finally:
        await adapter.aclose()

    assert len(probe.headers_for("initialize")) == 2, "失败后未重建会话"
    for sent in probe.headers_for("initialize"):
        assert sent["x-app-id"] == "sdspmcp"
        assert sent["x-api-key"] == SECRET
        assert "authorization" not in sent
    calls = probe.headers_for("tools/call")
    assert [sent["mcp-session-id"] for sent in calls] == [
        "probe-session-1",
        "probe-session-1",
        "probe-session-2",
    ]
    assert all(sent["x-api-key"] == SECRET for sent in calls)


async def test_e14_is_error_propagates_and_allows_retry() -> None:
    """[E-14] `isError: true` 必须按工具错误上抛，不得伪报成功；重试重新 initialize。"""
    probe = HeaderProbe()
    probe.fail_is_error = True
    server = _server(secret_header="X-Api-Key", auth_secret=SECRET)
    adapter = _adapter(probe)
    try:
        with pytest.raises(McpToolError) as rejected:
            await _call(adapter, server)
        assert rejected.value.reason == "tool_error"
        assert "probe tool failure" in rejected.value.detail

        probe.fail_is_error = False
        assert await _call(adapter, server) == f"probe:{TOOL_NAME}"
    finally:
        await adapter.aclose()

    assert len(probe.headers_for("initialize")) == 2


def test_resolved_server_definition_carries_headers_and_secret_header() -> None:
    """[S-02] Console resolve 契约字段原样进入 Runtime 定义（密钥仍来自凭据映射）。"""
    server_id = uuid.uuid4()
    resolved = ResolvedMcpServer(
        mcp_server_id=server_id,
        key=SERVER_KEY,
        endpoint="http://mcp-probe/mcp",
        catalog_revision=3,
        catalog_hash="sha256:" + "b" * 64,
        definitions=[{"name": TOOL_NAME}],
        headers={"X-App-Id": "sdspmcp"},
        secret_header="X-Api-Key",
    )

    definition = _mcp_server_definition(resolved, {str(server_id): SECRET})

    assert definition.headers == {"X-App-Id": "sdspmcp"}
    assert definition.secret_header == "X-Api-Key"
    assert definition.auth_secret == SECRET


def _snapshot_inputs() -> tuple[ResolvedAgent, ResolvedModel, ResolvedSkill, WorkspaceRef]:
    agent = ResolvedAgent(id=uuid.uuid4(), key="agent-key", revision=1, instructions="answer")
    return (
        agent,
        ResolvedModel(
            id=uuid.uuid4(),
            revision=1,
            model_id="gpt-4o-mini",
            base_url="https://model.example/v1",
        ),
        ResolvedSkill(
            skill_id=uuid.uuid4(),
            artifact_id=uuid.uuid4(),
            key="my-skill",
            name="My Skill",
            description="skill",
            version="1.0.0",
            checksum="sha256:" + "a" * 64,
            storage_key="skills/my-skill/1.0.0/skill.zip",
            source=SkillSource.ADMIN,
        ),
        WorkspaceRef(tenant_id="tenant-1", user_id=uuid.uuid4(), agent_id=agent.id),
    )


def _task_snapshot(
    server: ResolvedMcpServer,
    inputs: tuple[ResolvedAgent, ResolvedModel, ResolvedSkill, WorkspaceRef] | None = None,
) -> dict[str, Any]:
    agent, model, skill, workspace = inputs or _snapshot_inputs()
    return build_task_snapshot(
        agent=agent,
        model=model,
        skill=skill,
        workspace=workspace,
        mcp_servers=[server],
    )


@pytest.mark.contract
def test_task_snapshot_mcp_entry_excludes_headers_and_secret_header() -> None:
    """[RULE-snapshot-001] Task Snapshot 的 MCP 条目只含 ID/catalog/tools，不冻结认证配置。"""
    server = ResolvedMcpServer(
        mcp_server_id=uuid.uuid4(),
        key=SERVER_KEY,
        endpoint="http://mcp-probe/mcp",
        catalog_revision=3,
        catalog_hash="sha256:" + "b" * 64,
        definitions=[{"name": TOOL_NAME}],
        headers={"X-App-Id": "sdspmcp"},
        secret_header="X-Api-Key",
    )

    entry = _task_snapshot(server)["mcp"][0]

    assert set(entry) == {"mcp_server_id", "catalog_revision", "catalog_hash", "tools"}
    assert entry["tools"] == [TOOL_NAME]
    serialized = json.dumps(_task_snapshot(server), ensure_ascii=False)
    assert "X-App-Id" not in serialized
    assert "sdspmcp" not in serialized
    assert "X-Api-Key" not in serialized


@pytest.mark.contract
def test_task_snapshot_hash_ignores_header_config_changes() -> None:
    """[RULE-snapshot-001] 认证头不参与 Task 快照 hash（与 endpoint 同等处理）。"""
    base = ResolvedMcpServer(
        mcp_server_id=uuid.uuid4(),
        key=SERVER_KEY,
        endpoint="http://mcp-probe/mcp",
        catalog_revision=3,
        definitions=[{"name": TOOL_NAME}],
    )
    with_headers = base.model_copy(
        update={"headers": {"X-App-Id": "sdspmcp"}, "secret_header": "X-Api-Key"}
    )

    inputs = _snapshot_inputs()
    assert snapshot_hash(_task_snapshot(base, inputs)) == snapshot_hash(
        _task_snapshot(with_headers, inputs)
    )


@pytest.mark.contract
def test_run_snapshot_freezes_non_secret_headers_without_secret() -> None:
    """Run snapshot 冻结非密认证头以保证 resume 一致；密钥值仍只来自 API-09。"""
    agent, model, skill, _workspace = _snapshot_inputs()
    server = ResolvedMcpServer(
        mcp_server_id=uuid.uuid4(),
        key=SERVER_KEY,
        endpoint="http://mcp-probe/mcp",
        catalog_revision=3,
        definitions=[{"name": TOOL_NAME}],
        headers={"X-App-Id": "sdspmcp"},
        secret_header="X-Api-Key",
    )

    snapshot = build_snapshot(
        run_id=uuid.uuid4(),
        tenant_id="tenant-1",
        resolved=ResolveDefinitionResponse(
            agent=agent,
            model=model,
            skills=[skill],
            mcp_servers=[server],
        ),
    )

    entry = snapshot.mcp_catalog_json[0]
    assert entry["headers"] == {"X-App-Id": "sdspmcp"}
    assert entry["secret_header"] == "X-Api-Key"
    assert "auth_secret" not in entry
    assert SECRET not in json.dumps(entry, ensure_ascii=False)
