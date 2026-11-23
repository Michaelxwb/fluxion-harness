"""Real PG claim/resume, Redis metering and HTTP for Worker MCP/Skill budgets."""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
from muad_agent_core.agent import AgentRunStatus
from muad_agent_core.model import ModelToolCall
from muad_agent_core.skill import SkillExecutionRequest, SkillExecutionResult, SkillExecutionStatus
from muad_agent_worker.application.agentic.snapshot import load_frozen_execution
from muad_agent_worker.application.agentic.suspension import AgenticSuspension
from muad_agent_worker.infrastructure.models.task import TaskExecution
from muad_agent_worker.worker.agentic_executor import AgenticTaskExecutor
from muad_agent_worker.worker.agentic_skills import AgenticSkillTools
from muad_agent_worker.worker.claimer import TaskClaimer
from muad_agent_worker.worker.executor import TaskExecutionError
from muad_common import SharedSettings
from muad_contracts.shared_egress import SharedEgressPolicy
from muad_platform_sdk.host_egress_execution import HostEgressExecution
from muad_platform_sdk.shared_redis import SharedRedisMeter
from redis.asyncio import Redis
from sqlalchemy import text

from agent_worker.conftest import TenantContext
from tests.agent_worker.test_scheduled_v2_agentic_adapter import (
    WorkerAdapterHarness,
    _deps_for,
    agentic_snapshot,
    insert_task,
)
from tests.agent_worker.test_task_lease_epoch import waiting_again
from tests.kernel_adapter_contract import text_reply, tool_call_reply

pytestmark = pytest.mark.integration


@pytest.fixture
def mcp_probe() -> Iterator[tuple[str, list[str]]]:
    methods: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            methods.append(payload["method"])
            result = {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "serverInfo": {"name": "probe", "version": "1"},
            }
            if payload["method"] != "initialize":
                result = {"content": [{"type": "text", "text": "ok"}]}
            body = json.dumps({"jsonrpc": "2.0", "id": payload.get("id"), "result": result}).encode()
            self.send_response(200 if "id" in payload else 202)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/rpc", methods
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
async def metered_settings(tenant: TenantContext) -> AsyncIterator[SharedSettings]:
    redis = Redis.from_url(tenant.settings.require_redis_url(), decode_responses=True)
    domain = "mcp-budget-" + uuid.uuid4().hex
    meter = SharedRedisMeter(redis, domain=domain, epoch="epoch")
    await redis.hset(meter._key("guard"), mapping={"epoch": "epoch"})
    await meter.publish(SharedEgressPolicy(revision=1))
    settings = tenant.settings.model_copy(
        update={
            "egress_meter_domain": domain,
            "egress_meter_epoch": "epoch",
            "egress_http_read_timeout_sec": 2,
            "egress_cleanup_sec": 0.1,
            "egress_renewal_interval_sec": 0.05,
            "egress_source_refresh_sec": 0.5,
            "egress_stop_margin_sec": 12,
        }
    )
    try:
        yield settings
    finally:
        async with tenant.session_factory.begin() as session:
            for table in ("runtime.egress_invocation", "runtime.egress_execution"):
                await session.execute(
                    text(f"DELETE FROM {table} WHERE tenant_id=:tenant"), {"tenant": tenant.tenant_id}
                )
        keys = [key async for key in redis.scan_iter(meter._key("*"))]
        if keys:
            await redis.delete(*keys)
        await redis.aclose()


class HttpSkillExecutor:
    def __init__(self, endpoint: str) -> None:
        self._endpoint = endpoint

    async def execute(self, request: SkillExecutionRequest) -> SkillExecutionResult:
        assert request.platform_context is not None
        factory = request.platform_context.transport_factory
        assert factory is not None and request.egress_broker is not None
        async with httpx.AsyncClient(transport=factory(True)) as client:
            await client.post(self._endpoint, json={"jsonrpc": "2.0", "id": "skill", "method": "ping"})
        return SkillExecutionResult(status=SkillExecutionStatus.SUCCEEDED, stdout="ok")


class ReadyResolver:
    def __init__(self, root: Path) -> None:
        self._root = root

    async def ensure(self, *, artifact_id: str, storage_key: str, checksum: str) -> Path:
        return self._root


