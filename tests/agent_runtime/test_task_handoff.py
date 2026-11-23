"""B-123：Runtime Tool → Worker HTTP → PostgreSQL 的后台任务/定时任务交接（API-01/02/05）。

真实边界：真实 Runtime 工具入口（ToolRegistry handler）→ 真实 Worker HTTP（ASGI）
→ 真实 PostgreSQL；Runtime 不直接写 task schema。
"""

from __future__ import annotations

import json
import tempfile
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from muad_agent_core.tools import ToolCall, ToolError, ToolRegistry
from muad_agent_runtime.application.run_service import delivery_route_of
from muad_agent_runtime.application.skill_tools import EXECUTE_SKILL_TOOL, EXECUTE_TOOL, SkillToolSet
from muad_agent_runtime.application.task_client import TaskSubmissionContext, WorkerTaskClient
from muad_agent_runtime.application.task_tools import (
    DRAFT_SCHEDULE_TOOL,
    GET_TASK_TOOL,
    LIST_SCHEDULES_TOOL,
    PAUSE_SCHEDULE_TOOL,
    BackgroundTaskToolSet,
)
from muad_agent_worker.application.ports import PlatformSettingsSnapshot
from muad_agent_worker.infrastructure.db import get_session_factory as worker_session_factory
from muad_agent_worker.infrastructure.models.task import (
    TaskDelivery,
    TaskExecution,
    TaskSchedule,
    TaskScheduleDraft,
)
from muad_agent_worker.main import app as worker_app
from muad_artifact_store import NfsArtifactStore, SkillArtifactCache
from muad_contracts import (
    CompletionMode,
    DeliveryRouteInput,
    ResolvedAgent,
    ResolvedModel,
    ResolvedSkill,
    ScheduleAdmissionMaterials,
    ScheduleAdmissionRequest,
    ScheduleGrantMaterials,
    ScheduleMode,
    ScheduleModelMaterials,
    SkillExecutionMode,
)
from sqlalchemy import func, select, text

TOKEN = "runtime-worker-token"
TENANT = "test-runtime-handoff"


@pytest.fixture(autouse=True)
def internal_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", TOKEN)


#: 清理顺按 FK：提交台账引用 Draft/Schedule，Draft 引用 Revision 的目标 Schedule。
TASK_TENANT_CLEANUP = (
    "DELETE FROM task.runtime_result_outbox WHERE tenant_id = :t",
    "DELETE FROM task.runtime_operation WHERE tenant_id = :t",
    "DELETE FROM task.task_event WHERE tenant_id = :t",
    "DELETE FROM task.task_delivery WHERE tenant_id = :t",
    "DELETE FROM task.task_submission WHERE tenant_id = :t",
    "UPDATE task.task_schedule SET current_revision_id = NULL WHERE tenant_id = :t",
    "DELETE FROM task.task_schedule_draft WHERE tenant_id = :t",
    "DELETE FROM task.task_schedule_revision WHERE tenant_id = :t",
    "DELETE FROM task.task_execution WHERE tenant_id = :t",
    "DELETE FROM task.task_schedule WHERE tenant_id = :t",
    "DELETE FROM task.delivery_route WHERE tenant_id = :t",
)


async def _purge_task_tenant() -> None:
    session_factory = worker_session_factory()
    for statement in TASK_TENANT_CLEANUP:
        async with session_factory() as session:
            await session.execute(text(statement), {"t": TENANT})
            await session.commit()


@pytest.fixture(autouse=True)
async def clean_task_schema(database_guard: None) -> AsyncGenerator[None, None]:
    await _purge_task_tenant()
    yield
    await _purge_task_tenant()


def _client() -> WorkerTaskClient:
    return WorkerTaskClient(
        "http://worker",
        service_token=TOKEN,
        transport=httpx.ASGITransport(app=worker_app),
    )


