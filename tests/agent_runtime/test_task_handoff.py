"""B-123：Runtime Tool → Worker HTTP → PostgreSQL 的后台任务/定时任务交接（API-01/02/05）。

真实边界：真实 Runtime 工具入口（ToolRegistry handler）→ 真实 Worker HTTP（ASGI）
→ 真实 PostgreSQL；Runtime 不直接写 task schema。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import replace
from typing import Any, cast

import json
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from muad_agent_worker.infrastructure.db import get_session_factory as worker_session_factory
from muad_agent_worker.infrastructure.models.task import TaskExecution, TaskSchedule
from muad_agent_core.tools import ToolRegistry
from muad_agent_runtime.application.run_service import delivery_route_of
from muad_agent_runtime.application.skill_tools import EXECUTE_SKILL_TOOL, SkillToolSet
from muad_agent_worker.main import app as worker_app
from muad_agent_runtime.application.task_client import TaskSubmissionContext, WorkerTaskClient
from muad_agent_runtime.application.task_tools import (
    CANCEL_TASK_TOOL,
    CREATE_SCHEDULE_TOOL,
    DELETE_SCHEDULE_TOOL,
    GET_TASK_TOOL,
    LIST_SCHEDULES_TOOL,
    LIST_TASKS_TOOL,
    UPDATE_SCHEDULE_TOOL,
    BackgroundTaskToolSet,
)
from muad_artifact_store import NfsArtifactStore, SkillArtifactCache
from muad_contracts import (
    DeliveryRouteInput,
    ResolvedAgent,
    ResolvedModel,
    ResolvedSkill,
    SkillExecutionMode,
)
from sqlalchemy import func, select, text

TOKEN = "runtime-worker-token"
TENANT = "test-runtime-handoff"


@pytest.fixture(autouse=True)
def internal_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", TOKEN)


@pytest.fixture(autouse=True)
async def clean_task_schema(database_guard: None) -> AsyncGenerator[None, None]:
    session_factory = worker_session_factory()
    for statement in (
        "DELETE FROM task.task_event WHERE tenant_id = :t",
        "DELETE FROM task.task_submission WHERE tenant_id = :t",
        "DELETE FROM task.task_execution WHERE tenant_id = :t",
        "DELETE FROM task.task_schedule WHERE tenant_id = :t",
        "DELETE FROM task.delivery_route WHERE tenant_id = :t",
    ):
        async with session_factory() as session:
            await session.execute(text(statement), {"t": TENANT})
            await session.commit()
    yield
    for statement in (
        "DELETE FROM task.task_event WHERE tenant_id = :t",
        "DELETE FROM task.task_submission WHERE tenant_id = :t",
        "DELETE FROM task.task_execution WHERE tenant_id = :t",
        "DELETE FROM task.task_schedule WHERE tenant_id = :t",
        "DELETE FROM task.delivery_route WHERE tenant_id = :t",
    ):
        async with session_factory() as session:
            await session.execute(text(statement), {"t": TENANT})
            await session.commit()


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
        frontmatter={},
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
        agent=ResolvedAgent(
            id=uuid.uuid4(), key="agent", revision=1, instructions="hi", runtime_config={}
        ),
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
        task_client=client,
        task_context=context,
    )


async def _call_tool(tool_set: SkillToolSet, name: str, arguments: dict[str, object]) -> dict[str, Any]:
    definition = tool_set.registry().get(name)
    assert definition.handler is not None
    return cast(dict[str, Any], json.loads(await definition.handler(arguments)))


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
        delivery_route=DeliveryRouteInput(
            channel="WECOM", bot_id="bot-1", external_user_id="wotv-1"
        ),
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
    assert task.delivery_mode == "FINAL_ONLY"
    assert task.delivery_route_id is not None


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
        payload = await _call_tool(
            tool_set, EXECUTE_SKILL_TOOL, {"skill_key": target.key, "input": {}}
        )
    finally:
        await client.aclose()

    task = await _task_row(uuid.UUID(payload["task_id"]))
    assert task.skill_artifact_id == target.artifact_id
    assert [entry["artifact_id"] for entry in task.execution_snapshot_json["skills"]] == [
        str(target.artifact_id)
    ]


async def test_b123_repeated_async_submit_reuses_same_task() -> None:
    skill = _async_skill()
    client = _client()
    context = _submission_context(actor_user_id=uuid.uuid4(), skill=skill, delivery_route=None)
    tool_set = _tool_set(skill=skill, client=client, context=context)
    try:
        first = await _call_tool(
            tool_set, EXECUTE_SKILL_TOOL, {"skill_key": skill.key, "input": {"customers": ["A"]}}
        )
        second = await _call_tool(
            tool_set, EXECUTE_SKILL_TOOL, {"skill_key": skill.key, "input": {"customers": ["A"]}}
        )
    finally:
        await client.aclose()

    assert first["task_id"] == second["task_id"], "同一 Run 同输入重试不得重复建 Task"
    async with worker_session_factory()() as session:
        total = (
            await session.execute(
                select(func.count())
                .select_from(TaskExecution)
                .where(TaskExecution.tenant_id == TENANT)
            )
        ).scalar_one()
    assert total == 1


async def test_b123_query_and_cancel_read_back_through_worker_http() -> None:
    skill = _async_skill()
    client = _client()
    context = _submission_context(actor_user_id=uuid.uuid4(), skill=skill, delivery_route=None)
    tool_set = _tool_set(skill=skill, client=client, context=context)
    try:
        submitted = await _call_tool(
            tool_set, EXECUTE_SKILL_TOOL, {"skill_key": skill.key, "input": {}}
        )
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


async def test_b123_schedule_create_retry_is_idempotent_and_manageable() -> None:
    skill = _async_skill()
    actor = uuid.uuid4()
    client = _client()
    try:
        agent_id = uuid.uuid4()
        first = await client.create_schedule(
            tenant_id=TENANT,
            agent_id=agent_id,
            actor_user_id=actor,
            intent_key="policy_check",
            skill=skill,
            input_template={"customer": "A"},
            schedule={"type": "CRON", "cron": "0 9 * * *", "timezone": "Asia/Shanghai"},
            delivery_route=DeliveryRouteInput(
                channel="WECOM", bot_id="bot-1", external_user_id="wotv-1"
            ),
            idempotency_key="run:schedule:1",
        )
        replay = await client.create_schedule(
            tenant_id=TENANT,
            agent_id=agent_id,
            actor_user_id=actor,
            intent_key="policy_check",
            skill=skill,
            input_template={"customer": "A"},
            schedule={"type": "CRON", "cron": "0 9 * * *", "timezone": "Asia/Shanghai"},
            delivery_route=DeliveryRouteInput(
                channel="WECOM", bot_id="bot-1", external_user_id="wotv-1"
            ),
            idempotency_key="run:schedule:1",
        )
    finally:
        await client.aclose()

    assert first["schedule_id"] == replay["schedule_id"]
    async with worker_session_factory()() as session:
        total = (
            await session.execute(
                select(func.count())
                .select_from(TaskSchedule)
                .where(TaskSchedule.tenant_id == TENANT)
            )
        ).scalar_one()
    assert total == 1

    schedule_id = uuid.UUID(first["schedule_id"])
    client = _client()
    try:
        paused = await client.update_schedule(
            tenant_id=TENANT,
            schedule_id=schedule_id,
            actor_user_id=actor,
            schedule=None,
            name="renamed",
        )
        assert paused["name"] == "renamed"
        deleted = await client.delete_schedule(
            tenant_id=TENANT, schedule_id=schedule_id, actor_user_id=actor
        )
        assert deleted == {"schedule_id": str(schedule_id), "deleted": True}
    finally:
        await client.aclose()


async def test_b123_actor_cannot_be_forged_through_tool_arguments() -> None:
    skill = _async_skill()
    actor = uuid.uuid4()
    forged = uuid.uuid4()
    client = _client()
    context = _submission_context(actor_user_id=actor, skill=skill, delivery_route=None)
    tool_set = _tool_set(skill=skill, client=client, context=context)
    try:
        definition = tool_set.registry().get(EXECUTE_SKILL_TOOL)
        schema = definition.input_schema
        assert set(schema["properties"]) == {"skill_key", "input"}
        assert "actor_user_id" not in json.dumps(schema)

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
    source = (
        Path("apps/agent-runtime/src/muad_agent_runtime/application/task_client.py").read_text()
    )
    assert "muad_agent_worker" not in source
    assert "get_session" not in source
    assert "infrastructure.models" not in source


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
    return cast(dict[str, Any], json.loads(await handler(arguments)))


async def test_b123_create_schedule_tool_uses_run_actor_and_route() -> None:
    """create_schedule Tool：actor/route 取自 Run 上下文，重试不重复建，list 只见本人。"""
    skill = _async_skill()
    actor = uuid.uuid4()
    client = _client()
    context = _submission_context(actor_user_id=actor, skill=skill, delivery_route=ROUTE)
    registry = _task_tools(client, context, skill)
    arguments: dict[str, object] = {
        "skill_key": skill.key,
        "input_template": {"day": "{{previous_day}}"},
        "schedule": {"type": "CRON", "cron": "0 9 * * 1", "timezone": "Asia/Shanghai"},
    }
    try:
        first = await _invoke(registry, CREATE_SCHEDULE_TOOL, arguments)
        replay = await _invoke(registry, CREATE_SCHEDULE_TOOL, arguments)
        listed = await _invoke(registry, LIST_SCHEDULES_TOOL, {})
    finally:
        await client.aclose()

    assert first["schedule_id"] == replay["schedule_id"]
    assert first["actor_user_id"] == str(actor)
    assert first["skill_id"] == str(skill.skill_id)
    assert [item["schedule_id"] for item in listed["items"]] == [first["schedule_id"]]
    async with worker_session_factory()() as session:
        row = await session.get(TaskSchedule, uuid.UUID(first["schedule_id"]))
    assert row is not None and row.delivery_route_id is not None


async def test_b123_create_schedule_tool_requires_delivery_route() -> None:
    skill = _async_skill()
    client = _client()
    context = _submission_context(actor_user_id=uuid.uuid4(), skill=skill, delivery_route=None)
    try:
        result = await _invoke(
            _task_tools(client, context, skill),
            CREATE_SCHEDULE_TOOL,
            {
                "skill_key": skill.key,
                "schedule": {"type": "CRON", "cron": "0 9 * * *", "timezone": "Asia/Shanghai"},
            },
        )
    finally:
        await client.aclose()
    assert result["error"]["code"] == "SCHEDULE_DELIVERY_ROUTE_REQUIRED"


async def test_b123_tools_cannot_touch_other_users_schedule_or_task() -> None:
    """别人的 Schedule 改/删 FORBIDDEN，别人的 Task 查/取消 NOT_FOUND。"""
    skill = _async_skill()
    owner, intruder = uuid.uuid4(), uuid.uuid4()
    client = _client()
    owner_ctx = _submission_context(actor_user_id=owner, skill=skill, delivery_route=ROUTE)
    intruder_tools = _task_tools(
        client, _submission_context(actor_user_id=intruder, skill=skill, delivery_route=ROUTE), skill
    )
    try:
        schedule = await _invoke(
            _task_tools(client, owner_ctx, skill),
            CREATE_SCHEDULE_TOOL,
            {
                "skill_key": skill.key,
                "schedule": {"type": "CRON", "cron": "0 9 * * *", "timezone": "Asia/Shanghai"},
            },
        )
        task = await client.submit_task(owner_ctx, skill=skill, input_data={"x": 1})
        update = await _invoke(
            intruder_tools, UPDATE_SCHEDULE_TOOL, {"schedule_id": schedule["schedule_id"], "name": "x"}
        )
        delete = await _invoke(
            intruder_tools, DELETE_SCHEDULE_TOOL, {"schedule_id": schedule["schedule_id"]}
        )
        peek = await _invoke(intruder_tools, GET_TASK_TOOL, {"task_id": task["task_id"]})
        cancel = await _invoke(intruder_tools, CANCEL_TASK_TOOL, {"task_id": task["task_id"]})
        listed = await _invoke(intruder_tools, LIST_TASKS_TOOL, {})
    finally:
        await client.aclose()

    assert update["error"]["code"] == "FORBIDDEN"
    assert delete["error"]["code"] == "FORBIDDEN"
    assert peek["error"]["code"] == "COMMON_NOT_FOUND"
    assert cancel["error"]["code"] == "COMMON_NOT_FOUND"
    assert listed["items"] == []
    assert (await _task_row(uuid.UUID(task["task_id"]))).status == "QUEUED"


async def test_b123_run_channel_builds_delivery_route_for_background_tasks() -> None:
    """Run 持久化的入站渠道 → DeliveryRoute；缺接收方时不投递。"""
    route = delivery_route_of(
        {"type": "WECOM", "bot_id": "bot-1", "external_user_id": "wotv-1", "external_conversation_id": "c1"}
    )
    assert route == DeliveryRouteInput(
        channel="WECOM", bot_id="bot-1", external_user_id="wotv-1", external_conversation_id="c1"
    )
    assert delivery_route_of({"type": "WECOM", "bot_id": "bot-1"}) is None
    assert delivery_route_of(None) is None
