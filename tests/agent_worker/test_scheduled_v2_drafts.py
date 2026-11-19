"""TASK-011 · Schedule Draft：可预览、可重放、**无副作用**（S-01/S-02/S-03、E-01、E-04、B-04）。

真实边界：真实 PostgreSQL（`task.*` 表与 partial unique/advisory 锁）、真实 FastAPI 路由
（受信服务身份 + 租户/actor/agent 请求头 + `Idempotency-Key` 台账）、真实 recurrence 计算。
依赖解析源（Console `schedule-admission-materials`）是**只读**内部端点，用例注入桩快照；
Console 侧的材料解析本身由 TASK-010 覆盖。

「无副作用」不靠读代码，靠**真实行数**证明：Draft 创建后 Schedule/Revision/Fire/Execution/
Delivery/Human/TaskEvent 一条都不能多，只有 `task_schedule_draft` 与首次响应快照各一行。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from muad_agent_worker.application.ports import PlatformSettingsSnapshot
from muad_agent_worker.application.schedule_drafts import (
    draft_payload,
    schedule_draft_hash,
)
from muad_agent_worker.infrastructure.models.task import (
    TaskSchedule,
    TaskScheduleDraft,
    TaskScheduleRevision,
)
from muad_agent_worker.main import app
from muad_agent_worker.scheduler.client import ScheduleMaterialsError
from muad_contracts import (
    ResolvedAgent,
    ResolvedSkill,
    ScheduleAdmissionMaterials,
    ScheduleAdmissionRequest,
    ScheduleDraftRequest,
    ScheduleGrantMaterials,
    ScheduleMode,
    ScheduleModelMaterials,
    ScheduleToolMaterials,
    SkillExecutionMode,
    SkillSource,
)

from agent_worker.conftest import TenantContext
from agent_worker.helpers import internal_service_headers

pytestmark = pytest.mark.integration

TZ = "Asia/Shanghai"
SKILL_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
MCP_TOOL = {"source": "MCP", "server_key": "crm", "tool_name": "customer.report"}


@dataclass(frozen=True, slots=True)
class FakeSettingsClient:
    """设置快照桩：Console 内部设置端点由 TASK-005 覆盖，这里只证明边界取值。"""

    settings: dict[str, Any]
    revision: int = 1

    async def fetch_snapshot(
        self, *, tenant_id: str, trace_id: str = ""
    ) -> PlatformSettingsSnapshot:
        return PlatformSettingsSnapshot(revision=self.revision, settings=self.settings)


class FakeMaterials:
    """只读依赖解析桩：可返回材料，也可抛出 Console 侧的稳定错误。"""

    def __init__(
        self,
        result: ScheduleAdmissionMaterials | None = None,
        *,
        error: ScheduleMaterialsError | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.calls: list[tuple[str, ScheduleAdmissionRequest]] = []

    async def resolve_schedule_materials(
        self, tenant_id: str, payload: ScheduleAdmissionRequest
    ) -> ScheduleAdmissionMaterials:
        self.calls.append((tenant_id, payload))
        if self.error is not None:
            raise self.error
        assert self.result is not None, "fake materials without a result"
        return self.result


def skill_materials(skill_id: uuid.UUID = SKILL_ID) -> ScheduleAdmissionMaterials:
    return ScheduleAdmissionMaterials(
        mode=ScheduleMode.SKILL,
        agent=ResolvedAgent(id=uuid.uuid4(), key="agent", revision=1, instructions="be brief"),
        model=ScheduleModelMaterials(
            id=uuid.uuid4(), revision=1, model_id="gpt-4o-mini", protocol="OPENAI"
        ),
        skills=[
            ResolvedSkill(
                skill_id=skill_id,
                artifact_id=uuid.uuid4(),
                key="weekly-report",
                name="周报",
                description="Render a weekly report",
                version="1.0.0",
                checksum="sha256:" + "c" * 64,
                storage_key="skills/weekly-report.tar.gz",
                execution_mode=SkillExecutionMode.SYNC,
                source=SkillSource.ADMIN,
            )
        ],
        grants=ScheduleGrantMaterials(agent_access=True, skill_ids=[skill_id]),
        settings_revision=1,
    )


def agentic_materials(
    allowlist: list[dict[str, Any]], *, extra: ScheduleToolMaterials | None = None
) -> ScheduleAdmissionMaterials:
    tools = [
        ScheduleToolMaterials(
            source=entry["source"],
            tool_name=entry["tool_name"],
            server_key=entry.get("server_key"),
            skill_id=uuid.UUID(entry["skill_id"]) if entry.get("skill_id") else None,
            catalog_revision=3,
        )
        for entry in allowlist
    ]
    if extra is not None:
        tools.append(extra)
    return ScheduleAdmissionMaterials(
        mode=ScheduleMode.AGENTIC,
        agent=ResolvedAgent(id=uuid.uuid4(), key="agent", revision=1, instructions="be brief"),
        model=ScheduleModelMaterials(
            id=uuid.uuid4(), revision=1, model_id="gpt-4o-mini", protocol="OPENAI"
        ),
        tools=tools,
        grants=ScheduleGrantMaterials(agent_access=True),
        settings_revision=1,
    )


def reminder_materials() -> ScheduleAdmissionMaterials:
    return ScheduleAdmissionMaterials(
        mode=ScheduleMode.REMINDER,
        agent=ResolvedAgent(id=uuid.uuid4(), key="agent", revision=1, instructions="be brief"),
        model=ScheduleModelMaterials(
            id=uuid.uuid4(), revision=1, model_id="gpt-4o-mini", protocol="OPENAI"
        ),
        settings_revision=1,
    )


#: PUSH Draft 的冻结投递目标（创建时的那一个会话）。
PUSH_ROUTE = {
    "channel": "WECOM",
    "bot_id": "bot-demo",
    "external_user_id": "wotv-001",
    "external_conversation_id": "chat-001",
}


def _with_push_route(body: dict[str, Any]) -> dict[str, Any]:
    """PUSH 必带目标：与 `delivery.route must be present exactly when intent is PUSH` 同一口径。"""
    delivery = body.setdefault("delivery", {"intent": "NONE"})
    if delivery.get("intent") == "PUSH" and "route" not in delivery:
        delivery["route"] = dict(PUSH_ROUTE)
    return body


def draft_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "instruction": "每天 8 点提醒我开会",
        "recurrence": {"kind": "CRON", "cron": "0 8 * * *", "timezone": TZ},
        "payload": {"kind": "REMINDER", "text": "开会"},
    }
    body.update(overrides)
    return _with_push_route(body)


def skill_body(**overrides: Any) -> dict[str, Any]:
    body = {
        "instruction": "每天 9 点用周报 Skill 生成简报",
        "recurrence": {"kind": "CRON", "cron": "0 9 * * *", "timezone": TZ},
        "payload": {
            "kind": "SKILL_PLAN",
            "skill_id": str(SKILL_ID),
            "input": {"body": "{{fire_date}} 的客户简报"},
        },
    }
    body.update(overrides)
    return _with_push_route(body)


def agentic_body(*, allowlist: list[str] | None = None, **overrides: Any) -> dict[str, Any]:
    body = {
        "instruction": "每周一 9 点汇总 A 客户数据",
        "recurrence": {"kind": "CRON", "cron": "0 9 * * 1", "timezone": TZ},
        "payload": {
            "kind": "AGENT_TURN",
            "prompt": "汇总 A 客户上周数据并生成周报",
            "tools_allow": ["MCP:crm:customer.report"] if allowlist is None else allowlist,
        },
    }
    body.update(overrides)
    return _with_push_route(body)


def headers(
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
        "Idempotency-Key": key or f"draft-{uuid.uuid4()}",
    }
    values.update(extra)
    return internal_service_headers(tenant.tenant_id, **values)


@pytest.fixture
def install(
    monkeypatch: pytest.MonkeyPatch,
) -> Any:
    """安装设置快照与依赖解析桩（两者都是外部边界，不 mock 应用内部）。"""

    def _install(
        materials: FakeMaterials, *, settings: dict[str, Any] | None = None
    ) -> FakeMaterials:
        monkeypatch.setattr(app.state, "schedule_materials_client", materials, raising=False)
        monkeypatch.setattr(
            app.state,
            "platform_settings_client",
            FakeSettingsClient(settings or {}),
            raising=False,
        )
        return materials

    return _install


TABLE_SIDE_EFFECTS = (
    "task_schedule",
    "task_schedule_revision",
    "schedule_fire",
    "task_execution",
    "task_delivery",
    "task_human_interaction",
    "task_event",
)


async def count_rows(tenant: TenantContext, table: str) -> int:
    async with tenant.session_factory() as session:
        total = await session.scalar(
            sa.text(f"SELECT count(*) FROM task.{table} WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant.tenant_id},
        )
    return int(total or 0)


async def assert_no_schedule_side_effects(tenant: TenantContext) -> None:
    for table in TABLE_SIDE_EFFECTS:
        assert await count_rows(tenant, table) == 0, f"{table} must stay empty"


async def load_draft(tenant: TenantContext, draft_id: uuid.UUID) -> TaskScheduleDraft:
    async with tenant.session_factory() as session:
        draft = await session.get(TaskScheduleDraft, draft_id)
    assert draft is not None
    return draft


async def seed_schedule(
    tenant: TenantContext,
    *,
    owner: uuid.UUID,
    agent: uuid.UUID,
    name: str = "天气任务",
    cron: str = "0 8 * * *",
    timezone: str = TZ,
    status: str = "ACTIVE",
) -> TaskSchedule:
    async with tenant.session_factory() as session:
        schedule = TaskSchedule(
            tenant_id=tenant.tenant_id,
            owner_user_id=owner,
            agent_id=agent,
            name=name,
            status=status,
            created_by=owner,
        )
        session.add(schedule)
        await session.flush()
        revision = TaskScheduleRevision(
            tenant_id=tenant.tenant_id,
            schedule_id=schedule.id,
            revision_no=1,
            mode="REMINDER",
            instruction_raw="每天 8 点提醒我开会",
            normalized_goal="开会",
            recurrence_kind="CRON",
            cron_expr=cron,
            timezone=timezone,
            # 与 Draft 载荷同形：差异预览只应报出真正改动的那一项。
            context_json={"parameters": {}, "output": {}},
            payload_kind="REMINDER", payload_json={"kind": "REMINDER", "text": "开会"},
            session_target="isolated",
            tools_policy_json={"tools_allow": []},
            created_by=owner,
        )
        session.add(revision)
        await session.flush()
        schedule.current_revision_id = revision.id
        await session.commit()
    return schedule


async def test_create_draft_is_pending_with_a_preview_and_no_side_effects(
    client: AsyncClient, tenant: TenantContext, install: Any
) -> None:
    materials = install(
        FakeMaterials(reminder_materials()),
        settings={"schedule": {"confirmation_ttl_sec": 1800}},
    )
    owner, agent = uuid.uuid4(), uuid.uuid4()

    response = await client.post(
        "/internal/schedule-drafts",
        json=draft_body(),
        headers=headers(tenant, actor=owner, agent=agent),
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "PENDING"
    assert data["draft_hash"].startswith("sha256:")
    assert len(data["preview"]["dependencies"]) == 0  # REMINDER 不依赖 Skill/Model
    assert data["preview"]["budget"] == {}
    assert data["preview"]["budget_source"] == "NONE"
    assert data["preview"]["mode"] == "REMINDER"
    assert data["preview"]["what"] == "开会"
    assert data["preview"]["when"] == f"CRON 0 8 * * * ({TZ})"
    assert data["preview"]["delivery"] == "NONE"
    assert datetime.fromisoformat(data["preview"]["first_fire_at"]) > datetime.now(UTC)
    assert materials.calls[0][0] == tenant.tenant_id

    draft = await load_draft(tenant, uuid.UUID(data["draft_id"]))
    assert draft.tenant_id == tenant.tenant_id
    assert draft.owner_user_id == owner
    assert draft.agent_id == agent
    assert draft.kind == "CREATE"
    assert draft.base_schedule_id is None and draft.base_config_version is None
    assert draft.status == "PENDING"
    assert draft.idempotency_key
    # draft_hash 与落库载荷是同一份事实：从 payload_json 重算必须一致。
    assert schedule_draft_hash(draft.payload_json) == draft.draft_hash == data["draft_hash"]
    assert draft.payload_json["delivery"]["intent"] == "NONE"
    assert draft.preview_json == data["preview"]
    # expires_at 来自配置的确认有效期，不是写死的常量。
    ttl = (draft.expires_at - draft.create_time).total_seconds()
    assert abs(ttl - 1800) <= 5

    await assert_no_schedule_side_effects(tenant)
    assert await count_rows(tenant, "task_schedule_draft") == 1
    assert await count_rows(tenant, "task_submission") == 1


async def test_same_key_same_payload_replays_the_first_draft(
    client: AsyncClient, tenant: TenantContext, install: Any
) -> None:
    install(FakeMaterials(reminder_materials()))
    owner, agent = uuid.uuid4(), uuid.uuid4()
    request_headers = headers(tenant, actor=owner, agent=agent, key="replay-key")

    first = await client.post(
        "/internal/schedule-drafts", json=draft_body(), headers=request_headers
    )
    second = await client.post(
        "/internal/schedule-drafts", json=draft_body(), headers=request_headers
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert second.json()["data"] == first.json()["data"]
    assert await count_rows(tenant, "task_schedule_draft") == 1
    assert await count_rows(tenant, "task_submission") == 1


async def test_same_key_with_different_payload_is_a_conflict_and_keeps_the_draft(
    client: AsyncClient, tenant: TenantContext, install: Any
) -> None:
    install(FakeMaterials(reminder_materials()))
    owner, agent = uuid.uuid4(), uuid.uuid4()
    request_headers = headers(tenant, actor=owner, agent=agent, key="conflict-key")

    first = await client.post(
        "/internal/schedule-drafts", json=draft_body(), headers=request_headers
    )
    before = await load_draft(tenant, uuid.UUID(first.json()["data"]["draft_id"]))

    other = draft_body(recurrence={"kind": "CRON", "cron": "0 10 * * *", "timezone": TZ})
    conflict = await client.post(
        "/internal/schedule-drafts", json=other, headers=request_headers
    )

    assert conflict.status_code == 409
    assert conflict.json()["code"] == "IDEMPOTENCY_MISMATCH"
    after = await load_draft(tenant, before.id)
    assert after.draft_hash == before.draft_hash
    assert after.payload_json == before.payload_json
    assert after.status == "PENDING"
    assert await count_rows(tenant, "task_schedule_draft") == 1


async def test_replay_wins_even_after_the_draft_was_cancelled_and_expired(
    client: AsyncClient, tenant: TenantContext, install: Any
) -> None:
    install(FakeMaterials(reminder_materials()))
    owner, agent = uuid.uuid4(), uuid.uuid4()
    request_headers = headers(tenant, actor=owner, agent=agent, key="consumed-key")
    first = await client.post(
        "/internal/schedule-drafts", json=draft_body(), headers=request_headers
    )
    draft_id = uuid.UUID(first.json()["data"]["draft_id"])
    async with tenant.session_factory() as session:
        await session.execute(
            sa.text(
                "UPDATE task.task_schedule_draft SET status = 'CANCELLED', "
                "expires_at = now() - interval '1 hour' WHERE id = :id"
            ),
            {"id": draft_id},
        )
        await session.commit()

    replay = await client.post(
        "/internal/schedule-drafts", json=draft_body(), headers=request_headers
    )

    assert replay.status_code == 200, replay.text
    assert replay.json()["data"] == first.json()["data"]
    draft = await load_draft(tenant, draft_id)
    assert draft.status == "CANCELLED"  # 重放不复活、不改写已存在的 Draft
    assert await count_rows(tenant, "task_schedule_draft") == 1


async def test_missing_timezone_asks_for_one_instead_of_guessing(
    client: AsyncClient, tenant: TenantContext, install: Any
) -> None:
    materials = install(FakeMaterials(reminder_materials()))
    response = await client.post(
        "/internal/schedule-drafts",
        json=draft_body(recurrence={"kind": "CRON", "cron": "0 8 * * *"}),
        headers=headers(tenant, actor=uuid.uuid4(), agent=uuid.uuid4()),
    )

    assert response.status_code == 422
    assert response.json()["code"] == "SCHEDULE_TIMEZONE_REQUIRED"
    assert materials.calls == []
    assert await count_rows(tenant, "task_schedule_draft") == 0


async def test_invalid_cron_is_an_explicit_draft_error(
    client: AsyncClient, tenant: TenantContext, install: Any
) -> None:
    install(FakeMaterials(reminder_materials()))
    response = await client.post(
        "/internal/schedule-drafts",
        json=draft_body(recurrence={"kind": "CRON", "cron": "not a cron", "timezone": TZ}),
        headers=headers(tenant, actor=uuid.uuid4(), agent=uuid.uuid4()),
    )

    assert response.status_code == 400
    assert response.json()["code"] == "SCHEDULE_DRAFT_INVALID"
    assert response.json()["data"]["reason"] == "RECURRENCE_INVALID"
    assert await count_rows(tenant, "task_schedule_draft") == 0


async def test_once_in_the_past_has_no_future_occurrence(
    client: AsyncClient, tenant: TenantContext, install: Any
) -> None:
    install(FakeMaterials(reminder_materials()))
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    response = await client.post(
        "/internal/schedule-drafts",
        json=draft_body(
            recurrence={"kind": "ONCE", "run_at": past, "timezone": TZ},
        ),
        headers=headers(tenant, actor=uuid.uuid4(), agent=uuid.uuid4()),
    )

    assert response.status_code == 400
    assert response.json()["code"] == "SCHEDULE_NO_FUTURE_OCCURRENCE"
    assert response.json()["data"]["reason"] == "NO_FUTURE_OCCURRENCE"
    assert await count_rows(tenant, "task_schedule_draft") == 0


async def test_missing_required_parameter_lists_what_is_missing(
    client: AsyncClient, tenant: TenantContext, install: Any
) -> None:
    install(FakeMaterials(reminder_materials()))
    body = draft_body()
    body.pop("payload")

    response = await client.post(
        "/internal/schedule-drafts",
        json=body,
        headers=headers(tenant, actor=uuid.uuid4(), agent=uuid.uuid4()),
    )

    assert response.status_code == 422
    assert response.json()["code"] == "SCHEDULE_PARAMETER_INCOMPLETE"
    assert response.json()["data"]["fields"] == ["payload"]


async def test_skill_unknown_template_variable_lists_the_variable(
    client: AsyncClient, tenant: TenantContext, install: Any
) -> None:
    install(FakeMaterials(skill_materials()))
    body = skill_body()
    body["payload"]["input"] = {"body": "{{custom}} 的简报"}

    response = await client.post(
        "/internal/schedule-drafts",
        json=body,
        headers=headers(tenant, actor=uuid.uuid4(), agent=uuid.uuid4()),
    )

    assert response.status_code == 422
    assert response.json()["code"] == "SCHEDULE_PARAMETER_INCOMPLETE"
    assert response.json()["data"]["unknown_variables"] == ["custom"]
    assert await count_rows(tenant, "task_schedule_draft") == 0


async def test_skill_draft_previews_the_resolved_skill_dependency(
    client: AsyncClient, tenant: TenantContext, install: Any
) -> None:
    materials = install(FakeMaterials(skill_materials()))
    response = await client.post(
        "/internal/schedule-drafts",
        json=skill_body(),
        headers=headers(tenant, actor=uuid.uuid4(), agent=uuid.uuid4()),
    )

    assert response.status_code == 200, response.text
    preview = response.json()["data"]["preview"]
    assert preview["dependencies"] == [f"SKILL:{SKILL_ID}:weekly-report"]
    assert preview["what"] == "每天 9 点用周报 Skill 生成简报"
    assert materials.calls[0][1].skill_id == SKILL_ID
    await assert_no_schedule_side_effects(tenant)


async def test_missing_skill_is_reported_as_a_dependency_error(
    client: AsyncClient, tenant: TenantContext, install: Any
) -> None:
    install(
        FakeMaterials(
            error=ScheduleMaterialsError(
                "skill missing",
                code="SCHEDULE_DEPENDENCY_MISSING",
                data={"admission_code": "SKILL_MISSING", "repair": "UPDATE_SCHEDULE_DEPENDENCY"},
            )
        )
    )
    response = await client.post(
        "/internal/schedule-drafts",
        json=skill_body(),
        headers=headers(tenant, actor=uuid.uuid4(), agent=uuid.uuid4()),
    )

    assert response.status_code == 422
    payload = response.json()
    assert payload["code"] == "SCHEDULE_DEPENDENCY_MISSING"
    assert payload["data"] == {
        "admission_code": "SKILL_MISSING",
        "repair": "UPDATE_SCHEDULE_DEPENDENCY",
    }
    assert await count_rows(tenant, "task_schedule_draft") == 0


async def test_revoked_grant_is_reported_as_grant_denied(
    client: AsyncClient, tenant: TenantContext, install: Any
) -> None:
    install(
        FakeMaterials(
            error=ScheduleMaterialsError(
                "grant revoked",
                code="SCHEDULE_GRANT_DENIED",
                data={"admission_code": "GRANT_REVOKED", "repair": "RESTORE_AGENT_GRANT"},
            )
        )
    )
    response = await client.post(
        "/internal/schedule-drafts",
        json=skill_body(),
        headers=headers(tenant, actor=uuid.uuid4(), agent=uuid.uuid4()),
    )

    assert response.status_code == 403
    assert response.json()["code"] == "SCHEDULE_GRANT_DENIED"
    assert await count_rows(tenant, "task_schedule_draft") == 0


async def test_unknown_agent_is_a_dependency_error_not_a_grant_denial(
    client: AsyncClient, tenant: TenantContext, install: Any
) -> None:
    install(
        FakeMaterials(
            error=ScheduleMaterialsError(
                "agent missing",
                code="AGENT_NOT_FOUND",
                data={"admission_code": "AGENT_MISSING", "repair": "ENABLE_AGENT"},
            )
        )
    )
    response = await client.post(
        "/internal/schedule-drafts",
        json=draft_body(),
        headers=headers(tenant, actor=uuid.uuid4(), agent=uuid.uuid4()),
    )

    assert response.status_code == 422
    assert response.json()["code"] == "SCHEDULE_DEPENDENCY_MISSING"
    assert response.json()["data"]["admission_code"] == "AGENT_MISSING"


async def test_transient_resolve_failure_is_admission_failed(
    client: AsyncClient, tenant: TenantContext, install: Any
) -> None:
    install(FakeMaterials(error=ScheduleMaterialsError("console down", code="RESOLVE_UNAVAILABLE")))
    response = await client.post(
        "/internal/schedule-drafts",
        json=draft_body(),
        headers=headers(tenant, actor=uuid.uuid4(), agent=uuid.uuid4()),
    )

    assert response.status_code == 422
    payload = response.json()
    assert payload["code"] == "SCHEDULE_ADMISSION_FAILED"
    assert payload["data"] == {"admission_code": "RESOLVE_UNAVAILABLE", "repair": "RETRY_LATER"}
    assert await count_rows(tenant, "task_schedule_draft") == 0


async def test_missing_dependency_source_fails_explicitly(
    client: AsyncClient, tenant: TenantContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """没有依赖源就没有「没有依赖」这个结论：显式失败，不静默放过 Preflight。"""
    monkeypatch.setattr(
        app.state, "platform_settings_client", FakeSettingsClient({}), raising=False
    )
    response = await client.post(
        "/internal/schedule-drafts",
        json=draft_body(),
        headers=headers(tenant, actor=uuid.uuid4(), agent=uuid.uuid4()),
    )

    assert response.status_code == 422
    assert response.json()["code"] == "SCHEDULE_ADMISSION_FAILED"
    assert response.json()["data"]["admission_code"] == "RESOLVE_UNAVAILABLE"
    assert await count_rows(tenant, "task_schedule_draft") == 0


async def test_agent_turn_without_tools_follows_the_owner(
    client: AsyncClient, tenant: TenantContext, install: Any
) -> None:
    """空 `tools_allow` = 跟随 owner，不再要求模型先列一份 allowlist。"""
    materials = install(FakeMaterials(agentic_materials([])))
    response = await client.post(
        "/internal/schedule-drafts",
        json=agentic_body(allowlist=[]),
        headers=headers(tenant, actor=uuid.uuid4(), agent=uuid.uuid4()),
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["preview"]["tools_allow"] == ["*"]
    assert response.json()["data"]["preview"]["dependencies"] == []
    assert materials.calls[0][1].tool_allowlist == []
    assert await count_rows(tenant, "task_schedule_draft") == 1


async def test_agentic_allowlist_must_be_resolved_by_materials(
    client: AsyncClient, tenant: TenantContext, install: Any
) -> None:
    install(FakeMaterials(agentic_materials([])))
    response = await client.post(
        "/internal/schedule-drafts",
        json=agentic_body(),
        headers=headers(tenant, actor=uuid.uuid4(), agent=uuid.uuid4()),
    )

    assert response.status_code == 422
    payload = response.json()
    assert payload["code"] == "SCHEDULE_DEPENDENCY_MISSING"
    assert payload["data"]["admission_code"] == "TOOL_MISSING"
    assert payload["data"]["dependencies"] == ["MCP:crm:customer.report"]


async def test_agentic_draft_previews_allowlist_and_budget(
    client: AsyncClient, tenant: TenantContext, install: Any
) -> None:
    install(FakeMaterials(agentic_materials([MCP_TOOL])))
    response = await client.post(
        "/internal/schedule-drafts",
        json=agentic_body(),
        headers=headers(tenant, actor=uuid.uuid4(), agent=uuid.uuid4()),
    )

    assert response.status_code == 200, response.text
    preview = response.json()["data"]["preview"]
    assert preview["dependencies"] == ["MCP:crm:customer.report"]
    assert preview["budget"] == {}
    assert preview["budget_source"] == "PLATFORM"
    assert preview["what"] == "汇总 A 客户上周数据并生成周报"
    await assert_no_schedule_side_effects(tenant)


async def test_push_target_is_independent_of_the_creation_entry(
    client: AsyncClient, tenant: TenantContext, install: Any
) -> None:
    """入口只作来源元数据：Web 入口也能推企微，企微入口也能只留 Web。"""
    install(FakeMaterials(reminder_materials()))
    owner, agent = uuid.uuid4(), uuid.uuid4()

    webchat_push = await client.post(
        "/internal/schedule-drafts",
        json=draft_body(delivery={"intent": "PUSH"}),
        headers=headers(tenant, actor=owner, agent=agent, **{"X-Entry-Channel": "WEBCHAT"}),
    )
    wecom_none = await client.post(
        "/internal/schedule-drafts",
        json=draft_body(delivery={"intent": "NONE"}),
        headers=headers(tenant, actor=owner, agent=agent, **{"X-Entry-Channel": "WECOM"}),
    )
    unknown_push = await client.post(
        "/internal/schedule-drafts",
        json=draft_body(delivery={"intent": "PUSH"}),
        headers=headers(tenant, actor=owner, agent=agent),
    )

    assert webchat_push.status_code == 200, webchat_push.text
    assert webchat_push.json()["data"]["preview"]["delivery"] == "PUSH"
    assert wecom_none.status_code == 200, wecom_none.text
    assert wecom_none.json()["data"]["preview"]["delivery"] == "NONE"
    assert unknown_push.status_code == 200, unknown_push.text
    assert await count_rows(tenant, "task_schedule_draft") == 3


async def test_push_to_a_non_outbound_channel_is_rejected(
    client: AsyncClient, tenant: TenantContext, install: Any
) -> None:
    """PUSH 只能选真实可出站的通道；WebChat 是 pull 面，不是投递目标。"""
    install(FakeMaterials(reminder_materials()))
    response = await client.post(
        "/internal/schedule-drafts",
        json=draft_body(
            delivery={
                "intent": "PUSH",
                "route": {
                    "channel": "WEBCHAT",
                    "bot_id": "bot-demo",
                    "external_user_id": "web-user",
                },
            }
        ),
        headers=headers(
            tenant, actor=uuid.uuid4(), agent=uuid.uuid4(), **{"X-Entry-Channel": "WECOM"}
        ),
    )

    assert response.status_code == 422
    assert response.json()["code"] == "SCHEDULE_DELIVERY_UNSUPPORTED"
    assert await count_rows(tenant, "task_schedule_draft") == 0


async def test_push_without_a_target_is_rejected_by_the_contract(
    client: AsyncClient, tenant: TenantContext, install: Any
) -> None:
    install(FakeMaterials(reminder_materials()))
    body = draft_body(delivery={"intent": "PUSH"})
    body["delivery"].pop("route")
    response = await client.post(
        "/internal/schedule-drafts",
        json=body,
        headers=headers(tenant, actor=uuid.uuid4(), agent=uuid.uuid4()),
    )

    assert response.status_code == 400
    assert await count_rows(tenant, "task_schedule_draft") == 0


async def test_secret_in_the_instruction_is_rejected_without_echo(
    client: AsyncClient, tenant: TenantContext, install: Any
) -> None:
    install(FakeMaterials(reminder_materials()))
    secret = "sk-live-abcdefghijklmnopqrstuvwxyz0123"
    response = await client.post(
        "/internal/schedule-drafts",
        json=draft_body(instruction=f"每天 8 点用 {secret} 提醒我"),
        headers=headers(tenant, actor=uuid.uuid4(), agent=uuid.uuid4()),
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["code"] == "SCHEDULE_DRAFT_INVALID"
    assert payload["data"]["reason"] == "SECRET_DETECTED"
    assert payload["data"]["field"] == "instruction"
    assert secret not in response.text
    assert await count_rows(tenant, "task_schedule_draft") == 0


async def test_schedule_quota_is_enforced_at_the_draft_boundary(
    client: AsyncClient, tenant: TenantContext, install: Any
) -> None:
    install(
        FakeMaterials(reminder_materials()),
        settings={"schedule": {"max_schedules_per_user": 1}},
    )
    owner, agent = uuid.uuid4(), uuid.uuid4()
    await seed_schedule(tenant, owner=owner, agent=agent)

    response = await client.post(
        "/internal/schedule-drafts",
        json=draft_body(),
        headers=headers(tenant, actor=owner, agent=agent),
    )

    assert response.status_code == 429
    assert response.json()["code"] == "SCHEDULE_QUOTA_EXCEEDED"
    assert await count_rows(tenant, "task_schedule_draft") == 0


async def test_minimum_interval_is_enforced_at_the_draft_boundary(
    client: AsyncClient, tenant: TenantContext, install: Any
) -> None:
    install(
        FakeMaterials(reminder_materials()),
        settings={"schedule": {"min_schedule_interval_sec": 120}},
    )
    response = await client.post(
        "/internal/schedule-drafts",
        json=draft_body(recurrence={"kind": "CRON", "cron": "* * * * *", "timezone": TZ}),
        headers=headers(tenant, actor=uuid.uuid4(), agent=uuid.uuid4()),
    )

    assert response.status_code == 429
    payload = response.json()
    assert payload["code"] == "SCHEDULE_QUOTA_EXCEEDED"
    assert payload["data"]["limit"] == "MIN_INTERVAL"


async def test_update_draft_binds_the_base_version_and_previews_the_diff(
    client: AsyncClient, tenant: TenantContext, install: Any
) -> None:
    install(FakeMaterials(reminder_materials()))
    owner, agent = uuid.uuid4(), uuid.uuid4()
    schedule = await seed_schedule(tenant, owner=owner, agent=agent)

    response = await client.post(
        "/internal/schedule-drafts",
        json=draft_body(
            kind="UPDATE",
            base_schedule_id=str(schedule.id),
            base_config_version=schedule.config_version,
            instruction="改成每天 9 点提醒我开会",
            recurrence={"kind": "CRON", "cron": "0 9 * * *", "timezone": TZ},
        ),
        headers=headers(tenant, actor=owner, agent=agent),
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["preview"]["diff"] == [
        {
            "field": "recurrence",
            "before": {
                "kind": "CRON",
                "cron": "0 8 * * *",
                "run_at": None,
                "timezone": TZ,
            },
            "after": {"kind": "CRON", "cron": "0 9 * * *", "run_at": None, "timezone": TZ},
        }
    ]
    draft = await load_draft(tenant, uuid.UUID(data["draft_id"]))
    assert draft.kind == "UPDATE"
    assert draft.base_schedule_id == schedule.id
    assert draft.base_config_version == schedule.config_version
    assert draft.payload_json["name"] == schedule.name  # UPDATE 缺省沿用原名
    # 更新不占新名额，也不改动现有 Schedule/Revision。
    assert await count_rows(tenant, "task_schedule") == 1
    assert await count_rows(tenant, "task_schedule_revision") == 1
    assert await count_rows(tenant, "task_schedule_draft") == 1
    for table in ("schedule_fire", "task_execution", "task_delivery", "task_event"):
        assert await count_rows(tenant, table) == 0


async def test_update_draft_rejects_another_users_schedule(
    client: AsyncClient, tenant: TenantContext, install: Any
) -> None:
    install(FakeMaterials(reminder_materials()))
    owner, agent, intruder = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    schedule = await seed_schedule(tenant, owner=owner, agent=agent)

    response = await client.post(
        "/internal/schedule-drafts",
        json=draft_body(
            kind="UPDATE",
            base_schedule_id=str(schedule.id),
            base_config_version=schedule.config_version,
        ),
        headers=headers(tenant, actor=intruder, agent=agent),
    )

    assert response.status_code == 404
    assert response.json()["code"] == "SCHEDULE_NOT_FOUND"
    assert await count_rows(tenant, "task_schedule_draft") == 0


async def test_update_draft_rejects_another_agents_schedule(
    client: AsyncClient, tenant: TenantContext, install: Any
) -> None:
    install(FakeMaterials(reminder_materials()))
    owner, agent = uuid.uuid4(), uuid.uuid4()
    schedule = await seed_schedule(tenant, owner=owner, agent=agent)

    response = await client.post(
        "/internal/schedule-drafts",
        json=draft_body(
            kind="UPDATE",
            base_schedule_id=str(schedule.id),
            base_config_version=schedule.config_version,
        ),
        headers=headers(tenant, actor=owner, agent=uuid.uuid4()),
    )

    assert response.status_code == 404
    assert await count_rows(tenant, "task_schedule_draft") == 0


async def test_identity_and_idempotency_key_come_from_the_trusted_context(
    client: AsyncClient, tenant: TenantContext, install: Any
) -> None:
    install(FakeMaterials(reminder_materials()))
    base = {
        "X-Tenant-Id": tenant.tenant_id,
        "Idempotency-Key": f"identity-{uuid.uuid4()}",
    }
    no_internal_service = await client.post(
        "/internal/schedule-drafts", json=draft_body(), headers=base
    )
    no_actor = await client.post(
        "/internal/schedule-drafts",
        json=draft_body(),
        headers=internal_service_headers(
            tenant.tenant_id,
            **{"X-Agent-Id": str(uuid.uuid4()), "Idempotency-Key": f"no-actor-{uuid.uuid4()}"},
        ),
    )
    no_agent = await client.post(
        "/internal/schedule-drafts",
        json=draft_body(),
        headers=internal_service_headers(
            tenant.tenant_id,
            **{"X-Actor-User-Id": str(uuid.uuid4()), "Idempotency-Key": f"no-agent-{uuid.uuid4()}"},
        ),
    )
    no_key = await client.post(
        "/internal/schedule-drafts",
        json=draft_body(),
        headers=internal_service_headers(
            tenant.tenant_id,
            **{"X-Actor-User-Id": str(uuid.uuid4()), "X-Agent-Id": str(uuid.uuid4())},
        ),
    )

    assert no_internal_service.status_code == 403
    assert no_actor.status_code == 403
    assert no_agent.status_code == 403
    assert no_key.status_code == 422
    assert no_key.json()["code"] == "COMMON_VALIDATION_ERROR"
    assert await count_rows(tenant, "task_schedule_draft") == 0


async def test_body_cannot_carry_an_owner_and_is_rejected(
    client: AsyncClient, tenant: TenantContext, install: Any
) -> None:
    install(FakeMaterials(reminder_materials()))
    response = await client.post(
        "/internal/schedule-drafts",
        json=draft_body(owner_user_id=str(uuid.uuid4())),
        headers=headers(tenant, actor=uuid.uuid4(), agent=uuid.uuid4()),
    )

    assert response.status_code == 400
    assert response.json()["code"] == "SCHEDULE_DRAFT_INVALID"
    assert await count_rows(tenant, "task_schedule_draft") == 0


def test_draft_hash_covers_every_field_that_changes_meaning() -> None:
    """同一份规范化载荷 → 同一 hash；预算/时间/delivery 任一变化 → 不同 hash。"""
    base = ScheduleDraftRequest.model_validate(agentic_body(delivery={"intent": "PUSH"}))
    same = ScheduleDraftRequest.model_validate(agentic_body(delivery={"intent": "PUSH"}))
    assert schedule_draft_hash(draft_payload(base)) == schedule_draft_hash(draft_payload(same))
    variants = [
        agentic_body(delivery={"intent": "PUSH"}, recurrence={
            "kind": "CRON", "cron": "0 10 * * 1", "timezone": TZ,
        }),
        agentic_body(delivery={"intent": "PUSH"}, payload={
            "kind": "AGENT_TURN",
            "prompt": "汇总 A 客户上周数据并生成周报",
            "tools_allow": ["MCP:crm:customer.report"],
            "budget_ref": "other",
        }),
        agentic_body(delivery={"intent": "NONE"}),
    ]
    hashes = {schedule_draft_hash(draft_payload(base))}
    for variant in variants:
        hashes.add(schedule_draft_hash(draft_payload(ScheduleDraftRequest.model_validate(variant))))
    assert len(hashes) == 4