def _async_skill() -> ResolvedSkill:
    return ResolvedSkill(
        skill_id=uuid.uuid4(),
        artifact_id=uuid.uuid4(),
        key="policy_check",
        name="Policy Check",
        description="checks policy",
        version="1.0.0",
        checksum="sha256:" + "c" * 64,
        storage_key="skills/policy_check.zip",
        execution_mode=SkillExecutionMode.ASYNC,
        frontmatter={
            "invocation_contract": {
                "version": 1,
                "invocation": "stdin",
                "schema": {
                    "type": "object",
                    "properties": {
                        "day": {"type": "string", "format": "date"},
                        "customers": {"type": "array", "items": {"type": "string"}},
                        "actor_user_id": {"type": "string"},
                    },
                    "required": [],
                    "additionalProperties": False,
                },
            }
        },
        source="ADMIN",
    )


def _submission_context(
    *,
    actor_user_id: uuid.UUID,
    skill: ResolvedSkill,
    delivery_route: DeliveryRouteInput | None,
) -> TaskSubmissionContext:
    return TaskSubmissionContext(
        tenant_id=TENANT,
        actor_user_id=actor_user_id,
        agent=ResolvedAgent(id=uuid.uuid4(), key="agent", revision=1, instructions="hi", runtime_config={}),
        model=ResolvedModel(
            id=uuid.uuid4(),
            revision=1,
            model_id="gpt-4o-mini",
            base_url="http://model-gateway.internal/v1",
            params={},
        ),
        skills=(skill,),
        mcp_servers=(),
        source_run_id=uuid.uuid4(),
        delivery_route=delivery_route,
    )


def _tool_set(
    *,
    skill: ResolvedSkill,
    client: WorkerTaskClient,
    context: TaskSubmissionContext,
    extra_skills: tuple[ResolvedSkill, ...] = (),
) -> SkillToolSet:
    tmp = Path(tempfile.mkdtemp())
    store = NfsArtifactStore(tmp / "nfs")
    store.root.mkdir(parents=True, exist_ok=True)
    cache = SkillArtifactCache(store, tmp / "cache")
    return SkillToolSet(
        cache=cache,
        skills=[*extra_skills, skill],
        timeout_sec=10.0,
        task_client=_durable_client(client),
        task_context=context,
    )


def _durable_client(client):
    from muad_agent_runtime.application.async_tools.control_dispatcher import ControlDispatcher
    from muad_agent_runtime.application.async_tools.operations import DurableTaskSubmitter

    return DurableTaskSubmitter(
        worker_session_factory(),
        ControlDispatcher(worker_session_factory(), client._client, "http://worker", service_token=TOKEN),
    )


async def _call_tool(
    tool_set: SkillToolSet, name: str, arguments: dict[str, object], *, call_id=None
) -> dict[str, Any]:
    from muad_agent_runtime.infrastructure.models.runtime import RunRecord
    from muad_contracts import ResolveDefinitionResponse

    from tests.async_tool_submissions import seed_submission

    context = tool_set._task_context
    async with worker_session_factory()() as session:
        exists = await session.get(RunRecord, context.source_run_id)
    if exists is None:
        await seed_submission(
            worker_session_factory(),
            resolved=ResolveDefinitionResponse(
                agent=context.agent,
                model=context.model,
                skills=list(context.skills),
                mcp_servers=list(context.mcp_servers),
            ),
            tenant=context.tenant_id,
            actor=context.actor_user_id,
            run_id=context.source_run_id,
            pending_limit=4,
        )
    if name == EXECUTE_SKILL_TOOL:
        name = EXECUTE_TOOL
        arguments = {"target": "skill", **arguments}
    definition = tool_set.registry().get(name)
    assert definition.handler is not None
    call = ToolCall(
        call_id=call_id or f"call-{uuid.uuid4()}",
        name=name,
        arguments=arguments,
    )
    try:
        result = await definition.handler(arguments, call=call)
    except ToolError as exc:
        return cast(dict[str, Any], json.loads(exc.content()))
    return cast(dict[str, Any], json.loads(result.content))


async def _task_row(task_id: uuid.UUID) -> TaskExecution:
    async with worker_session_factory()() as session:
        row = await session.get(TaskExecution, task_id)
    assert row is not None
    return row


