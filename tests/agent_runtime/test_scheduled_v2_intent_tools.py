"""TASK-017 · 自然语言 Schedule 意图工具：只产 Draft、专用确认卡片、显式澄清。

真实边界：真实 `BackgroundTaskToolSet` 工具入口（ToolRegistry handler）→ 真实 Worker HTTP
（ASGI，含服务身份/租户/actor/agent/入口头与 `Idempotency-Key` 台账）→ 真实 PostgreSQL
（`task.*` 与 `runtime.canonical_event`）。不 mock 存储，也不 mock 工具协议。

关键断言（design §2.3.2 / §2.3.9 / §3.2.8 / §3.5.4 / §3.5.10）：

1. 三种模式都经 Draft：REMINDER/SKILL/AGENTIC 只写一条 PENDING Draft，Schedule/Revision/
   Fire/Execution 一条都不多；Skill 与 allowlist 只认本 Run 的有效 skill。
2. 同回合写一条 canonical `schedule.draft.ready`（draft_id/hash/预览/过期），重复工具调用
   重放同一条持久事件。
3. 模型面没有任何 create/confirm 入口：确认只能由 surface 带用户动作来源调用 API-27，
   同轮自确认在协议层被拒绝（E-14）。
4. 缺时区就问用户（`SCHEDULE_TIMEZONE_REQUIRED`），多客户参数必须拆成多条 Draft（B-07）。
5. 生命周期目标按名字匹配：多条 = `SCHEDULE_AMBIGUOUS` + 候选，绝不静默挑一个；终态任务
   只能新建；同一次工具调用的重试携带同一个幂等身份。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import httpx
import pytest
import sqlalchemy as sa
from muad_agent_core.tools import ToolRegistry
from muad_agent_runtime.application.task_client import TaskSubmissionContext, WorkerTaskClient
from muad_agent_runtime.application.task_tools import (
    DRAFT_SCHEDULE_TOOL,
    DRAFT_SCHEDULE_UPDATE_TOOL,
    PAUSE_SCHEDULE_TOOL,
    RESUME_SCHEDULE_TOOL,
    BackgroundTaskToolSet,
)
from muad_agent_runtime.infrastructure.db import get_session_factory as runtime_session_factory
from muad_agent_runtime.infrastructure.models.runtime import CanonicalEvent, Conversation
from muad_agent_worker.application.ports import PlatformSettingsSnapshot
from muad_agent_worker.infrastructure.db import get_session_factory as worker_session_factory
from muad_agent_worker.main import app as worker_app
from muad_contracts import (
    DeliveryRouteInput,
    ResolvedAgent,
    ResolvedModel,
    ResolvedSkill,
    ScheduleAdmissionMaterials,
    ScheduleAdmissionRequest,
    ScheduleGrantMaterials,
    ScheduleMode,
    ScheduleModelMaterials,
    ScheduleToolMaterials,
    SkillExecutionMode,
)
from sqlalchemy import text

from agent_runtime.conftest import TenantContext
from tests.internal_service import TOKEN

pytestmark = pytest.mark.integration

TZ = "Asia/Shanghai"
SKILL_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
BASE_TIME = datetime(2026, 11, 15, 8, 0, 0, tzinfo=UTC)


# --------------------------------------------------------------------------------------
# 外部边界桩：Console 的设置快照与只读材料解析（它们的实现由 TASK-005/TASK-010 覆盖）
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FakeSettingsClient:
    async def fetch_snapshot(
        self, *, tenant_id: str, trace_id: str = ""
    ) -> PlatformSettingsSnapshot:
        return PlatformSettingsSnapshot(revision=1, settings={})


@dataclass(slots=True)
class FakeMaterials:
    result: ScheduleAdmissionMaterials
    calls: list[tuple[str, ScheduleAdmissionRequest]] = field(default_factory=list)

    async def resolve_schedule_materials(
        self, tenant_id: str, payload: ScheduleAdmissionRequest
    ) -> ScheduleAdmissionMaterials:
        self.calls.append((tenant_id, payload))
        return self.result


def _agent() -> ResolvedAgent:
    return ResolvedAgent(id=uuid.uuid4(), key="agent", revision=1, instructions="be brief")


def _model() -> ScheduleModelMaterials:
    return ScheduleModelMaterials(id=uuid.uuid4(), revision=1, model_id="gpt-4o-mini", protocol="OPENAI")


def _skill() -> ResolvedSkill:
    return ResolvedSkill(
        skill_id=SKILL_ID,
        artifact_id=uuid.uuid4(),
        key="weekly-report",
        name="周报",
        description="Render a weekly report",
        version="1.0.0",
        checksum="sha256:" + "c" * 64,
        storage_key="skills/weekly-report.tar.gz",
        execution_mode=SkillExecutionMode.SYNC,
        source="ADMIN",
    )


def reminder_materials() -> ScheduleAdmissionMaterials:
    return ScheduleAdmissionMaterials(
        mode=ScheduleMode.REMINDER, agent=_agent(), model=_model(), settings_revision=1
    )


def skill_materials() -> ScheduleAdmissionMaterials:
    return ScheduleAdmissionMaterials(
        mode=ScheduleMode.SKILL,
        agent=_agent(),
        model=_model(),
        skills=[_skill()],
        grants=ScheduleGrantMaterials(agent_access=True, skill_ids=[SKILL_ID]),
        settings_revision=1,
    )


def agentic_materials(*tools: ScheduleToolMaterials) -> ScheduleAdmissionMaterials:
    return ScheduleAdmissionMaterials(
        mode=ScheduleMode.AGENTIC,
        agent=_agent(),
        model=_model(),
        tools=list(tools),
        grants=ScheduleGrantMaterials(agent_access=True),
        settings_revision=1,
    )


# --------------------------------------------------------------------------------------
# 夹具与工具调用
# --------------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def install_worker_boundaries(monkeypatch: pytest.MonkeyPatch) -> FakeMaterials:
    """安装设置快照与材料解析桩；每个用例可按模式替换 `result`。"""
    materials = FakeMaterials(reminder_materials())
    monkeypatch.setattr(worker_app.state, "schedule_materials_client", materials, raising=False)
    monkeypatch.setattr(worker_app.state, "platform_settings_client", FakeSettingsClient(), raising=False)
    return materials


@pytest.fixture
async def conversation(tenant: TenantContext) -> AsyncIterator[uuid.UUID]:
    """Run 所在的真实会话：Draft 卡片事件必须写进这里。"""
    conversation_id = uuid.uuid4()
    async with runtime_session_factory()() as session:
        session.add(
            Conversation(
                id=conversation_id,
                tenant_id=tenant.tenant_id,
                user_id=tenant.platform_user_id,
                agent_id=tenant.agent_id,
                title="意图工具会话",
                status="ACTIVE",
                last_seq=0,
                last_message_at=BASE_TIME,
                create_time=BASE_TIME,
                update_time=BASE_TIME,
                is_deleted=False,
            )
        )
        await session.commit()
    try:
        yield conversation_id
    finally:
        await _purge_task_schema(tenant.tenant_id)


async def _purge_task_schema(tenant_id: str) -> None:
    """清理本租户的 task.* 行（循环 FK：先摘 Schedule→Revision 引用）。"""
    async with worker_session_factory()() as session, session.begin():
        await session.execute(
            text("UPDATE task.task_schedule SET current_revision_id = NULL WHERE tenant_id = :t"),
            {"t": tenant_id},
        )
        # 顺序按 FK：提交台账引用 Draft/Schedule，必须先行删除。
        for table in (
            "task_event",
            "task_submission",
            "task_schedule_draft",
            "task_schedule_revision",
            "task_schedule",
        ):
            await session.execute(text(f"DELETE FROM task.{table} WHERE tenant_id = :t"), {"t": tenant_id})


def _client() -> WorkerTaskClient:
    return WorkerTaskClient(
        "http://worker", service_token=TOKEN, transport=httpx.ASGITransport(app=worker_app)
    )


def _context(
    tenant: TenantContext,
    conversation_id: uuid.UUID,
    *,
    entry_channel: str | None = "WEBCHAT",
    run_id: uuid.UUID | None = None,
) -> TaskSubmissionContext:
    return TaskSubmissionContext(
        tenant_id=tenant.tenant_id,
        actor_user_id=tenant.platform_user_id,
        agent=ResolvedAgent(
            id=tenant.agent_id, key="agent", revision=1, instructions="hi", runtime_config={}
        ),
        model=ResolvedModel(
            id=uuid.uuid4(), revision=1, model_id="gpt-4o-mini", base_url="http://model", params={}
        ),
        skills=(_skill(),),
        source_run_id=run_id or uuid.uuid4(),
        conversation_id=conversation_id,
        entry_channel=entry_channel,
        # 聊天入口的 Run 带着这条消息自己的投递目标（创建会话）：PUSH 的 Draft 必须有它。
        delivery_route=(
            DeliveryRouteInput(
                channel=entry_channel,
                bot_id="bot-1",
                external_user_id="ext-1",
                external_conversation_id="conv-1",
            )
            if entry_channel == "WECOM"
            else None
        ),
        entry_id=uuid.uuid4(),
    )


def _registry(context: TaskSubmissionContext, client: WorkerTaskClient) -> ToolRegistry:
    registry = ToolRegistry()
    BackgroundTaskToolSet(
        client=client,
        context=context,
        skills=[_skill()],
        session_factory=runtime_session_factory,
    ).register(registry)
    return registry


async def _invoke(
    registry: ToolRegistry, name: str, arguments: dict[str, Any], *, call_id: str | None = None
) -> dict[str, Any]:
    definition = registry.get(name)
    assert definition is not None and definition.handler is not None, f"{name} is not registered"
    payload = await definition.handler(arguments, call_id=call_id or f"call-{uuid.uuid4()}")
    return cast(dict[str, Any], json.loads(payload))


def _internal_headers(tenant: TenantContext, **extra: str) -> dict[str, str]:
    headers = {
        "X-Internal-Service": TOKEN,
        "X-Tenant-Id": tenant.tenant_id,
        "X-Actor-User-Id": str(tenant.platform_user_id),
    }
    headers.update(extra)
    return headers


async def _confirm(tenant: TenantContext, draft: dict[str, Any]) -> dict[str, Any]:
    """模拟 surface 在**用户显式动作**后调用 API-27（Runtime 没有这条通路）。"""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=worker_app), base_url="http://worker"
    ) as http:
        response = await http.post(
            f"/internal/schedule-drafts/{draft['draft_id']}/confirm",
            json={"draft_hash": draft["draft_hash"]},
            headers=_internal_headers(
                tenant,
                **{
                    "X-Confirmation-Surface": "WEBCHAT",
                    "X-Confirmation-Ref": str(uuid.uuid4()),
                    "Idempotency-Key": f"confirm-{uuid.uuid4()}",
                },
            ),
        )
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json()["data"])


def _draft_args(**overrides: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "instruction": "每天 9 点提醒我开周会",
        "mode": "REMINDER",
        "timezone": TZ,
        "recurrence": {"kind": "CRON", "cron": "0 9 * * *"},
        "reminder_text": "开周会",
        "name": "周会提醒",
    }
    args.update(overrides)
    return args


async def _count(table: str, tenant_id: str) -> int:
    async with worker_session_factory()() as session:
        return int(
            (
                await session.execute(
                    text(f"SELECT count(*) FROM task.{table} WHERE tenant_id = :t"), {"t": tenant_id}
                )
            ).scalar_one()
        )


async def _draft_payloads(tenant_id: str) -> list[dict[str, Any]]:
    async with worker_session_factory()() as session:
        rows = await session.scalars(
            text(
                "SELECT payload_json FROM task.task_schedule_draft "
                "WHERE tenant_id = :t ORDER BY create_time"
            ),
            {"t": tenant_id},
        )
        return [dict(row) for row in rows.all()]


async def _card_events(tenant_id: str) -> list[CanonicalEvent]:
    async with runtime_session_factory()() as session:
        rows = await session.scalars(
            sa.select(CanonicalEvent)
            .where(CanonicalEvent.tenant_id == tenant_id)
            .order_by(CanonicalEvent.seq)
        )
        return list(rows.all())


# --------------------------------------------------------------------------------------
# 1. 三种模式都只产 Draft，并在同回合写确认卡片事件
# --------------------------------------------------------------------------------------


async def test_reminder_draft_has_no_side_effects_and_publishes_one_card(
    tenant: TenantContext, conversation: uuid.UUID
) -> None:
    client = _client()
    try:
        registry = _registry(_context(tenant, conversation), client)
        result = await _invoke(registry, DRAFT_SCHEDULE_TOOL, _draft_args())
    finally:
        await client.aclose()

    assert result["status"] == "PENDING"
    assert result["kind"] == "CREATE"
    assert result["mode"] == "REMINDER"
    assert result["preview"]["what"] == "开周会"
    assert TZ in result["preview"]["when"]
    assert result["preview"]["delivery"] == "NONE", "WebChat 入口不承诺主动推送"
    assert uuid.UUID(result["draft_id"])

    for table in ("task_schedule", "task_schedule_revision", "schedule_fire", "task_execution"):
        assert await _count(table, tenant.tenant_id) == 0, f"draft must not create {table}"
    assert await _count("task_schedule_draft", tenant.tenant_id) == 1

    rows = await _card_events(tenant.tenant_id)
    assert len(rows) == 1
    assert rows[0].stream_type == "schedule.draft.ready"
    assert rows[0].event_type == "SCHEDULE_DRAFT_READY"
    assert rows[0].run_id is not None
    assert rows[0].seq == result["card_event_seq"]
    assert rows[0].payload_json["draft_id"] == result["draft_id"]
    assert rows[0].payload_json["draft_hash"] == result["draft_hash"]
    expires_at = rows[0].payload_json["expires_at"].replace("Z", "+00:00")
    assert datetime.fromisoformat(expires_at) == datetime.fromisoformat(result["expires_at"])


async def test_repeated_draft_call_replays_the_same_draft_and_card(
    tenant: TenantContext, conversation: uuid.UUID
) -> None:
    """重复工具调用（同参数）不能长出第二条 Draft 或第二张卡。"""
    context = _context(tenant, conversation)
    client = _client()
    try:
        registry = _registry(context, client)
        first = await _invoke(registry, DRAFT_SCHEDULE_TOOL, _draft_args())
        second = await _invoke(registry, DRAFT_SCHEDULE_TOOL, _draft_args())
    finally:
        await client.aclose()

    assert second["draft_id"] == first["draft_id"]
    assert second["card_event_seq"] == first["card_event_seq"]
    assert await _count("task_schedule_draft", tenant.tenant_id) == 1
    assert len(await _card_events(tenant.tenant_id)) == 1


async def test_skill_draft_resolves_the_effective_skill(
    tenant: TenantContext, conversation: uuid.UUID, install_worker_boundaries: FakeMaterials
) -> None:
    install_worker_boundaries.result = skill_materials()
    client = _client()
    try:
        registry = _registry(_context(tenant, conversation), client)
        result = await _invoke(
            registry,
            DRAFT_SCHEDULE_TOOL,
            _draft_args(
                mode="SKILL",
                instruction="每天 9 点用周报 Skill 生成简报",
                skill_key="weekly-report",
                input_template={"body": "{{fire_date}} 的客户简报"},
                reminder_text=None,
            ),
        )
    finally:
        await client.aclose()

    assert result["mode"] == "SKILL"
    payload = (await _draft_payloads(tenant.tenant_id))[0]
    assert payload["execution_spec"]["skill_id"] == str(SKILL_ID)
    assert payload["execution_spec"]["input_template"] == {"body": "{{fire_date}} 的客户简报"}


async def test_agentic_draft_maps_skill_allowlist_and_keeps_budget(
    tenant: TenantContext, conversation: uuid.UUID, install_worker_boundaries: FakeMaterials
) -> None:
    install_worker_boundaries.result = agentic_materials(
        ScheduleToolMaterials(
            source="SKILL",
            tool_name="weekly-report",
            skill_id=SKILL_ID,
            catalog_revision=3,
        ),
        ScheduleToolMaterials(
            source="MCP", tool_name="customer.report", server_key="crm", catalog_revision=3
        ),
    )
    client = _client()
    try:
        registry = _registry(_context(tenant, conversation), client)
        result = await _invoke(
            registry,
            DRAFT_SCHEDULE_TOOL,
            _draft_args(
                mode="AGENTIC",
                instruction="每周一 9 点汇总 A 客户数据",
                reminder_text=None,
                goal="汇总 A 客户上周数据并生成周报",
                tool_allowlist=[
                    {"source": "SKILL", "skill_key": "weekly-report", "tool_name": "weekly-report"},
                    {"source": "MCP", "server_key": "crm", "tool_name": "customer.report"},
                ],
                budget={"max_steps": 6, "max_tool_calls": 8},
            ),
        )
    finally:
        await client.aclose()

    assert result["mode"] == "AGENTIC"
    assert result["preview"]["budget"] == {"max_steps": 6, "max_tool_calls": 8, "max_tokens": 0}
    spec = (await _draft_payloads(tenant.tenant_id))[0]["execution_spec"]
    assert spec["tool_allowlist"] == [
        {
            "source": "SKILL",
            "skill_id": str(SKILL_ID),
            "tool_name": "weekly-report",
            "server_key": None,
        },
        {"source": "MCP", "server_key": "crm", "tool_name": "customer.report", "skill_id": None},
    ]


async def test_wecom_entry_requests_push_delivery(
    tenant: TenantContext, conversation: uuid.UUID
) -> None:
    client = _client()
    try:
        registry = _registry(_context(tenant, conversation, entry_channel="WECOM"), client)
        result = await _invoke(registry, DRAFT_SCHEDULE_TOOL, _draft_args())
    finally:
        await client.aclose()
    assert result["preview"]["delivery"] == "PUSH"
    draft = (await _draft_payloads(tenant.tenant_id))[0]
    # 冻结创建时的目标（会话级），并记下入口只作审计的 source。
    assert draft["delivery_route"] == {
        "channel": "WECOM",
        "bot_id": "bot-1",
        "external_user_id": "ext-1",
        "external_conversation_id": "conv-1",
    }
    assert draft["context"]["source"]["origin_channel"] == "WECOM"


async def test_chat_entry_tells_the_model_the_confirm_command(
    tenant: TenantContext, conversation: uuid.UUID
) -> None:
    client = _client()
    try:
        registry = _registry(_context(tenant, conversation, entry_channel="WECOM"), client)
        result = await _invoke(registry, DRAFT_SCHEDULE_TOOL, _draft_args())
    finally:
        await client.aclose()

    assert result["confirm_command"] == f"/confirm {result['draft_id']}"
    assert f"/confirm {result['draft_id']}" in result["next_step"]


# --------------------------------------------------------------------------------------
# 2. 澄清：时区缺失/非法、复合指令必须拆分
# --------------------------------------------------------------------------------------


async def test_missing_timezone_asks_the_user_instead_of_defaulting(
    tenant: TenantContext, conversation: uuid.UUID
) -> None:
    client = _client()
    try:
        registry = _registry(_context(tenant, conversation), client)
        missing = await _invoke(registry, DRAFT_SCHEDULE_TOOL, _draft_args(timezone=None))
        invalid = await _invoke(registry, DRAFT_SCHEDULE_TOOL, _draft_args(timezone="Mars/Olympus"))
    finally:
        await client.aclose()

    assert missing["error"]["code"] == "SCHEDULE_TIMEZONE_REQUIRED"
    assert "ask the user" in missing["error"]["message"]
    assert invalid["error"]["code"] == "SCHEDULE_DRAFT_INVALID"
    assert invalid["error"]["data"]["reason"] == "INVALID_TIMEZONE"
    assert await _count("task_schedule_draft", tenant.tenant_id) == 0
    assert await _card_events(tenant.tenant_id) == []


async def test_multi_customer_parameters_must_be_split_into_separate_drafts(
    tenant: TenantContext, conversation: uuid.UUID
) -> None:
    client = _client()
    try:
        registry = _registry(_context(tenant, conversation), client)
        result = await _invoke(
            registry,
            DRAFT_SCHEDULE_TOOL,
            _draft_args(context={"parameters": {"customer": ["A", "B", "C"]}}),
        )
    finally:
        await client.aclose()

    assert result["error"]["code"] == "SCHEDULE_DRAFT_INVALID"
    assert result["error"]["data"] == {"reason": "MULTIPLE_TARGETS", "fields": ["customer"]}
    assert "one schedule" in result["error"]["message"]
    assert await _count("task_schedule_draft", tenant.tenant_id) == 0


# --------------------------------------------------------------------------------------
# 3. 模型面没有 create/confirm；同轮自确认在协议层被拒绝（E-14）
# --------------------------------------------------------------------------------------


async def test_model_surface_exposes_no_create_or_confirm_tool(
    tenant: TenantContext, conversation: uuid.UUID
) -> None:
    client = _client()
    try:
        registry = _registry(_context(tenant, conversation), client)
        names = {tool.name for tool in registry.model_tools()}
    finally:
        await client.aclose()

    assert DRAFT_SCHEDULE_TOOL in names and DRAFT_SCHEDULE_UPDATE_TOOL in names
    forbidden = {"create_schedule", "confirm_schedule", "confirm_schedule_draft", "activate_schedule"}
    assert names.isdisjoint(forbidden)
    # 客户端也没有确认能力：确认只能由 surface 走另一个端点。
    assert not hasattr(WorkerTaskClient, "confirm_schedule_draft")


async def test_self_confirmation_is_rejected_and_the_draft_stays_pending(
    tenant: TenantContext, conversation: uuid.UUID
) -> None:
    client = _client()
    try:
        registry = _registry(_context(tenant, conversation), client)
        draft = await _invoke(registry, DRAFT_SCHEDULE_TOOL, _draft_args())
        assert "cannot confirm" in draft["next_step"]
    finally:
        await client.aclose()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=worker_app), base_url="http://worker"
    ) as http:
        response = await http.post(
            f"/internal/schedule-drafts/{draft['draft_id']}/confirm",
            json={"draft_hash": draft["draft_hash"]},
            headers=_internal_headers(tenant, **{"Idempotency-Key": f"self-{uuid.uuid4()}"}),
        )

    assert response.status_code == 409
    assert response.json()["code"] == "SCHEDULE_CONFIRMATION_REQUIRED"
    assert await _count("task_schedule", tenant.tenant_id) == 0
    async with worker_session_factory()() as session:
        status = await session.scalar(
            text("SELECT status FROM task.task_schedule_draft WHERE tenant_id = :t"),
            {"t": tenant.tenant_id},
        )
    assert status == "PENDING"


# --------------------------------------------------------------------------------------
# 4. 生命周期：多匹配澄清、UPDATE 差异、终态只能新建、重试幂等身份
# --------------------------------------------------------------------------------------


async def _create_schedule(
    tenant: TenantContext, conversation: uuid.UUID, *, name: str, cron: str = "0 9 * * *"
) -> dict[str, Any]:
    client = _client()
    try:
        registry = _registry(_context(tenant, conversation), client)
        draft = await _invoke(
            registry, DRAFT_SCHEDULE_TOOL, _draft_args(name=name, recurrence={"kind": "CRON", "cron": cron})
        )
    finally:
        await client.aclose()
    assert "error" not in draft, draft
    return {**await _confirm(tenant, draft), "draft": draft, "name": name, "cron": cron}


async def test_ambiguous_target_returns_candidates_instead_of_guessing(
    tenant: TenantContext, conversation: uuid.UUID
) -> None:
    first = await _create_schedule(tenant, conversation, name="天气任务")
    second = await _create_schedule(tenant, conversation, name="天气任务-备份")
    client = _client()
    try:
        registry = _registry(_context(tenant, conversation), client)
        ambiguous = await _invoke(registry, PAUSE_SCHEDULE_TOOL, {"query": "天气任务"})
        missing = await _invoke(registry, PAUSE_SCHEDULE_TOOL, {"query": "不存在的任务"})
        exact = await _invoke(registry, PAUSE_SCHEDULE_TOOL, {"schedule_id": first["schedule_id"]})
    finally:
        await client.aclose()

    assert ambiguous["error"]["code"] == "SCHEDULE_AMBIGUOUS"
    candidate_ids = {item["schedule_id"] for item in ambiguous["error"]["data"]["candidates"]}
    assert candidate_ids == {first["schedule_id"], second["schedule_id"]}
    assert missing["error"]["code"] == "SCHEDULE_NOT_FOUND"
    assert exact["status"] == "PAUSED"


async def test_update_draft_shows_the_diff_and_binds_the_config_version(
    tenant: TenantContext, conversation: uuid.UUID
) -> None:
    created = await _create_schedule(tenant, conversation, name="周会提醒", cron="0 9 * * *")
    client = _client()
    try:
        registry = _registry(_context(tenant, conversation), client)
        draft = await _invoke(
            registry,
            DRAFT_SCHEDULE_UPDATE_TOOL,
            _draft_args(
                schedule_id=created["schedule_id"],
                recurrence={"kind": "CRON", "cron": "30 8 * * *"},
            ),
        )
    finally:
        await client.aclose()

    assert draft["kind"] == "UPDATE"
    diff = {item["field"]: item for item in draft["preview"]["diff"]}
    assert diff["recurrence"]["before"]["cron"] == "0 9 * * *"
    assert diff["recurrence"]["after"]["cron"] == "30 8 * * *"
    payload = (await _draft_payloads(tenant.tenant_id))[-1]
    assert payload["kind"] == "UPDATE"
    assert payload["base_schedule_id"] == created["schedule_id"]
    assert payload["base_config_version"] == created["config_version"]


async def test_ended_or_cancelled_schedule_can_only_be_recreated(
    tenant: TenantContext, conversation: uuid.UUID
) -> None:
    created = await _create_schedule(tenant, conversation, name="一次性提醒")
    client = _client()
    try:
        registry = _registry(_context(tenant, conversation), client)
        cancelled = await _invoke(
            registry, "cancel_schedule", {"schedule_id": created["schedule_id"]}
        )
        update = await _invoke(
            registry,
            DRAFT_SCHEDULE_UPDATE_TOOL,
            _draft_args(schedule_id=created["schedule_id"]),
        )
        resume = await _invoke(registry, RESUME_SCHEDULE_TOOL, {"schedule_id": created["schedule_id"]})
    finally:
        await client.aclose()

    assert cancelled["status"] == "CANCELLED"
    assert update["error"]["code"] == "SCHEDULE_STATE_INVALID"
    assert update["error"]["data"]["repair"] == "CREATE_NEW"
    assert resume["error"]["code"] == "SCHEDULE_STATE_INVALID"
    assert await _count("task_schedule_draft", tenant.tenant_id) == 1, "终态目标不产生新 Draft"


async def test_lifecycle_retry_reuses_one_idempotent_command(
    tenant: TenantContext, conversation: uuid.UUID
) -> None:
    created = await _create_schedule(tenant, conversation, name="每 2 小时巡检")
    context = _context(tenant, conversation)
    client = _client()
    try:
        registry = _registry(context, client)
        arguments = {"schedule_id": created["schedule_id"], "reason": "USER_REQUEST"}
        first = await _invoke(registry, "cancel_schedule", arguments, call_id="call-fixed")
        retry = await _invoke(registry, "cancel_schedule", arguments, call_id="call-fixed")
        other = await _invoke(registry, "cancel_schedule", arguments, call_id="call-other")
    finally:
        await client.aclose()

    assert first["status"] == "CANCELLED"
    assert retry == first, "同一次工具调用的重试必须重放首次结果"
    assert other["status"] == "CANCELLED", "服务本身对重复 cancel 幂等"
    # 服务对重复 cancel 本身是幂等的，因此「换一条命令」的判据是提交台账的身份数，
    # 而不是第二次调用的返回值：3 次请求只应占 2 个幂等身份。
    async with worker_session_factory()() as session:
        identities = int(
            (
                await session.execute(
                    text(
                        "SELECT count(*) FROM task.task_submission "
                        "WHERE tenant_id = :t AND endpoint = 'schedule-actions'"
                    ),
                    {"t": tenant.tenant_id},
                )
            ).scalar_one()
        )
    assert identities == 2
