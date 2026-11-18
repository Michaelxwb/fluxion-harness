"""TASK-014 · S-13 从成功执行提升：复用真实 skill/input/argv，只产 Draft。

真实边界：真实 Runtime 工具入口（ToolRegistry handler）→ 真实 PostgreSQL（
`runtime.run_record`/`canonical_event`/`tool_call_audit` + `task.task_schedule_draft`）→
真实 Worker Draft API（ASGI，含服务身份/租户/actor/agent 头与幂等台账）。材料解析是只读
外部边界，用桩快照注入。

关键断言：

1. 只从审计里业务成功、且 canonical 回合确有完整参数的 `execute(target=skill)` 提升；
   参数按记录原样复制（审计预览是截断的，不能作为事实来源）。
2. 日期值绑定为 `{{previous_day}}`/`{{fire_date}}`，其余输入/argv 不变。
3. 结果仍是一条 PENDING Draft：不自动激活，不建 Schedule/Revision/Fire。
4. 跨租户/用户/Agent 与未成功运行一律拒绝，且不留下 Draft。
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
from muad_agent_runtime.application.schedule_promotion import (
    bind_dates,
    date_bindings,
)
from muad_agent_runtime.application.task_client import TaskSubmissionContext, WorkerTaskClient
from muad_agent_runtime.application.task_tools import PROMOTE_RUN_TOOL, BackgroundTaskToolSet
from muad_agent_runtime.infrastructure.db import get_session_factory as runtime_session_factory
from muad_agent_runtime.infrastructure.models.runtime import (
    CanonicalEvent,
    Conversation,
    RunRecord,
    ToolCallAudit,
)
from muad_agent_worker.application.ports import PlatformSettingsSnapshot
from muad_agent_worker.infrastructure.db import get_session_factory as worker_session_factory
from muad_agent_worker.main import app as worker_app
from muad_contracts import (
    ResolvedAgent,
    ResolvedMcpServer,
    ResolvedModel,
    ResolvedSkill,
    ScheduleAdmissionMaterials,
    ScheduleAdmissionRequest,
    ScheduleGrantMaterials,
    ScheduleMode,
    ScheduleModelMaterials,
    SkillExecutionMode,
)
from sqlalchemy import func, select

from agent_runtime.conftest import TenantContext
from tests.internal_service import TOKEN

pytestmark = pytest.mark.integration

TZ = "Asia/Shanghai"
SKILL_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
CALL_ID = "call-skill-1"
#: 记录调用的本地日期 = 2026-11-18 09:00 Asia/Shanghai；业务日期是前一天。
CALL_STARTED_AT = datetime(2026, 11, 18, 1, 0, 0, tzinfo=UTC)
BUSINESS_DATE = "2026-11-17"


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


def skill_materials() -> ScheduleAdmissionMaterials:
    return ScheduleAdmissionMaterials(
        mode=ScheduleMode.SKILL,
        agent=ResolvedAgent(id=uuid.uuid4(), key="agent", revision=1, instructions="be brief"),
        model=ScheduleModelMaterials(
            id=uuid.uuid4(), revision=1, model_id="gpt-4o-mini", protocol="OPENAI"
        ),
        skills=[_skill()],
        grants=ScheduleGrantMaterials(agent_access=True, skill_ids=[SKILL_ID]),
        settings_revision=1,
    )


@pytest.fixture(autouse=True)
def install_worker_boundaries(monkeypatch: pytest.MonkeyPatch) -> FakeMaterials:
    materials = FakeMaterials(skill_materials())
    monkeypatch.setattr(worker_app.state, "schedule_materials_client", materials, raising=False)
    monkeypatch.setattr(
        worker_app.state, "platform_settings_client", FakeSettingsClient(), raising=False
    )
    return materials


@pytest.fixture
async def conversation(tenant: TenantContext) -> AsyncIterator[uuid.UUID]:
    conversation_id = uuid.uuid4()
    async with runtime_session_factory()() as session:
        session.add(
            Conversation(
                id=conversation_id,
                tenant_id=tenant.tenant_id,
                user_id=tenant.platform_user_id,
                agent_id=tenant.agent_id,
                title="promotion",
                status="ACTIVE",
                last_seq=0,
                last_message_at=CALL_STARTED_AT,
                create_time=CALL_STARTED_AT,
                update_time=CALL_STARTED_AT,
                is_deleted=False,
            )
        )
        await session.commit()
    try:
        yield conversation_id
    finally:
        await _purge(tenant.tenant_id)


async def _purge(tenant_id: str) -> None:
    """清理本租户的 runtime 审计/事件与 task.* Draft（每次用例独立租户）。"""
    async with runtime_session_factory()() as session, session.begin():
        for table in ("tool_call_audit", "canonical_event", "run_record", "conversation"):
            await session.execute(
                sa.text(f"DELETE FROM runtime.{table} WHERE tenant_id = :t"), {"t": tenant_id}
            )
    async with worker_session_factory()() as session, session.begin():
        for table in ("task_event", "task_submission", "task_schedule_draft"):
            await session.execute(
                sa.text(f"DELETE FROM task.{table} WHERE tenant_id = :t"), {"t": tenant_id}
            )


async def _seed_run(
    tenant: TenantContext,
    conversation_id: uuid.UUID,
    *,
    run_status: str = "COMPLETED",
    business_status: str | None = "SUCCEEDED",
    include_arguments: bool = True,
    tenant_id: str | None = None,
    user_id: uuid.UUID | None = None,
    agent_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """种一次真实 Run：完成回合 + 工具结果 + 业务成功的审计行。"""
    run_id = uuid.uuid4()
    owner_tenant = tenant_id or tenant.tenant_id
    owner_user = user_id or tenant.platform_user_id
    owner_agent = agent_id or tenant.agent_id
    arguments = {
        "target": "skill",
        "skill_key": "weekly-report",
        "input": {"company_id": "22921947", "date": BUSINESS_DATE},
        "argv": ["--company-id", "22921947", "--date", BUSINESS_DATE],
    }
    turn_payload: dict[str, Any] = {"text": "", "reasoning_content": None, "tool_calls": []}
    if include_arguments:
        turn_payload["tool_calls"] = [
            {"id": CALL_ID, "name": "execute", "arguments": arguments}
        ]
    async with runtime_session_factory()() as session, session.begin():
        last_seq = await session.scalar(
            select(func.max(CanonicalEvent.seq)).where(
                CanonicalEvent.conversation_id == conversation_id
            )
        )
        base_seq = int(last_seq or 0)
        session.add(
            RunRecord(
                id=run_id,
                tenant_id=owner_tenant,
                conversation_id=conversation_id,
                user_id=owner_user,
                agent_id=owner_agent,
                status=run_status,
                input_text="生成周报",
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
                    tenant_id=owner_tenant,
                    conversation_id=conversation_id,
                    run_id=run_id,
                    seq=base_seq + 1,
                    event_type="ASSISTANT_TURN",
                    payload_json=turn_payload,
                    create_time=CALL_STARTED_AT,
                    update_time=CALL_STARTED_AT,
                    is_deleted=False,
                ),
                CanonicalEvent(
                    tenant_id=owner_tenant,
                    conversation_id=conversation_id,
                    run_id=run_id,
                    seq=base_seq + 2,
                    event_type="TOOL_CALL",
                    payload_json={
                        "tool_call_id": CALL_ID,
                        "tool_name": "execute",
                        "status": "OK",
                    },
                    create_time=CALL_STARTED_AT,
                    update_time=CALL_STARTED_AT,
                    is_deleted=False,
                ),
                ToolCallAudit(
                    tenant_id=owner_tenant,
                    run_id=run_id,
                    conversation_id=conversation_id,
                    tool_call_id=CALL_ID,
                    tool_name="execute",
                    tool_kind="SKILL",
                    prepared_args_hash="sha256:" + "a" * 64,
                    args_preview_json={"target": "skill", "skill_key": "weekly-report"},
                    status="OK",
                    start_time=CALL_STARTED_AT,
                    business_status=business_status,
                    create_time=CALL_STARTED_AT,
                    update_time=CALL_STARTED_AT,
                    is_deleted=False,
                ),
            ]
        )
    return run_id


def _client() -> WorkerTaskClient:
    return WorkerTaskClient(
        "http://worker", service_token=TOKEN, transport=httpx.ASGITransport(app=worker_app)
    )


def _context(
    tenant: TenantContext,
    conversation_id: uuid.UUID,
    *,
    actor_user_id: uuid.UUID | None = None,
    agent_id: uuid.UUID | None = None,
) -> TaskSubmissionContext:
    agent = ResolvedAgent(
        id=agent_id or tenant.agent_id, key="agent", revision=1, instructions="hi", runtime_config={}
    )
    return TaskSubmissionContext(
        tenant_id=tenant.tenant_id,
        actor_user_id=actor_user_id or tenant.platform_user_id,
        agent=agent,
        model=ResolvedModel(
            id=uuid.uuid4(), revision=1, model_id="gpt-4o-mini", base_url="http://model", params={}
        ),
        skills=(_skill(),),
        mcp_servers=(
            ResolvedMcpServer(
                mcp_server_id=uuid.uuid4(),
                key="crm",
                endpoint="https://crm.internal/mcp",
                catalog_revision=1,
                definitions=[{"name": "customer.report"}],
            ),
        ),
        source_run_id=uuid.uuid4(),
        conversation_id=conversation_id,
        entry_channel="WEBCHAT",
    )


def _registry(
    context: TaskSubmissionContext, *, session_factory: Any = runtime_session_factory
) -> ToolRegistry:
    registry = ToolRegistry()
    BackgroundTaskToolSet(
        client=_client(),
        context=context,
        skills=[_skill()],
        session_factory=session_factory,
    ).register(registry)
    return registry


async def _invoke(
    registry: ToolRegistry, name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    definition = registry.get(name)
    assert definition is not None and definition.handler is not None
    payload = await definition.handler(arguments, call_id=f"call-{uuid.uuid4()}")
    return cast(dict[str, Any], json.loads(payload))


def _promote_args(run_id: uuid.UUID) -> dict[str, Any]:
    return {
        "run_id": str(run_id),
        "instruction": "把这个做成每天 9 点",
        "timezone": TZ,
        "recurrence": {"kind": "CRON", "cron": "0 9 * * *"},
    }


async def _drafts(tenant_id: str) -> list[dict[str, Any]]:
    async with worker_session_factory()() as session:
        rows = await session.execute(
            sa.text(
                "SELECT payload_json FROM task.task_schedule_draft "
                "WHERE tenant_id = :t ORDER BY create_time"
            ),
            {"t": tenant_id},
        )
        return [dict(row[0]) for row in rows.all()]


async def _count(tenant_id: str, table: str) -> int:
    async with worker_session_factory()() as session:
        return int(
            (
                await session.execute(
                    sa.text(f"SELECT count(*) FROM task.{table} WHERE tenant_id = :t"),
                    {"t": tenant_id},
                )
            ).scalar_one()
        )


async def test_promotion_copies_the_recorded_skill_call_and_binds_dates(
    tenant: TenantContext, conversation: uuid.UUID
) -> None:
    run_id = await _seed_run(tenant, conversation)
    result = await _invoke(_registry(_context(tenant, conversation)), PROMOTE_RUN_TOOL, _promote_args(run_id))

    assert result["status"] == "PENDING"
    assert result["mode"] == "SKILL"
    assert result["promotion"]["run_id"] == str(run_id)
    assert result["promotion"]["tool_call_id"] == CALL_ID
    assert result["promotion"]["bindings"] == {"previous_day": 2}
    assert result["resolved_payload"]["skill_id"] == str(SKILL_ID)
    assert result["resolved_payload"]["input"] == {
        "company_id": "22921947",
        "date": "{{previous_day}}",
    }
    assert result["resolved_payload"]["argv"] == [
        "--company-id",
        "22921947",
        "--date",
        "{{previous_day}}",
    ]
    assert result["resolved_payload"]["window"] == {"kind": "DAY", "offset": -1}

    drafts = await _drafts(tenant.tenant_id)
    assert len(drafts) == 1
    payload = drafts[0]["payload"]
    assert payload["skill_id"] == str(SKILL_ID)
    assert payload["input"]["company_id"] == "22921947", "literal parameters stay exact"
    assert payload["input"]["date"] == "{{previous_day}}"
    assert payload["argv"] == ["--company-id", "22921947", "--date", "{{previous_day}}"]
    assert (await _count(tenant.tenant_id, "task_schedule")) == 0
    assert (await _count(tenant.tenant_id, "task_schedule_revision")) == 0
    assert (await _count(tenant.tenant_id, "schedule_fire")) == 0


async def test_fire_date_is_bound_when_the_call_used_todays_date(
    tenant: TenantContext, conversation: uuid.UUID
) -> None:
    run_id = await _seed_run(tenant, conversation)
    async with runtime_session_factory()() as session, session.begin():
        await session.execute(
            sa.text(
                "UPDATE runtime.canonical_event SET payload_json = CAST(:payload AS jsonb) "
                "WHERE tenant_id = :t AND run_id = :r AND event_type = 'ASSISTANT_TURN'"
            ),
            {
                "t": tenant.tenant_id,
                "r": run_id,
                "payload": json.dumps(
                    {
                        "text": "",
                        "reasoning_content": None,
                        "tool_calls": [
                            {
                                "id": CALL_ID,
                                "name": "execute",
                                "arguments": {
                                    "target": "skill",
                                    "skill_key": "weekly-report",
                                    "input": {"date": "2026-11-18"},
                                    "argv": ["--date", "2026-11-18"],
                                },
                            }
                        ],
                    }
                ),
            },
        )
    result = await _invoke(
        _registry(_context(tenant, conversation)), PROMOTE_RUN_TOOL, _promote_args(run_id)
    )
    assert result["promotion"]["bindings"] == {"fire_date": 2}
    assert result["resolved_payload"]["input"] == {"date": "{{fire_date}}"}
    assert result["resolved_payload"]["window"] == {"kind": "DAY", "offset": 0}


async def test_promotion_rejects_other_owners_and_unsuccessful_runs(
    tenant: TenantContext, conversation: uuid.UUID
) -> None:
    run_id = await _seed_run(tenant, conversation)
    other = _context(tenant, conversation, actor_user_id=uuid.uuid4())
    denied_user = await _invoke(_registry(other), PROMOTE_RUN_TOOL, _promote_args(run_id))
    assert denied_user["error"]["code"] == "COMMON_NOT_FOUND"

    other_agent = _context(tenant, conversation, agent_id=uuid.uuid4())
    denied_agent = await _invoke(
        _registry(other_agent), PROMOTE_RUN_TOOL, _promote_args(run_id)
    )
    assert denied_agent["error"]["code"] == "COMMON_NOT_FOUND"

    foreign_tenant = f"test-{uuid.uuid4()}"
    foreign_run = await _seed_run(
        tenant,
        conversation,
        tenant_id=foreign_tenant,
        user_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
    )
    try:
        denied_tenant = await _invoke(
            _registry(_context(tenant, conversation)),
            PROMOTE_RUN_TOOL,
            _promote_args(foreign_run),
        )
        assert denied_tenant["error"]["code"] == "COMMON_NOT_FOUND"
    finally:
        await _purge(foreign_tenant)

    failed_run = await _seed_run(tenant, conversation, run_status="FAILED")
    failed = await _invoke(
        _registry(_context(tenant, conversation)), PROMOTE_RUN_TOOL, _promote_args(failed_run)
    )
    assert failed["error"]["code"] == "RUN_NOT_COMPLETED"

    unsuccessful = await _seed_run(tenant, conversation, business_status="FAILED")
    no_success = await _invoke(
        _registry(_context(tenant, conversation)), PROMOTE_RUN_TOOL, _promote_args(unsuccessful)
    )
    assert no_success["error"]["code"] == "RUN_SKILL_CALL_MISSING"

    assert await _drafts(tenant.tenant_id) == []


def test_date_binding_only_touches_standalone_date_tokens() -> None:
    hits: dict[str, int] = {}
    bound = bind_dates(
        {
            "exact": BUSINESS_DATE,
            "argument": f"--date={BUSINESS_DATE}",
            "identifier": f"customer-{BUSINESS_DATE}",
            "unrelated": "2026-11-01",
            "timestamp": f"{BUSINESS_DATE}T09:00:00+08:00",
        },
        dates=date_bindings(CALL_STARTED_AT, TZ),
        hits=hits,
    )
    assert bound == {
        "exact": "{{previous_day}}",
        "argument": "--date={{previous_day}}",
        "identifier": f"customer-{BUSINESS_DATE}",
        "unrelated": "2026-11-01",
        "timestamp": f"{BUSINESS_DATE}T09:00:00+08:00",
    }
    assert hits == {"previous_day": 2}


async def test_promotion_requires_recorded_arguments_not_a_prose_guess(
    tenant: TenantContext, conversation: uuid.UUID
) -> None:
    run_id = await _seed_run(tenant, conversation, include_arguments=False)
    result = await _invoke(
        _registry(_context(tenant, conversation)), PROMOTE_RUN_TOOL, _promote_args(run_id)
    )
    assert result["error"]["code"] == "RUN_SKILL_CALL_MISSING"
    assert await _drafts(tenant.tenant_id) == []