async def test_b123_async_skill_tool_submits_task_with_frozen_snapshot() -> None:
    skill = _async_skill()
    actor = uuid.uuid4()
    client = _client()
    context = _submission_context(
        actor_user_id=actor,
        skill=skill,
        delivery_route=DeliveryRouteInput(channel="WECOM", bot_id="bot-1", external_user_id="wotv-1"),
    )
    tool_set = _tool_set(skill=skill, client=client, context=context)
    try:
        payload = await _call_tool(
            tool_set, EXECUTE_SKILL_TOOL, {"skill_key": skill.key, "input": {"customers": ["A"]}}
        )
    finally:
        await client.aclose()

    assert payload["status"] == "SUBMITTED"
    task = await _task_row(uuid.UUID(payload["task_id"]))
    assert task.status == "QUEUED"
    assert task.tenant_id == TENANT
    assert task.actor_user_id == actor, "actor 必须来自已认证 Run 上下文"
    assert task.trigger_type == "IMMEDIATE"
    assert task.task_type == "SKILL"
    assert task.source_run_id == context.source_run_id
    assert task.skill_id == skill.skill_id
    assert task.skill_artifact_id == skill.artifact_id
    assert task.snapshot_hash.startswith("sha256:")
    assert task.execution_snapshot_json["skills"][0]["artifact_id"] == str(skill.artifact_id)
    assert task.skill_execution_mode == str(skill.execution_mode)
    assert task.completion_mode == str(CompletionMode.DETACH), "Run 侧 ASYNC 恒为后台任务"
    async with worker_session_factory()() as session:
        deliveries = list(
            await session.scalars(
                select(TaskDelivery).where(TaskDelivery.execution_id == task.id)
            )
        )
    assert len(deliveries) == 1, "后台任务按 Run 的 delivery route 独立投递"
    assert deliveries[0].delivery_kind == "PUSH"
    assert task.max_attempts == 1
    assert task.deadline_at > datetime.now(UTC), "后台任务按 Task deadline 收尾，不再依赖 Run 期限"


async def test_b123_multi_skill_agent_freezes_only_submitted_skill() -> None:
    """Agent 绑定多个 Skill 时，快照只冻结本次提交的 Skill（防止 Worker 执行错对象）。"""
    first = _async_skill().model_copy(update={"key": "first_skill"})
    target = _async_skill().model_copy(update={"key": "target_skill"})
    client = _client()
    context = replace(
        _submission_context(actor_user_id=uuid.uuid4(), skill=target, delivery_route=None),
        skills=(first, target),
    )
    tool_set = _tool_set(skill=target, client=client, context=context, extra_skills=(first,))
    try:
        payload = await _call_tool(tool_set, EXECUTE_SKILL_TOOL, {"skill_key": target.key, "input": {}})
    finally:
        await client.aclose()

    task = await _task_row(uuid.UUID(payload["task_id"]))
    assert task.skill_artifact_id == target.artifact_id
    assert [entry["artifact_id"] for entry in task.execution_snapshot_json["skills"]] == [
        str(target.artifact_id)
    ]


async def test_b123_equal_inputs_have_distinct_calls_and_retry_reuses_operation() -> None:
    skill = _async_skill()
    client = _client()
    context = _submission_context(actor_user_id=uuid.uuid4(), skill=skill, delivery_route=None)
    tool_set = _tool_set(skill=skill, client=client, context=context)
    try:
        first = await _call_tool(
            tool_set,
            EXECUTE_SKILL_TOOL,
            {"skill_key": skill.key, "input": {"customers": ["A"]}},
            call_id="first",
        )
        second = await _call_tool(
            tool_set,
            EXECUTE_SKILL_TOOL,
            {"skill_key": skill.key, "input": {"customers": ["A"]}},
            call_id="second",
        )
        replay = await _call_tool(
            tool_set,
            EXECUTE_SKILL_TOOL,
            {"skill_key": skill.key, "input": {"customers": ["A"]}},
            call_id="first",
        )
    finally:
        await client.aclose()

    assert first["task_id"] != second["task_id"]
    assert replay["operation_id"] == first["operation_id"] and replay["task_id"] == first["task_id"]
    async with worker_session_factory()() as session:
        total = (
            await session.execute(
                select(func.count()).select_from(TaskExecution).where(TaskExecution.tenant_id == TENANT)
            )
        ).scalar_one()
    assert total == 2