@pytest.fixture
async def budget_harness(
    tenant: TenantContext,
    metered_settings: SharedSettings,
    mcp_probe: tuple[str, list[str]],
    tmp_path: Path,
) -> AsyncIterator[WorkerAdapterHarness]:
    harness = WorkerAdapterHarness(tenant)
    await harness._mcp_http.aclose()
    harness._mcp_http = httpx.AsyncClient(trust_env=False)
    deps = replace(_deps_for(tenant, harness), settings=metered_settings)

    def skills(
        task: TaskExecution, suspension: AgenticSuspension, egress: HostEgressExecution | None
    ) -> AgenticSkillTools:
        frozen = load_frozen_execution(task)
        return AgenticSkillTools(
            task=task,
            resolver=ReadyResolver(tmp_path),
            executor=HttpSkillExecutor(mcp_probe[0]),
            settings=metered_settings,
            sessions=tenant.session_factory,
            suspension=suspension,
            operations=None,
            skills={skill.skill_id: skill for skill in frozen.snapshot.skills},
            egress=egress,
        )

    harness._executor = AgenticTaskExecutor(deps=deps, skill_runner=skills)
    try:
        yield harness
    finally:
        await harness._mcp_http.aclose()


async def _task(tenant: TenantContext, endpoint: str, cap: int) -> TaskExecution:
    snapshot = agentic_snapshot(
        max_steps=8, max_tool_calls=8, egress_budget={"execution": {"max_requests": cap}}
    )
    snapshot["mcp"][0]["endpoint"] = endpoint
    snapshot["skills"][0]["execution_mode"] = "SYNC"
    snapshot["tools"][0]["execution_mode"] = "SYNC"
    task = await insert_task(tenant, snapshot)
    frozen = load_frozen_execution(task)
    assert frozen.snapshot.budget.egress_budget.execution.max_requests == cap
    return task


def _mcp(call_id: str) -> ModelToolCall:
    return ModelToolCall(call_id, "mcp::crm::customer.report", {})


async def test_mcp_budget_survives_normal_suspension_and_a_new_claim(
    tenant: TenantContext,
    budget_harness: WorkerAdapterHarness,
    mcp_probe: tuple[str, list[str]],
) -> None:
    harness = budget_harness
    task = await _task(tenant, mcp_probe[0], 3)
    harness._factory.script = [
        tool_call_reply(_mcp("first")),
        tool_call_reply(
            ModelToolCall(
                "ask",
                "ask_user",
                {"question": "continue?", "input_schema": {"type": "object"}},
            )
        ),
    ]
    first = await harness._execute(task)
    assert first.status is AgentRunStatus.WAITING_INPUT
    assert len(mcp_probe[1]) == 3
    await waiting_again(tenant, task)
    claimer = TaskClaimer(tenant.session_factory, harness._executor._deps.settings)
    resumed = await claimer.claim_one("worker-agentic")
    assert resumed is not None and resumed.id == task.id and resumed.lease_epoch > task.lease_epoch
    harness._factory.script = [tool_call_reply(_mcp("second")), text_reply("must not run")]
    with pytest.raises(TaskExecutionError, match="LOCAL_BUDGET_EXHAUSTED"):
        await harness._executor.execute(resumed)
    assert len(mcp_probe[1]) == 3, "the resumed initialize must be denied before sending HTTP"
    assert not harness._mcp_http.is_closed, "the borrowed process pool remains open"


@pytest.mark.parametrize("cap", [3, 5])
async def test_mcp_and_sync_skill_share_one_execution_budget(
    tenant: TenantContext,
    budget_harness: WorkerAdapterHarness,
    mcp_probe: tuple[str, list[str]],
    cap: int,
) -> None:
    harness = budget_harness
    task = await _task(tenant, mcp_probe[0], cap)
    harness._factory.script = [
        tool_call_reply(_mcp("first")),
        tool_call_reply(harness.staging_call("skill")),
        tool_call_reply(_mcp("second")),
        text_reply("done"),
    ]
    if cap == 3:
        with pytest.raises(TaskExecutionError, match="execute / LOCAL_BUDGET_EXHAUSTED"):
            await harness._executor.execute(task)
        assert len(mcp_probe[1]) == 3, "the Skill request uses the already consumed Execution cap"
    else:
        result = await harness._execute(task)
        assert result.status is AgentRunStatus.COMPLETED
        assert "ping" in mcp_probe[1], result.tool_results
        assert mcp_probe[1] == ["initialize", "notifications/initialized", "tools/call", "ping", "tools/call"]
