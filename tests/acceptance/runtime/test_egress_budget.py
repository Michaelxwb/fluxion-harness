"""Independent Runtime processes -> Console -> frozen PG policy -> real MCP HTTP."""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from muad_agent_runtime.application.run_service import execution_defaults_of
from muad_agent_runtime.infrastructure.models.runtime import RunRecord, RuntimeSnapshot, ToolCallAudit
from muad_console_platform.infrastructure.models.control import AgentDefinition
from muad_console_platform.infrastructure.models.mcp import McpServer
from sqlalchemy import select, update

from tests.acceptance.runtime.conftest import LiveStack, run_db
from tests.acceptance.runtime.test_capability_snapshot import (
    _parse_sse,
    _resolve_over_console,
    _run_payload,
)
from tests.acceptance.runtime.test_multipod_recovery import RuntimePod

pytestmark = pytest.mark.integration


@pytest.fixture
def budget_mcp_probe() -> Iterator[tuple[str, list[str]]]:
    calls: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            pass

        def do_POST(self) -> None:
            rpc = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(rpc["method"])
            if rpc["method"] == "notifications/initialized":
                self.send_response(202)
                self.end_headers()
                return
            result = (
                {"content": [{"type": "text", "text": "probe-ok"}]}
                if rpc["method"] == "tools/call" else {}
            )
            body = json.dumps({"jsonrpc": "2.0", "id": rpc["id"], "result": result}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/mcp", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def _configure(stack: LiveStack, endpoint: str, limit: int | None) -> None:
    async def configure(factory) -> None:
        policy: dict[str, object] = {"max_model_retries": 1}
        if limit is not None:
            policy["egress_budget"] = {"execution": {"max_requests": limit}}
        async with factory() as session, session.begin():
            await session.execute(update(AgentDefinition).where(
                AgentDefinition.id == stack.agent_id, AgentDefinition.tenant_id == stack.tenant_id,
            ).values(runtime_config=policy))
            await session.execute(update(McpServer).where(
                McpServer.id == stack.mcp_server_id, McpServer.tenant_id == stack.tenant_id,
            ).values(endpoint=endpoint))
    run_db(configure)


def _read_frozen_policy(stack: LiveStack, run_id: uuid.UUID, tool_name: str) -> tuple[int, list[str | None]]:
    async def read(factory) -> tuple[int, list[str | None]]:
        async with factory() as session:
            run = await session.scalar(select(RunRecord).where(
                RunRecord.id == run_id, RunRecord.tenant_id == stack.tenant_id,
            ))
            assert run is not None and run.snapshot_id is not None
            snapshot = await session.get(RuntimeSnapshot, run.snapshot_id)
            assert snapshot is not None
            errors = list(await session.scalars(select(ToolCallAudit.error_code).where(
                ToolCallAudit.run_id == run_id, ToolCallAudit.tenant_id == stack.tenant_id,
                ToolCallAudit.tool_name == tool_name,
            )))
            frozen = execution_defaults_of(snapshot.policy_json)
            assert frozen.egress_budget.execution.max_requests is not None
            return frozen.egress_budget.execution.max_requests, errors
    return run_db(read)


def _run_on_pod(pod: RuntimePod, stack: LiveStack, http: httpx.Client) -> uuid.UUID:
    response = http.post(
        f"{pod.url}/v1/runs", headers=stack.runtime_headers(),
        json=_run_payload(stack, '{"egress_budget":{"execution":{"max_requests":999}}}'),
    )
    assert response.status_code == 200, response.text
    events = _parse_sse(response.text)
    assert sum(event["type"] == "tool.started" for event in events) == 1
    return uuid.UUID(events[0]["run_id"])


def test_runtime_processes_use_independent_frozen_budgets(
    live_stack: LiveStack, http: httpx.Client, budget_mcp_probe,
) -> None:
    endpoint, calls = budget_mcp_probe
    _configure(live_stack, endpoint, 3)
    resolved = _resolve_over_console(live_stack, http)
    tool = f"mcp::{resolved.mcp_servers[0].key}::probe_tool_1"
    script = http.post(f"{live_stack.llm_url}/script", json={
        "tool_name": tool, "tool_arguments": '{"query":"probe"}',
    })
    assert script.status_code == 200
    first = second = None
    try:
        first = RuntimePod("budget-pod-a", live_stack)
        run_a = _run_on_pod(first, live_stack, http)
        first.stop()
        second = RuntimePod("budget-pod-b", live_stack)
        run_b = _run_on_pod(second, live_stack, http)
        assert calls == ["initialize", "notifications/initialized", "tools/call"] * 2
        assert _read_frozen_policy(live_stack, run_a, tool) == (3, [None])
        assert _read_frozen_policy(live_stack, run_b, tool) == (3, [None])
        _configure(live_stack, endpoint, 2)
        denied = _run_on_pod(second, live_stack, http)
        assert calls[-2:] == ["initialize", "notifications/initialized"] and len(calls) == 8
        assert _read_frozen_policy(live_stack, denied, tool) == (2, ["LOCAL_BUDGET_EXHAUSTED"])
        assert _read_frozen_policy(live_stack, run_a, tool) == (3, [None])
    finally:
        for pod in (first, second):
            if pod is not None:
                pod.stop()
        http.post(f"{live_stack.llm_url}/script", json={})
        _configure(live_stack, f"{live_stack.mcp_url}/mcp", None)