async def test_b123_query_and_cancel_read_back_through_worker_http() -> None:
    skill = _async_skill()
    client = _client()
    context = _submission_context(actor_user_id=uuid.uuid4(), skill=skill, delivery_route=None)
    tool_set = _tool_set(skill=skill, client=client, context=context)
    try:
        submitted = await _call_tool(tool_set, EXECUTE_SKILL_TOOL, {"skill_key": skill.key, "input": {}})
        task_id = uuid.UUID(submitted["task_id"])

        detail = await client.get_task(tenant_id=TENANT, task_id=task_id)
        assert detail["task_id"] == str(task_id)
        assert detail["status"] == "QUEUED"

        cancelled = await client.cancel_task(tenant_id=TENANT, task_id=task_id)
        assert cancelled["status"] == "CANCELLED"
        assert "CANCELLING" not in json.dumps(cancelled)

        refreshed = await client.get_task(tenant_id=TENANT, task_id=task_id)
        assert refreshed["status"] == "CANCELLED"
    finally:
        await client.aclose()


@dataclass
class _FakeMaterials:
    """只读依赖解析桩（Console 侧的解析由 TASK-010 覆盖）。"""

    materials: ScheduleAdmissionMaterials | None = None

    async def resolve_schedule_materials(
        self, tenant_id: str, payload: ScheduleAdmissionRequest
    ) -> ScheduleAdmissionMaterials:
        assert self.materials is not None, "test did not install schedule materials"
        return self.materials


@dataclass(frozen=True)
class _FakeSettings:
    async def fetch_snapshot(
        self, *, tenant_id: str, trace_id: str = ""
    ) -> PlatformSettingsSnapshot:
        return PlatformSettingsSnapshot(revision=1, settings={})


def _skill_materials(skill: ResolvedSkill) -> ScheduleAdmissionMaterials:
    return ScheduleAdmissionMaterials(
        mode=ScheduleMode.SKILL,
        agent=ResolvedAgent(id=uuid.uuid4(), key="agent", revision=1, instructions="be brief"),
        model=ScheduleModelMaterials(
            id=uuid.uuid4(), revision=1, model_id="gpt-4o-mini", protocol="OPENAI"
        ),
        skills=[skill],
        grants=ScheduleGrantMaterials(agent_access=True, skill_ids=[skill.skill_id]),
        settings_revision=1,
    )


@pytest.fixture(autouse=True)
def schedule_materials(monkeypatch: pytest.MonkeyPatch) -> _FakeMaterials:
    holder = _FakeMaterials()
    monkeypatch.setattr(worker_app.state, "schedule_materials_client", holder, raising=False)
    monkeypatch.setattr(worker_app.state, "platform_settings_client", _FakeSettings(), raising=False)
    return holder


async def _confirm_draft(
    client: WorkerTaskClient, *, actor: uuid.UUID, draft: dict[str, Any]
) -> dict[str, Any]:
    """surface 在用户显式动作后调 API-27；Runtime 侧没有这条通路。"""
    response = await client._client.post(
        f"/internal/schedule-drafts/{draft['draft_id']}/confirm",
        json={"draft_hash": draft["draft_hash"]},
        headers={
            "X-Internal-Service": TOKEN,
            "X-Tenant-Id": TENANT,
            "X-Actor-User-Id": str(actor),
            "X-Confirmation-Surface": "WECOM",
            "X-Confirmation-Ref": str(uuid.uuid4()),
            "Idempotency-Key": f"confirm-{uuid.uuid4()}",
        },
    )
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json()["data"])


