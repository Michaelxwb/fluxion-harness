"""TASK-023 · Authoring / 试跑 / 提升端到端：业务意图只产计划，不选 mode。

真实边界：真实 Authoring 编译器、真实 Worker Draft/Trial/Confirm 路由、真实 PostgreSQL、
真实 Skill 子进程 + fake MSSP、真实 Runtime promotion 事实读取。模型只提供业务意图的
proposal；平台负责编译、校验、试跑与提升。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from muad_agent_runtime.api.deps import get_artifact_store as runtime_get_artifact_store
from muad_agent_runtime.application.schedule_authoring import (
    ScheduleAuthoringContext,
    compile_schedule_plan,
)
from muad_agent_runtime.application.schedule_promotion import (
    load_promotion_facts,
    promotion_proposal,
)
from muad_agent_runtime.infrastructure.db import get_session_factory as runtime_session_factory
from muad_agent_runtime.infrastructure.models.runtime import (
    CanonicalEvent,
    Conversation,
    RunRecord,
    ToolCallAudit,
)
from muad_agent_runtime.main import app as runtime_app
from muad_agent_worker.infrastructure.models.task import (
    TaskScheduleDraft,
    TaskScheduleRevision,
)
from muad_agent_worker.infrastructure.task_artifact_client import TaskArtifactClient
from muad_agent_worker.main import app as worker_app
from muad_agent_worker.worker.dispatch import TaskTypeExecutor
from muad_agent_worker.worker.executor import SkillTaskExecutor
from muad_agent_worker.worker.reminder import ReminderExecutor
from muad_artifact_store import NfsArtifactStore, SkillArtifactCache
from muad_contracts import (
    ResolvedAgent,
    ResolvedSkill,
    ScheduleAdmissionMaterials,
    ScheduleGrantMaterials,
    ScheduleMode,
    ScheduleModelMaterials,
    SkillExecutionMode,
    SkillSource,
)

from agent_worker.conftest import TEST_INTERNAL_SERVICE_TOKEN, TenantContext
from agent_worker.helpers import (
    FakeMaterialsClient,
    FakePlatformSettingsClient,
    internal_service_headers,
    run_worker_once,
    write_skill_archive,
)
from agent_worker.test_scheduled_v2_snapshot import make_materials
from tests.tools.mssp_support import PATH_SERVICE_INFO, running_mssp

pytestmark = pytest.mark.integration

TZ = "Asia/Shanghai"
SKILL_ID = uuid.UUID("5a1d0c1e-0000-4000-8000-000000023003")
SKILL_KEY = "daily-duty-report-mssp-local"
SKILL_ROOT = Path(__file__).resolve().parents[2] / "skills/daily-duty-report-mssp-local"
CALL_STARTED_AT = datetime(2026, 11, 18, 1, 0, tzinfo=UTC)
RECORDED_DATE = "2026-11-17"


def _skill(
    *,
    checksum: str,
    storage_key: str,
    entrypoint: str = "scripts/run.py",
) -> ResolvedSkill:
    return ResolvedSkill(
        skill_id=SKILL_ID,
        artifact_id=uuid.uuid4(),
        key=SKILL_KEY,
        name="本地值守日报",
        description="fake MSSP daily duty report",
        version="0.1.0",
        checksum=checksum,
        storage_key=storage_key,
        execution_mode=SkillExecutionMode.SYNC,
        frontmatter={
            "entrypoint": entrypoint,
            "invocation_contract": {
                "version": 1,
                "invocation": "argv",
                "schema": {
                    "type": "object",
                    "properties": {
                        "company-id": {"type": "string", "pattern": "^[0-9]+$"},
                        "date": {"type": "string", "format": "date"},
                        "no-wecom-push": {"type": "boolean"},
                    },
                    "required": ["company-id"],
                    "additionalProperties": False,
                },
            },
        },
        source=SkillSource.ADMIN,
    )


def _skill_materials(
    *,
    checksum: str,
    storage_key: str,
    entrypoint: str = "scripts/run.py",
) -> ScheduleAdmissionMaterials:
    skill = _skill(checksum=checksum, storage_key=storage_key, entrypoint=entrypoint)
    return ScheduleAdmissionMaterials(
        mode=ScheduleMode.SKILL,
        agent=ResolvedAgent(id=uuid.uuid4(), key="agent", revision=1, instructions="be brief"),
        model=ScheduleModelMaterials(
            id=uuid.uuid4(), revision=1, model_id="gpt-4o-mini", protocol="OPENAI"
        ),
        skills=[skill],
        grants=ScheduleGrantMaterials(agent_access=True, skill_ids=[SKILL_ID]),
        settings_revision=1,
    )


def _context(skill: ResolvedSkill | None) -> ScheduleAuthoringContext:
    return ScheduleAuthoringContext(
        skills={skill.key: skill} if skill is not None else {},
        mcp_servers=(),
        entry_channel="WEBCHAT",
    )


def _draft_body(authoring: Any, instruction: str, *, name: str) -> dict[str, Any]:
    assert authoring.plan is not None
    assert authoring.recurrence is not None
    assert authoring.timezone is not None
    return {
        "plan": authoring.plan.model_dump(mode="json"),
        "instruction": instruction,
        "recurrence": {**authoring.recurrence, "timezone": authoring.timezone},
        "name": name,
    }


def _package_skill(nfs_root: Path, platform: Any, *, entrypoint: str) -> tuple[str, str]:
    storage_key = f"skills/e2e/{uuid.uuid4()}.zip"
    config = json.loads((SKILL_ROOT / "config/api_config.json").read_text(encoding="utf-8"))
    config["base_url"] = platform.base_url
    manifest = json.loads((SKILL_ROOT / "muad.skill.json").read_text(encoding="utf-8"))
    manifest["entrypoint"] = entrypoint
    checksum = write_skill_archive(
        nfs_root,
        storage_key,
        SKILL_ROOT,
        overrides={
            "config/api_config.json": json.dumps(config, ensure_ascii=False),
            "muad.skill.json": json.dumps(manifest, ensure_ascii=False),
        },
    )
    return checksum, storage_key


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


async def _test_run(
    client: httpx.AsyncClient,
    tenant: TenantContext,
    draft_id: str,
    *,
    actor: uuid.UUID,
    agent: uuid.UUID,
) -> dict[str, Any]:
    response = await client.post(
        f"/internal/schedule-drafts/{draft_id}/test-run",
        headers=_headers(tenant, actor=actor, agent=agent),
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def _read_trial(
    client: httpx.AsyncClient,
    tenant: TenantContext,
    draft_id: str,
    *,
    actor: uuid.UUID,
    agent: uuid.UUID,
) -> dict[str, Any]:
    response = await client.get(
        f"/internal/schedule-drafts/{draft_id}/test-run",
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


async def _count(tenant: TenantContext, table: str) -> int:
    async with tenant.session_factory() as session:
        return int(
            (
                await session.execute(
                    sa.text(f"SELECT count(*) FROM task.{table} WHERE tenant_id = :t"),
                    {"t": tenant.tenant_id},
                )
            ).scalar_one()
        )


async def _draft_row(tenant: TenantContext, draft_id: str) -> TaskScheduleDraft:
    async with tenant.session_factory() as session:
        row = await session.get(TaskScheduleDraft, uuid.UUID(draft_id))
    assert row is not None
    return row


async def _run_skill_worker(
    tenant: TenantContext,
    cache: SkillArtifactCache,
    artifacts: TaskArtifactClient,
) -> None:
    await run_worker_once(
        tenant,
        TaskTypeExecutor(
            reminder=ReminderExecutor(),
            skill=SkillTaskExecutor(cache, artifacts=artifacts),
        ),
    )


@pytest.fixture
async def runtime_artifacts(tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    root = tmp_path / "artifacts"
    runtime_app.dependency_overrides[runtime_get_artifact_store] = lambda: NfsArtifactStore(root)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=runtime_app), base_url="http://runtime"
    ) as client:
        yield client
    runtime_app.dependency_overrides.pop(runtime_get_artifact_store, None)


async def test_intents_compile_to_three_plans_and_create_drafts_without_mode(
    client: httpx.AsyncClient,
    tenant: TenantContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor, agent = uuid.uuid4(), uuid.uuid4()
    skill = _skill(checksum="sha256:" + "c" * 64, storage_key="skills/e2e/authoring.zip")
    context = _context(skill)
    materials_client = FakeMaterialsClient(
        by_mode={
            ScheduleMode.REMINDER: make_materials(mode=ScheduleMode.REMINDER),
            ScheduleMode.SKILL: _skill_materials(
                checksum="sha256:" + "c" * 64, storage_key="skills/e2e/authoring.zip"
            ),
            ScheduleMode.AGENTIC: make_materials(mode=ScheduleMode.AGENTIC, skill_id=SKILL_ID),
        }
    )
    monkeypatch.setattr(
        worker_app.state, "schedule_materials_client", materials_client, raising=False
    )
    monkeypatch.setattr(
        worker_app.state,
        "platform_settings_client",
        FakePlatformSettingsClient({}),
        raising=False,
    )

    cases = [
        (
            "reminder",
            "每天 9 点提醒我开周会",
            {"text": "9 点开周会"},
            "REMINDER",
            "NONE",
        ),
        (
            "skill",
            "每天 9 点给客户 1001 出昨天的值守日报",
            {
                "skill_key": SKILL_KEY,
                "argv": [
                    "--company-id",
                    "1001",
                    "--date",
                    "{{previous_day}}",
                    "--no-wecom-push",
                ],
                "window": {"kind": "DAY", "offset": -1},
            },
            "SKILL",
            "NONE",
        ),
        (
            "agent",
            "每天 9 点汇总昨天的定时任务执行，按失败原因分类",
            {
                "prompt": "汇总 {{previous_day}} 的定时任务执行，按失败原因分类",
                "tools_allow": ["PLATFORM:current_time"],
            },
            "AGENTIC",
            "PLATFORM",
        ),
    ]

    for label, instruction, payload, expected_mode, expected_budget in cases:
        authoring = compile_schedule_plan(
            {
                "instruction": instruction,
                "timezone": TZ,
                "recurrence": {"kind": "CRON", "cron": "0 9 * * *"},
                "payload": payload,
            },
            context=context,
        )
        assert authoring.plan is not None, label
        assert authoring.plan.derived_mode.value == expected_mode, label
        body = _draft_body(authoring, instruction, name=f"{label} 计划")
        assert "mode" not in body, f"{label}: the user never picks a mode"
        draft = await _create_draft(client, tenant, body, actor=actor, agent=agent)
        assert draft["preview"]["mode"] == expected_mode, label
        assert draft["preview"]["budget_source"] == expected_budget, label
        row = await _draft_row(tenant, draft["draft_id"])
        assert "mode" not in row.payload_json, label
        assert row.payload_json["payload"]["kind"] == {
            "REMINDER": "REMINDER",
            "SKILL": "SKILL_PLAN",
            "AGENTIC": "AGENT_TURN",
        }[expected_mode]

    assert await _count(tenant, "task_schedule_draft") == 3
    assert await _count(tenant, "task_schedule") == 0, "drafts must not create Jobs"


async def test_ambiguous_intent_asks_one_business_question_and_creates_no_draft(
    tenant: TenantContext,
) -> None:
    result = compile_schedule_plan(
        {
            "instruction": "每天给我看看客户情况",
            "timezone": TZ,
            "recurrence": {"kind": "CRON", "cron": "0 9 * * *"},
        },
        context=_context(None),
    )

    assert result.plan is None
    assert result.clarification is not None
    question = result.clarification.question
    assert question
    for internal in ("SKILL_PLAN", "AGENT_TURN", "REMINDER", "mode", "模式"):
        assert internal not in question
    assert "固定的" in question
    assert await _count(tenant, "task_schedule_draft") == 0


async def test_failed_trial_reports_repair_and_creates_no_job(
    client: httpx.AsyncClient,
    tenant: TenantContext,
    tmp_path: Path,
    runtime_artifacts: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "workspace"))
    actor, agent = uuid.uuid4(), uuid.uuid4()
    with running_mssp() as platform:
        nfs_root = tmp_path / "skill-nfs"
        nfs_root.mkdir()
        checksum, storage_key = _package_skill(nfs_root, platform, entrypoint="scripts/run.py")
        materials_client = FakeMaterialsClient(
            _skill_materials(checksum=checksum, storage_key=storage_key)
        )
        monkeypatch.setattr(
            worker_app.state, "schedule_materials_client", materials_client, raising=False
        )
        monkeypatch.setattr(
            worker_app.state,
            "platform_settings_client",
            FakePlatformSettingsClient({}),
            raising=False,
        )
        cache = SkillArtifactCache(NfsArtifactStore(nfs_root), tmp_path / "cache")
        artifacts = TaskArtifactClient(
            "http://runtime", runtime_artifacts, service_token=TEST_INTERNAL_SERVICE_TOKEN
        )

        skill = _skill(checksum=checksum, storage_key=storage_key)
        authoring = compile_schedule_plan(
            {
                "instruction": "每天 9 点给客户 1001 出昨天的值守日报",
                "timezone": TZ,
                "recurrence": {"kind": "CRON", "cron": "0 9 * * *"},
                "payload": {
                    "skill_key": SKILL_KEY,
                    "argv": [
                        "--company-id",
                        "1001",
                        "--date",
                        "{{previous_day}}",
                        "--no-wecom-push",
                    ],
                },
            },
            context=_context(skill),
        )
        body = _draft_body(authoring, "每天 9 点出值守日报", name="每日值守日报")
        draft = await _create_draft(client, tenant, body, actor=actor, agent=agent)

        platform.inject(path=PATH_SERVICE_INFO, http_status=401, times=1)
        receipt = await _test_run(client, tenant, draft["draft_id"], actor=actor, agent=agent)
        assert receipt["status"] == "QUEUED"
        await _run_skill_worker(tenant, cache, artifacts)
        trial_data = await _read_trial(
            client, tenant, draft["draft_id"], actor=actor, agent=agent
        )
        trial = trial_data["preview"]["trial"]

    assert trial["status"] == "FAILED"
    assert trial["error_code"] == "MSSP_FETCH_FAILED"
    assert trial["repair"] == "CHECK_TRIAL_FAILURE"
    assert trial["repair_hint"]
    assert await _count(tenant, "task_execution") == 1, "only the failed trial exists"
    assert await _count(tenant, "task_schedule") == 0
    assert await _count(tenant, "task_schedule_revision") == 0
    assert await _count(tenant, "schedule_fire") == 0
    assert (await _draft_row(tenant, draft["draft_id"])).status == "PENDING"


async def _seed_recorded_call(
    tenant: TenantContext,
    *,
    actor: uuid.UUID,
    agent: uuid.UUID,
    conversation_id: uuid.UUID,
) -> uuid.UUID:
    run_id = uuid.uuid4()
    arguments = {
        "target": "skill",
        "skill_key": SKILL_KEY,
        "argv": [
            "--company-id",
            "1001",
            "--date",
            RECORDED_DATE,
            "--no-wecom-push",
        ],
    }
    async with runtime_session_factory()() as session, session.begin():
        session.add(
            Conversation(
                id=conversation_id,
                tenant_id=tenant.tenant_id,
                user_id=actor,
                agent_id=agent,
                title="promotion",
                status="ACTIVE",
                last_seq=0,
                last_message_at=CALL_STARTED_AT,
                create_time=CALL_STARTED_AT,
                update_time=CALL_STARTED_AT,
                is_deleted=False,
            )
        )
        session.add(
            RunRecord(
                id=run_id,
                tenant_id=tenant.tenant_id,
                conversation_id=conversation_id,
                user_id=actor,
                agent_id=agent,
                status="COMPLETED",
                input_text="生成值守日报",
                trace_id="trace-promotion",
                start_time=CALL_STARTED_AT,
                end_time=CALL_STARTED_AT,
                create_time=CALL_STARTED_AT,
                update_time=CALL_STARTED_AT,
                is_deleted=False,
            )
        )
        session.add_all(
            [
                CanonicalEvent(
                    tenant_id=tenant.tenant_id,
                    conversation_id=conversation_id,
                    run_id=run_id,
                    seq=1,
                    event_type="ASSISTANT_TURN",
                    payload_json={
                        "text": "",
                        "reasoning_content": None,
                        "tool_calls": [
                            {
                                "id": "call-skill-1",
                                "name": "execute",
                                "arguments": arguments,
                            }
                        ],
                    },
                    create_time=CALL_STARTED_AT,
                    update_time=CALL_STARTED_AT,
                    is_deleted=False,
                ),
                CanonicalEvent(
                    tenant_id=tenant.tenant_id,
                    conversation_id=conversation_id,
                    run_id=run_id,
                    seq=2,
                    event_type="TOOL_CALL",
                    payload_json={
                        "tool_call_id": "call-skill-1",
                        "tool_name": "execute",
                        "status": "OK",
                    },
                    create_time=CALL_STARTED_AT,
                    update_time=CALL_STARTED_AT,
                    is_deleted=False,
                ),
                ToolCallAudit(
                    tenant_id=tenant.tenant_id,
                    run_id=run_id,
                    conversation_id=conversation_id,
                    tool_call_id="call-skill-1",
                    tool_name="execute",
                    tool_kind="SKILL",
                    prepared_args_hash="sha256:" + "a" * 64,
                    args_preview_json={"target": "skill", "skill_key": SKILL_KEY},
                    status="OK",
                    start_time=CALL_STARTED_AT,
                    business_status="SUCCEEDED",
                    create_time=CALL_STARTED_AT,
                    update_time=CALL_STARTED_AT,
                    is_deleted=False,
                ),
            ]
        )
    return run_id


async def test_promotion_reuses_the_recorded_call_and_a_successful_trial_creates_the_job(
    client: httpx.AsyncClient,
    tenant: TenantContext,
    tmp_path: Path,
    runtime_artifacts: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "workspace"))
    actor, agent = uuid.uuid4(), uuid.uuid4()
    conversation_id = uuid.uuid4()
    run_id = await _seed_recorded_call(
        tenant, actor=actor, agent=agent, conversation_id=conversation_id
    )
    facts = await load_promotion_facts(
        runtime_session_factory,
        tenant_id=tenant.tenant_id,
        actor_user_id=actor,
        agent_id=agent,
        run_id=run_id,
    )
    assert facts.tool_call_id == "call-skill-1"

    with running_mssp() as platform:
        nfs_root = tmp_path / "skill-nfs"
        nfs_root.mkdir()
        checksum, storage_key = _package_skill(nfs_root, platform, entrypoint="scripts/run.py")
        materials_client = FakeMaterialsClient(
            _skill_materials(checksum=checksum, storage_key=storage_key)
        )
        monkeypatch.setattr(
            worker_app.state, "schedule_materials_client", materials_client, raising=False
        )
        monkeypatch.setattr(
            worker_app.state,
            "platform_settings_client",
            FakePlatformSettingsClient({}),
            raising=False,
        )
        cache = SkillArtifactCache(NfsArtifactStore(nfs_root), tmp_path / "cache")
        artifacts = TaskArtifactClient(
            "http://runtime", runtime_artifacts, service_token=TEST_INTERNAL_SERVICE_TOKEN
        )

        proposal = promotion_proposal(
            facts,
            {
                "instruction": "把这个做成每天 9 点",
                "timezone": TZ,
                "recurrence": {"kind": "CRON", "cron": "0 9 * * *"},
            },
            timezone=TZ,
        )
        assert proposal.facts.bindings == {"previous_day": 1}
        authoring = compile_schedule_plan(
            proposal.arguments,
            context=_context(_skill(checksum=checksum, storage_key=storage_key)),
        )
        assert authoring.plan is not None
        assert authoring.plan.payload.kind == "SKILL_PLAN"
        body = _draft_body(authoring, "把这个做成每天 9 点", name="每日值守日报")
        draft = await _create_draft(client, tenant, body, actor=actor, agent=agent)
        assert draft["preview"]["mode"] == "SKILL"
        row = await _draft_row(tenant, draft["draft_id"])
        payload = row.payload_json["payload"]
        assert payload["skill_id"] == str(SKILL_ID)
        assert payload["input"] == {}
        assert payload["argv"] == [
            "--company-id",
            "1001",
            "--date",
            "{{previous_day}}",
            "--no-wecom-push",
        ]
        assert payload["window"] == {"kind": "DAY", "offset": -1}
        assert await _count(tenant, "task_schedule") == 0, "promotion only creates a Draft"

        receipt = await _test_run(client, tenant, draft["draft_id"], actor=actor, agent=agent)
        assert receipt["status"] == "QUEUED"
        await _run_skill_worker(tenant, cache, artifacts)
        trial_data = await _read_trial(
            client, tenant, draft["draft_id"], actor=actor, agent=agent
        )
        trial = trial_data["preview"]["trial"]
        assert trial["status"] == "COMPLETED", trial
        assert any(
            str(item["name"]).endswith("report.txt") for item in trial["artifacts"]
        )

        confirmed = await _confirm(
            client, tenant, draft, actor=actor, agent=agent, name="每日值守日报"
        )
        assert confirmed["status"] == "ACTIVE"

    assert await _count(tenant, "task_schedule") == 1
    assert await _count(tenant, "task_schedule_revision") == 1
    assert await _count(tenant, "schedule_fire") == 0
    async with tenant.session_factory() as session:
        revision = await session.scalar(
            sa.select(TaskScheduleRevision).where(
                TaskScheduleRevision.tenant_id == tenant.tenant_id,
                TaskScheduleRevision.schedule_id == uuid.UUID(confirmed["schedule_id"]),
            )
        )
    assert revision is not None
    assert revision.payload_json["skill_id"] == str(SKILL_ID)
    assert revision.payload_json["input"] == {}
    assert revision.payload_json["argv"] == [
        "--company-id",
        "1001",
        "--date",
        "{{previous_day}}",
        "--no-wecom-push",
    ]
    assert revision.payload_json["window"] == {"kind": "DAY", "offset": -1}
