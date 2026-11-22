"""TASK-023 · AGENT_TURN 端到端：Draft → 确认 → 09:00 触发 → 冻结 prompt/工具/预算/静默。

真实边界：真实 Worker Draft/Confirm 路由、真实 PostgreSQL、真实 Kernel 执行器与工具管线；
模型传输用脚本化 provider（Kernel 契约不测 HTTP），容量/凭据/操作用记录型端口。平台工具
（`current_time`/`list_tasks`/`record_result`）与 outbox 都走生产路径。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from muad_agent_core.model import ModelRole, ModelToolCall
from muad_agent_worker.application.agentic.run import AgenticDependencies
from muad_agent_worker.infrastructure.models.task import (
    TaskDelivery,
    TaskExecution,
    TaskMessageOutbox,
    TaskSchedule,
)
from muad_agent_worker.main import app as worker_app
from muad_agent_worker.scheduler.service import SchedulerLoop
from muad_agent_worker.worker.agentic_executor import AgenticTaskExecutor
from muad_agent_worker.worker.dispatch import TaskTypeExecutor
from muad_agent_worker.worker.reminder import ReminderExecutor
from muad_contracts import (
    ResolvedAgent,
    ResolvedMcpServer,
    ScheduleAdmissionMaterials,
    ScheduleGrantMaterials,
    ScheduleMode,
    ScheduleModelMaterials,
)

from agent_worker.conftest import TenantContext
from agent_worker.helpers import (
    FakeMaterialsClient,
    FakePlatformSettingsClient,
    internal_service_headers,
    persist_task,
    run_worker_once,
)
from agent_worker.test_scheduled_v2_agentic_adapter import (
    RecordingCapacity,
    RecordingCredentials,
    RecordingOperations,
    StubResolver,
)
from agent_worker.test_scheduled_v2_fire import set_next
from tests.kernel_adapter_contract import ScriptedModelProvider, text_reply, tool_call_reply

pytestmark = pytest.mark.integration

TZ = "Asia/Shanghai"
#: 2036-11-19 09:00 Asia/Shanghai；固定的未来槽保证 lease/deadline 不随运行日期过期。
SLOT = datetime(2036, 11, 19, 1, 0, tzinfo=UTC)
PREVIOUS_DAY = "2036-11-18"
NOW = datetime(2036, 11, 19, 2, 30, tzinfo=UTC)  # 10:30 Asia/Shanghai
ROUTE = {
    "channel": "WECOM",
    "bot_id": "bot-scheduled",
    "external_user_id": "wotv-scheduled",
}
SETTINGS: dict[str, Any] = {
    "agent": {
        "max_turns": 7,
        "max_tool_calls": 9,
        "deadline_ms": 120_000,
        "max_model_retries": 2,
        "max_identical_failures": 3,
    },
    "task": {
        # 测试用固定 09:00 槽：绝对 deadline 留够未来时间，避免随运行日期过期。
        "default_deadline_hours": 24 * 365,
        "execution_timeout_sec": 240,
    },
}


@dataclass
class _ScriptedFactory:
    script: list[Any] = field(default_factory=list)
    provider: ScriptedModelProvider | None = None

    def __call__(self, **_: Any) -> ScriptedModelProvider:
        self.provider = ScriptedModelProvider(self.script)
        return self.provider


def _headers(
    tenant: TenantContext,
    *,
    actor: uuid.UUID,
    agent: uuid.UUID,
    key: str | None = None,
    **extra: str,
) -> dict[str, str]:
    values = {
        "X-Actor-User-Id": str(actor),
        "X-Agent-Id": str(agent),
        "Idempotency-Key": key or f"e2e-{uuid.uuid4()}",
        **extra,
    }
    return internal_service_headers(tenant.tenant_id, **values)


def _agentic_materials() -> ScheduleAdmissionMaterials:
    deployment_id = uuid.uuid4()
    mcp_server_id = uuid.uuid4()
    return ScheduleAdmissionMaterials(
        mode=ScheduleMode.AGENTIC,
        agent=ResolvedAgent(id=uuid.uuid4(), key="agent", revision=1, instructions="be brief"),
        model=ScheduleModelMaterials(
            id=uuid.uuid4(),
            revision=1,
            model_id="gpt-4o-mini",
            protocol="OPENAI",
            deployment_id=deployment_id,
            params={"temperature": 0.0},
            pool_members=[
                {
                    "deployment_id": str(deployment_id),
                    "credential_id": str(uuid.uuid4()),
                    "revision": 1,
                    "weight": 1,
                }
            ],
        ),
        # 材料与授权都可用，但 Job 的 tools_allow 只收平台只读工具：这是 per-job 收紧。
        mcp_servers=[
            ResolvedMcpServer(
                mcp_server_id=mcp_server_id,
                key="crm",
                # 工具不在 allowlist，永不发起 MCP 调用；discard 端口保证即使误连也不出网。
                endpoint="http://127.0.0.1:1/mcp",
                catalog_revision=4,
                definitions=[
                    {
                        "name": "customer.report",
                        "description": "customer report",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"q": {"type": "string"}},
                        },
                    }
                ],
            )
        ],
        grants=ScheduleGrantMaterials(
            agent_access=True, mcp_server_ids=[mcp_server_id]
        ),
        settings_revision=1,
    )


def _agent_body() -> dict[str, Any]:
    return {
        "instruction": "每天 9 点汇总昨天的定时任务执行，按失败原因分类",
        "name": "每日执行汇总",
        "recurrence": {"kind": "CRON", "cron": "0 9 * * *", "timezone": TZ},
        "payload": {
            "kind": "AGENT_TURN",
            "prompt": "汇总 {{previous_day}} 的定时任务执行，按失败原因分类",
            "tools_allow": ["PLATFORM:current_time", "PLATFORM:list_tasks"],
        },
        "session_target": "isolated",
        "delivery": {"intent": "PUSH", "route": ROUTE},
    }


async def _create_draft(
    client: httpx.AsyncClient,
    tenant: TenantContext,
    body: dict[str, Any],
    *,
    actor: uuid.UUID,
    agent: uuid.UUID,
) -> dict[str, Any]:
    response = await client.post(
        "/internal/schedule-drafts",
        json=body,
        headers=_headers(tenant, actor=actor, agent=agent),
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def _confirm(
    client: httpx.AsyncClient,
    tenant: TenantContext,
    draft: dict[str, Any],
    *,
    actor: uuid.UUID,
    agent: uuid.UUID,
    name: str,
) -> dict[str, Any]:
    response = await client.post(
        f"/internal/schedule-drafts/{draft['draft_id']}/confirm",
        json={"draft_hash": draft["draft_hash"], "name": name},
        headers=_headers(
            tenant,
            actor=actor,
            agent=agent,
            **{
                "X-Confirmation-Surface": "WEBCHAT",
                "X-Confirmation-Ref": f"webchat-message-{uuid.uuid4()}",
            },
        ),
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def _execution(tenant: TenantContext, execution_id: uuid.UUID) -> TaskExecution:
    async with tenant.session_factory() as session:
        row = await session.get(TaskExecution, execution_id)
    assert row is not None
    return row


@pytest.fixture
async def runtime_sessions() -> AsyncIterator[Any]:
    from muad_agent_runtime.infrastructure import db as runtime_db

    yield runtime_db.get_session_factory()


async def _seed_artifact(
    runtime_sessions: Any, tenant: TenantContext, task_id: uuid.UUID
) -> uuid.UUID:
    from muad_agent_runtime.infrastructure.models.runtime import Artifact

    artifact_id = uuid.uuid4()
    content = b"report"
    async with runtime_sessions() as session, session.begin():
        session.add(
            Artifact(
                id=artifact_id,
                tenant_id=tenant.tenant_id,
                run_id=None,
                task_id=task_id,
                artifact_type="OUTPUT",
                storage_key=f"tasks/{tenant.tenant_id}/{task_id}/report.txt",
                media_type="text/plain",
                size=len(content),
                checksum="sha256:" + "c" * 64,
                metadata_json={"filename": "report.txt", "kind": "DOCUMENT"},
            )
        )
    return artifact_id


async def test_confirmed_agent_turn_runs_with_frozen_prompt_tools_budget_and_silence(
    client: httpx.AsyncClient,
    tenant: TenantContext,
    runtime_sessions: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor, agent = uuid.uuid4(), uuid.uuid4()
    materials_client = FakeMaterialsClient(_agentic_materials())
    settings_client = FakePlatformSettingsClient(SETTINGS)
    monkeypatch.setattr(
        worker_app.state, "schedule_materials_client", materials_client, raising=False
    )
    monkeypatch.setattr(
        worker_app.state, "platform_settings_client", settings_client, raising=False
    )

    body = _agent_body()
    assert "mode" not in body, "payload kind is derived; the user never picks a mode"
    draft = await _create_draft(client, tenant, body, actor=actor, agent=agent)
    preview = draft["preview"]
    assert preview["mode"] == "AGENTIC"
    assert preview["budget_source"] == "PLATFORM"
    assert preview["tools_allow"] == ["PLATFORM:current_time", "PLATFORM:list_tasks"]

    confirmed = await _confirm(
        client, tenant, draft, actor=actor, agent=agent, name=body["name"]
    )
    schedule_id = uuid.UUID(confirmed["schedule_id"])
    prior_result = {
        "status": "done",
        "text": "上次完成 5 项",
        "scheduled_at": "2036-11-17T01:00:00+00:00",
    }
    async with tenant.session_factory() as session, session.begin():
        await session.execute(
            sa.update(TaskSchedule)
            .where(TaskSchedule.id == schedule_id)
            .values(last_result_json=prior_result)
        )
    await set_next(tenant, schedule_id, SLOT)

    previous_task = await persist_task(
        tenant,
        agent_id=agent,
        actor_user_id=actor,
        status="COMPLETED",
        task_type="AGENTIC",
        trigger_type="SCHEDULED",
        result_json={"text": "上周汇总：3 个失败"},
    )

    loop = SchedulerLoop(
        tenant.session_factory,
        materials_client,
        tenant.settings,
        settings_client=settings_client,
    )
    execution_id = await loop.run_once(now=SLOT + timedelta(seconds=1))
    assert execution_id is not None, "the 09:00 occurrence must be executed"
    execution = await _execution(tenant, execution_id)

    assert execution.scheduled_at == SLOT
    assert execution.trigger_type == "SCHEDULED"
    assert execution.task_type == "AGENTIC"
    assert execution.input_json["prompt"] == "汇总 {{previous_day}} 的定时任务执行，按失败原因分类"
    assert execution.input_json["tools_allow"] == [
        "PLATFORM:current_time",
        "PLATFORM:list_tasks",
    ]
    assert execution.input_json["session_target"] == "isolated"
    assert execution.input_json["budget_ref"] == "platform"
    snapshot = execution.execution_snapshot_json
    assert snapshot["allowlist"] == [], "only platform read-only tools are allowed"
    assert [server["key"] for server in snapshot["mcp"]] == ["crm"]
    assert snapshot["mcp"][0]["definitions"][0]["name"] == "customer.report"
    budget = snapshot["budget"]
    assert budget["max_steps"] == 7
    assert budget["max_tool_calls"] == 9
    assert budget["max_tokens"] == 0
    assert budget["max_model_retries"] == 2
    assert budget["max_identical_failures"] == 3
    assert budget["deadline_ms"] == 120_000
    assert budget["execution_timeout_sec"] == 240
    assert datetime.fromisoformat(budget["deadline_at"]) == execution.deadline_at

    artifact_id = await _seed_artifact(runtime_sessions, tenant, execution.id)

    factory = _ScriptedFactory()
    http = httpx.AsyncClient()
    try:
        deps = AgenticDependencies(
            settings=tenant.settings,
            sessions=tenant.session_factory,
            artifact_resolver=StubResolver(),  # type: ignore[arg-type]
            skill_executor=object(),  # type: ignore[arg-type]
            capacity=RecordingCapacity(),  # type: ignore[arg-type]
            credentials=RecordingCredentials(),  # type: ignore[arg-type]
            operations=RecordingOperations(),  # type: ignore[arg-type]
            artifacts=None,
            mcp_http=http,
            provider_factory=factory,
            clock=lambda: NOW,
        )
        executor = AgenticTaskExecutor(deps=deps, skill_runner=lambda task, suspension, egress: None)
        factory.script = [
            tool_call_reply(ModelToolCall("call-1", "current_time", {})),
            tool_call_reply(ModelToolCall("call-2", "list_tasks", {"limit": 5})),
            tool_call_reply(
                ModelToolCall("call-3", "mcp::crm::customer.report", {"q": "x"})
            ),
            tool_call_reply(ModelToolCall("call-4", "record_result", {"status": "no_change"})),
            text_reply("本次没有变化 NO_REPLY"),
        ]
        claimed = await run_worker_once(
            tenant,
            TaskTypeExecutor(
                reminder=ReminderExecutor(),
                skill=ReminderExecutor(),
                agentic=executor,
            ),
            now=SLOT + timedelta(seconds=1),
        )
    finally:
        await http.aclose()

    assert claimed == execution.id
    provider = factory.provider
    assert provider is not None
    assert provider.call_count == 5

    prompt = provider.requests[0].messages[-1].content
    assert isinstance(prompt, str)
    assert prompt.startswith(
        f"[schedule:{schedule_id} 每日执行汇总] 汇总 {PREVIOUS_DAY} 的定时任务执行，按失败原因分类"
    )
    assert "Current time: 2036-11-19T09:00:00+08:00 (Asia/Shanghai)" in prompt
    assert "Reference UTC: 2036-11-19T01:00:00+00:00" in prompt
    assert (
        "Window: 2036-11-18T00:00:00+08:00 ~ 2036-11-19T00:00:00+08:00" in prompt
    )
    assert "Unattended scheduled run:" in prompt
    assert "Nobody is available to answer questions" in prompt
    assert "Use record_result(no_change)" in prompt
    assert "上次完成 5 项" in prompt

    tool_results = {
        str(message.tool_call_id): message.content
        for message in provider.requests[-1].messages
        if message.role is ModelRole.TOOL
    }
    assert tool_results["call-1"] == "2036-11-19T10:30:00+08:00 (Asia/Shanghai)"
    listed = json.loads(str(tool_results["call-2"]))
    assert any(item["task_id"] == str(previous_task.id) for item in listed["tasks"])
    assert any(item["summary"] == "上周汇总：3 个失败" for item in listed["tasks"])
    assert "unknown tool" in str(tool_results["call-3"])
    assert '"RECORDED"' in str(tool_results["call-4"])
    assert '"no_change"' in str(tool_results["call-4"])

    stored = await _execution(tenant, execution.id)
    assert stored.status == "COMPLETED"
    assert stored.result_json is not None
    assert stored.result_json["text"] == ""
    assert stored.result_json["result_status"] == "no_change"
    assert "NO_REPLY" not in json.dumps(stored.result_json, ensure_ascii=False)

    async with tenant.session_factory() as session:
        outbox = list(
            await session.scalars(
                sa.select(TaskMessageOutbox).where(TaskMessageOutbox.task_id == execution.id)
            )
        )
        deliveries = list(
            await session.scalars(
                sa.select(TaskDelivery).where(TaskDelivery.execution_id == execution.id)
            )
        )
        schedule_row = await session.get(TaskSchedule, schedule_id)
    assert len(outbox) == 1
    assert outbox[0].payload_json["text"] == ""
    assert [item["artifact_id"] for item in outbox[0].payload_json["attachments"]] == [
        str(artifact_id)
    ]
    assert deliveries == [], "the outbox owns the push; no duplicate delivery row"
    assert schedule_row is not None
    assert schedule_row.last_result_json == prior_result, "no_change must not overwrite state"

    # 预算来源是平台设置：同一个 Job 的下一次触发不会复用本执行的冻结值。
    assert snapshot["budget"]["max_steps"] == SETTINGS["agent"]["max_turns"]
    assert snapshot["budget"]["max_tool_calls"] == SETTINGS["agent"]["max_tool_calls"]
    assert snapshot["budget"]["max_tokens"] == 0
    assert snapshot["budget"]["deadline_ms"] == SETTINGS["agent"]["deadline_ms"]