async def test_b123_actor_cannot_be_forged_through_tool_arguments() -> None:
    skill = _async_skill()
    actor = uuid.uuid4()
    forged = uuid.uuid4()
    client = _client()
    context = _submission_context(actor_user_id=actor, skill=skill, delivery_route=None)
    tool_set = _tool_set(skill=skill, client=client, context=context)
    try:
        definition = tool_set.registry().get(EXECUTE_TOOL)
        schema = definition.input_schema
        assert {"target", "skill_key", "input", "argv"} <= set(schema["properties"])
        assert "completion_mode" not in schema["properties"], "Run 侧 ASYNC 恒为后台任务"
        for forged_field in ("actor_user_id", "tenant_id", "agent_id", "user_id"):
            assert forged_field not in json.dumps(schema)

        payload = await _call_tool(
            tool_set,
            EXECUTE_SKILL_TOOL,
            {"skill_key": skill.key, "input": {"actor_user_id": str(forged)}},
        )
    finally:
        await client.aclose()

    task = await _task_row(uuid.UUID(payload["task_id"]))
    assert task.actor_user_id == actor
    assert task.actor_user_id != forged


async def test_b123_runtime_task_client_does_not_touch_task_schema() -> None:
    source = Path("apps/agent-runtime/src/muad_agent_runtime/application/task_client.py").read_text()
    assert "muad_agent_worker" not in source
    assert "get_session" not in source
    assert "infrastructure.models" not in source


def test_b125_get_task_contract_relays_result_verbatim() -> None:
    """转发任务结果默认全文：这条提示是模型行为契约，不能被后续改描述时悄悄删掉。"""
    registry = ToolRegistry()
    BackgroundTaskToolSet(
        client=cast(Any, object()), context=cast(Any, object()), skills=[]
    ).register(registry)

    description = registry.get(GET_TASK_TOOL).description
    assert "verbatim" in description
    assert "do not summarize" in description
    assert "list_attachments(scope=task" in description


ROUTE = DeliveryRouteInput(channel="WECOM", bot_id="bot-1", external_user_id="wotv-1")


def _task_tools(
    client: WorkerTaskClient, context: TaskSubmissionContext, skill: ResolvedSkill
) -> ToolRegistry:
    registry = ToolRegistry()
    BackgroundTaskToolSet(client=client, context=context, skills=[skill]).register(registry)
    return registry


async def _invoke(registry: ToolRegistry, name: str, arguments: dict[str, object]) -> dict[str, Any]:
    handler = registry.get(name).handler
    assert handler is not None
    call = ToolCall(call_id=f"call-{uuid.uuid4()}", name=name, arguments=arguments)
    try:
        result = await handler(arguments, call=call)
    except ToolError as exc:
        return cast(dict[str, Any], json.loads(exc.content()))
    return cast(dict[str, Any], json.loads(result.content))


async def test_b123_draft_tool_uses_run_actor_and_never_creates_a_schedule(
    schedule_materials: _FakeMaterials,
) -> None:
    """Draft 工具：actor/agent/入口取自 Run 上下文；Draft 不生效，确认前 list 仍为空。"""
    skill = _async_skill()
    schedule_materials.materials = _skill_materials(skill)
    actor = uuid.uuid4()
    client = _client()
    context = _submission_context(actor_user_id=actor, skill=skill, delivery_route=ROUTE)
    registry = _task_tools(client, context, skill)
    arguments: dict[str, Any] = {
        "instruction": "每天 9 点用策略检查 Skill 跑一次",
        "timezone": "Asia/Shanghai",
        "recurrence": {"kind": "CRON", "cron": "0 9 * * 1"},
        "payload": {
            "kind": "SKILL_PLAN",
            "skill_key": skill.key,
            "input": {"day": "{{previous_day}}"},
            "argv": [],
        },
    }
    try:
        first = await _invoke(registry, DRAFT_SCHEDULE_TOOL, arguments)
        replay = await _invoke(registry, DRAFT_SCHEDULE_TOOL, arguments)
        listed = await _invoke(registry, LIST_SCHEDULES_TOOL, {})
    finally:
        await client.aclose()

    assert first["draft_id"] == replay["draft_id"]
    assert first["card"] == "UNPUBLISHED", "没有会话事件 sink 时必须显式降级"
    assert listed["items"] == [], "Draft 不生效：确认前不得出现 Schedule"
    async with worker_session_factory()() as session:
        draft = await session.get(TaskScheduleDraft, uuid.UUID(first["draft_id"]))
    assert draft is not None
    assert draft.owner_user_id == actor
    assert draft.agent_id == context.agent.id
    assert draft.payload_json["payload"]["skill_id"] == str(skill.skill_id)
async def test_b123_draft_tool_asks_for_the_user_timezone_when_no_default_exists() -> None:
    """直构路径没有平台设置源/冻结值时仍问用户，不自造服务端本地时区。"""
    skill = _async_skill()
    client = _client()
    context = _submission_context(actor_user_id=uuid.uuid4(), skill=skill, delivery_route=None)
    try:
        result = await _invoke(
            _task_tools(client, context, skill),
            DRAFT_SCHEDULE_TOOL,
            {
                "instruction": "每天 9 点提醒我开会",
                "recurrence": {"kind": "CRON", "cron": "0 9 * * *"},
                "payload": {"kind": "REMINDER", "text": "开会"},
            },
        )
    finally:
        await client.aclose()
    assert result["error"]["code"] == "SCHEDULE_TIMEZONE_REQUIRED"
async def test_b123_tools_cannot_touch_other_users_schedule(
    schedule_materials: _FakeMaterials,
) -> None:
    """别人的 Schedule 生命周期一律 404（不暴露存在性）。

    Task 侧的越权断言落在立即任务链上；该链的 create-task 端点正由 TASK-016 迁移，
    届时随它一起恢复（这里不重复一份会跟着 500 的断言）。
    """
    skill = _async_skill()
    schedule_materials.materials = _skill_materials(skill)
    owner, intruder = uuid.uuid4(), uuid.uuid4()
    client = _client()
    owner_ctx = _submission_context(actor_user_id=owner, skill=skill, delivery_route=ROUTE)
    intruder_tools = _task_tools(
        client, _submission_context(actor_user_id=intruder, skill=skill, delivery_route=ROUTE), skill
    )
    try:
        draft = await _invoke(
            _task_tools(client, owner_ctx, skill),
            DRAFT_SCHEDULE_TOOL,
            {
                "instruction": "每天 9 点用策略检查 Skill 跑一次",
                "timezone": "Asia/Shanghai",
                "recurrence": {"kind": "CRON", "cron": "0 9 * * *"},
                "payload": {"kind": "SKILL_PLAN", "skill_key": skill.key, "input": {}, "argv": []},
            },
        )
        schedule = await _confirm_draft(client, actor=owner, draft=draft)
        paused = await _invoke(
            intruder_tools, PAUSE_SCHEDULE_TOOL, {"schedule_id": schedule["schedule_id"]}
        )
        listed = await _invoke(intruder_tools, LIST_SCHEDULES_TOOL, {})
    finally:
        await client.aclose()

    # 跨用户一律「不存在」（不暴露存在性）：生命周期面用 SCHEDULE_NOT_FOUND。
    assert paused["error"]["code"] == "SCHEDULE_NOT_FOUND"
    assert listed["items"] == [], "别人的 Schedule 不出现在列表里"
    async with worker_session_factory()() as session:
        row = await session.get(TaskSchedule, uuid.UUID(schedule["schedule_id"]))
    assert row is not None and row.status == "ACTIVE", "越权动作不能改动对方的状态"


async def test_b123_run_channel_builds_delivery_route_for_background_tasks() -> None:
    """Run 持久化的入站渠道 → DeliveryRoute；缺接收方时不投递。"""
    route = delivery_route_of(
        {
            "type": "WECOM",
            "entry_id": str(uuid.uuid4()),
            "bot_id": "bot-1",
            "external_user_id": "wotv-1",
            "external_conversation_id": "c1",
        }
    )
    assert route == DeliveryRouteInput(
        channel="WECOM", bot_id="bot-1", external_user_id="wotv-1", external_conversation_id="c1"
    )
    assert delivery_route_of({"type": "WECOM", "entry_id": str(uuid.uuid4()), "bot_id": "bot-1"}) is None
    assert delivery_route_of(None) is None
