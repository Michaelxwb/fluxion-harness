"""[每次 Fire 的 Admission 与 Snapshot v4] 冻结、可重建、失败分类与落库。

真实边界：真实 PG（事务/行/唯一键）与真实 `TaskExecution` ORM；只把 Console 的
HTTP 取数替成协议实现（网络边界），快照组装、校验、哈希与落库全部走生产代码。
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from muad_agent_worker.application.schedule_admission import (
    AdmissionError,
    AdmissionOutcome,
    AdmissionRejected,
    AdmissionRequest,
    ScheduleAdmissionService,
    admission_request_from_revision,
    build_snapshot,
    classify_failure,
    delivery_capability_error,
    execution_snapshot_columns,
    record_admission_failure,
)
from muad_agent_worker.infrastructure.models.task import (
    ScheduleFire,
    TaskExecution,
    TaskSchedule,
    TaskScheduleRevision,
)
from muad_contracts import (
    ResolvedAgent,
    ResolvedMcpServer,
    ResolvedSkill,
    ScheduleAdmissionMaterials,
    ScheduleAdmissionRequest,
    ScheduleGrantMaterials,
    ScheduleMemoryCandidate,
    ScheduleMemoryMaterials,
    ScheduleMode,
    ScheduleModelMaterials,
    ScheduleToolMaterials,
    SkillExecutionMode,
    SkillSource,
    load_schedule_snapshot,
    schedule_snapshot_bytes,
    schedule_snapshot_hash,
)
from muad_contracts.platform_settings import default_platform_settings

from agent_worker.conftest import TenantContext

pytestmark = pytest.mark.integration

SKILL_CHECKSUM = "sha256:" + "c" * 64
POLICY = {"compaction": {"summary": {"enabled": False}}, "egress": {"enabled": False}}


@dataclass(frozen=True)
class Seeded:
    schedule_id: uuid.UUID
    revision_id: uuid.UUID
    fire_id: uuid.UUID
    agent_id: uuid.UUID
    actor_user_id: uuid.UUID
    request: AdmissionRequest


def _skill(skill_id: uuid.UUID) -> ResolvedSkill:
    return ResolvedSkill(
        skill_id=skill_id,
        artifact_id=uuid.uuid4(),
        key="report",
        name="Report",
        description="Render a report",
        version="1.2.0",
        checksum=SKILL_CHECKSUM,
        storage_key="skills/report.tar.gz",
        execution_mode=SkillExecutionMode.ASYNC,
        frontmatter={"retry_safety": "IDEMPOTENT"},
        source=SkillSource.ADMIN,
    )


def make_materials(
    *,
    mode: ScheduleMode,
    skill_id: uuid.UUID | None = None,
    memory: ScheduleMemoryMaterials | None = None,
    api_key: str | None = None,
) -> ScheduleAdmissionMaterials:
    skills = (
        [_skill(skill_id if skill_id is not None else uuid.uuid4())]
        if mode is not ScheduleMode.REMINDER
        else []
    )
    tools: list[ScheduleToolMaterials] = []
    mcp: list[ResolvedMcpServer] = []
    if mode is ScheduleMode.AGENTIC:
        server_id = uuid.uuid4()
        tools = [
            ScheduleToolMaterials(
                source="MCP",
                tool_name="customer.report",
                input_schema={"type": "object", "properties": {"account": {"type": "string"}}},
                retry_safety="READ_ONLY",
                server_id=server_id,
                server_key="crm",
                catalog_revision=4,
            ),
            ScheduleToolMaterials(
                source="SKILL",
                tool_name="run_report",
                skill_id=skills[0].skill_id,
                artifact_id=skills[0].artifact_id,
                checksum=SKILL_CHECKSUM,
                execution_mode="ASYNC",
                retry_safety="IDEMPOTENT",
            ),
        ]
        mcp = [
            ResolvedMcpServer(
                mcp_server_id=server_id,
                key="crm",
                endpoint="https://crm.example.com/mcp",
                catalog_revision=4,
                catalog_hash="sha256:" + "d" * 64,
                definitions=[{"name": "customer.report"}],
                headers={"X-Tenant": "demo"},
                secret_header="X-Api-Key",
            )
        ]
    return ScheduleAdmissionMaterials(
        mode=mode,
        agent=ResolvedAgent(
            id=uuid.uuid4(),
            key="agent",
            revision=3,
            instructions="You are the scheduled agent.",
            runtime_config={"budget": {"retry_safety": "IDEMPOTENT"}},
        ),
        model=ScheduleModelMaterials(
            id=uuid.uuid4(),
            revision=2,
            model_id="gpt-4o-mini",
            protocol="OPENAI",
            params={"temperature": 0.2, **({"api_key": api_key} if api_key else {})},
            base_url=None if mode is ScheduleMode.REMINDER else "https://api.example.com/v1",
            deployment_id=None if mode is ScheduleMode.REMINDER else uuid.uuid4(),
            pool_members=[{"deployment_id": str(uuid.uuid4()), "weight": 1}],
            summary_model={"id": str(uuid.uuid4()), "revision": 1, "model_id": "gpt-4o-mini"},
        ),
        skills=skills,
        mcp_servers=mcp,
        tools=tools,
        memory=memory,
        policy=POLICY,
        prompt_template={"version": "v1"},
        grants=ScheduleGrantMaterials(
            agent_access=True,
            skill_ids=[skill.skill_id for skill in skills],
            mcp_server_ids=[server.mcp_server_id for server in mcp],
        ),
        settings_revision=1,
        settings={},
    )


def _memory_materials(*, degraded: bool = False) -> ScheduleMemoryMaterials:
    candidates = []
    if not degraded:
        candidates = [
            ScheduleMemoryCandidate(
                memory_key="preference.reply_language",
                memory_kind="PREFERENCE",
                predicate="reply_language",
                scope_state="ABSENT",
                scope={},
                content={"value": "中文"},
                title="输出用中文",
                summary="用户要求中文回复",
                tags=[],
                cardinality="SINGLE",
                priority="REQUIRED",
                author="USER",
                version=4,
                updated_at=datetime.now(UTC),
            )
        ]
    return ScheduleMemoryMaterials(
        taxonomy_version=1,
        revision=42,
        as_of=datetime.now(UTC),
        candidates=candidates,
        degraded=degraded,
        error_code="MEMORY_READ_FAILED" if degraded else None,
    )


def _agentic_request(skill_id: uuid.UUID) -> AdmissionRequest:
    return AdmissionRequest(
        tenant_id="test-admission",
        agent_id=uuid.uuid4(),
        actor_user_id=uuid.uuid4(),
        mode=ScheduleMode.AGENTIC,
        skill_id=None,
        tool_allowlist=(
            _allowlist("MCP", "crm", "customer.report"),
            _allowlist("SKILL", str(skill_id), "run_report"),
        ),
        budget={"max_steps": 12, "max_tool_calls": 20, "max_tokens": 0},
        context={"prompt_hash": "sha256:" + "e" * 64},
    )


def _allowlist(source: str, ref: str, tool_name: str) -> Any:
    from muad_contracts import ToolAllowlistEntry

    if source == "MCP":
        return ToolAllowlistEntry(source="MCP", server_key=ref, tool_name=tool_name)
    return ToolAllowlistEntry(source="SKILL", skill_id=uuid.UUID(ref), tool_name=tool_name)


class FakeMaterialsClient:
    def __init__(
        self,
        materials: ScheduleAdmissionMaterials | None = None,
        errors: list[Exception] | None = None,
    ) -> None:
        self.materials = materials
        self.errors = list(errors or [])
        self.calls: list[tuple[str, ScheduleAdmissionRequest]] = []

    async def resolve_schedule_materials(
        self, tenant_id: str, payload: ScheduleAdmissionRequest
    ) -> ScheduleAdmissionMaterials:
        self.calls.append((tenant_id, payload))
        if self.errors:
            raise self.errors.pop(0)
        assert self.materials is not None
        return self.materials


def test_agentic_snapshot_freezes_dependencies_and_readonly_memory() -> None:
    skill_id = uuid.uuid4()
    materials = make_materials(
        mode=ScheduleMode.AGENTIC, skill_id=skill_id, memory=_memory_materials()
    )
    outcome = build_snapshot(materials, _agentic_request(skill_id), default_platform_settings())
    assert outcome.accepted, outcome.error
    snapshot = outcome.snapshot
    assert snapshot is not None

    assert snapshot["schema_version"] == 4 and snapshot["mode"] == "AGENTIC"
    assert snapshot["snapshot_bytes"] == schedule_snapshot_bytes(snapshot)
    assert schedule_snapshot_hash(snapshot) == outcome.snapshot_hash
    assert outcome.snapshot_hash is not None
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", outcome.snapshot_hash)

    # 依赖范围内的完整材料：Agent 全文、模型池/摘要、Skill artifact、工具完整 schema。
    assert snapshot["agent"]["instructions"] == "You are the scheduled agent."
    assert snapshot["model"]["pool_members"] and snapshot["model"]["summary_model"]
    assert snapshot["model"]["policy_hash"].startswith("sha256:")
    assert [skill["artifact_id"] for skill in snapshot["skills"]] == [
        str(materials.skills[0].artifact_id)
    ]
    by_source = {tool["source"]: tool for tool in snapshot["tools"]}
    assert by_source["MCP"]["input_schema"]["properties"]["account"]["type"] == "string"
    assert by_source["MCP"]["catalog_revision"] == 4
    assert by_source["SKILL"]["execution_mode"] == "ASYNC"
    assert snapshot["allowlist"] == [
        "MCP:crm:customer.report",
        f"SKILL:{skill_id}:run_report",
    ]
    assert snapshot["mcp"][0]["secret_header"] == "X-Api-Key"
    assert "auth_secret" not in str(snapshot)

    # 记忆：只读、实际注入块、revision 与 payload_hash 一起冻结。
    memory = snapshot["memory"]
    assert memory["mode"] == "READ_ONLY"
    assert memory["revision"] == 42
    assert memory["degraded"] is False
    assert memory["block"]["entries"][0]["memory_key"] == "preference.reply_language"
    assert "中文" in memory["block"]["rendered_text"]
    assert memory["payload_hash"].startswith("sha256:")

    expected = default_platform_settings().agent
    assert snapshot["budget"]["max_steps"] == expected.max_turns
    assert snapshot["budget"]["max_tool_calls"] == expected.max_tool_calls
    assert snapshot["budget"]["max_tokens"] == 0
    assert snapshot["budget"]["retry_safety"] == "IDEMPOTENT"


def test_snapshot_never_embeds_model_secrets() -> None:
    """Console 若把 Key 混进模型参数，快照构建直接失败而不是"打印时脱敏"。"""
    skill_id = uuid.uuid4()
    outcome = build_snapshot(
        make_materials(
            mode=ScheduleMode.AGENTIC, skill_id=skill_id, api_key="sk-live-must-not-leak"
        ),
        _agentic_request(skill_id),
        default_platform_settings(),
    )
    assert not outcome.accepted
    assert outcome.error is not None
    assert outcome.error.code == "SCHEMA_INCOMPATIBLE" and outcome.error.permanent


def test_skill_and_reminder_snapshot_shapes() -> None:
    skill_id = uuid.uuid4()
    skill_outcome = build_snapshot(
        make_materials(mode=ScheduleMode.SKILL, skill_id=skill_id),
        AdmissionRequest(
            tenant_id="test-admission",
            agent_id=uuid.uuid4(),
            actor_user_id=uuid.uuid4(),
            mode=ScheduleMode.SKILL,
            skill_id=skill_id,
        ),
        default_platform_settings(),
    )
    assert skill_outcome.accepted
    assert len(skill_outcome.snapshot["skills"]) == 1  # type: ignore[index]
    assert skill_outcome.snapshot["memory"] is None  # type: ignore[index]

    reminder_outcome = build_snapshot(
        make_materials(mode=ScheduleMode.REMINDER),
        AdmissionRequest(
            tenant_id="test-admission",
            agent_id=uuid.uuid4(),
            actor_user_id=uuid.uuid4(),
            mode=ScheduleMode.REMINDER,
        ),
        default_platform_settings(),
    )
    assert reminder_outcome.accepted
    snapshot = reminder_outcome.snapshot
    assert snapshot is not None
    assert snapshot["skills"] == [] and snapshot["memory"] is None


def test_snapshot_over_limit_is_a_permanent_failure() -> None:
    settings = default_platform_settings()
    tiny = replace(settings, schedule=replace(settings.schedule, max_snapshot_bytes=1024))
    skill_id = uuid.uuid4()
    outcome = build_snapshot(
        make_materials(mode=ScheduleMode.AGENTIC, skill_id=skill_id, memory=_memory_materials()),
        _agentic_request(skill_id),
        tiny,
    )
    assert not outcome.accepted
    assert outcome.snapshot is None and outcome.snapshot_bytes == 0
    assert outcome.error == AdmissionError(
        "SNAPSHOT_TOO_LARGE", True, "REDUCE_SCHEDULE_DEPENDENCIES"
    )


def test_memory_read_failure_degrades_instead_of_blocking() -> None:
    skill_id = uuid.uuid4()
    outcome = build_snapshot(
        make_materials(
            mode=ScheduleMode.AGENTIC, skill_id=skill_id, memory=_memory_materials(degraded=True)
        ),
        _agentic_request(skill_id),
        default_platform_settings(),
    )
    assert outcome.accepted, outcome.error
    assert outcome.memory_degraded is True
    memory = outcome.snapshot["memory"]  # type: ignore[index]
    assert memory["degraded"] is True
    assert memory["block"] == {}


def test_classification_is_permanent_only_for_fixable_dependencies() -> None:
    assert classify_failure("SCHEDULE_DEPENDENCY_MISSING", {"admission_code": "SKILL_DISABLED"}) == (
        AdmissionError("SKILL_DISABLED", True, "UPDATE_SCHEDULE_DEPENDENCY")
    )
    transient = classify_failure("RESOLVE_UNAVAILABLE")
    assert transient.permanent is False and transient.repair == "RETRY_LATER"
    # 未登记的新码按临时处理：一次未知错误不该把用户任务判死。
    assert classify_failure("SOMETHING_NEW").permanent is False


async def _seed_fire(
    tenant: TenantContext,
    *,
    mode: str = "AGENTIC",
    recurrence_kind: str = "CRON",
    payload: dict[str, Any] | None = None,
) -> Seeded:
    now = datetime.now(UTC).replace(microsecond=0)
    factory = tenant.session_factory
    async with factory() as session, session.begin():
        schedule = TaskSchedule(
            id=uuid.uuid4(),
            tenant_id=tenant.tenant_id,
            owner_user_id=uuid.uuid4(),
            agent_id=uuid.uuid4(),
            name="admission case",
            status="ACTIVE",
            config_version=1,
            created_by=uuid.uuid4(),
            next_fire_at=now,
        )
        session.add(schedule)
        await session.flush()
        revision = TaskScheduleRevision(
            id=uuid.uuid4(),
            tenant_id=tenant.tenant_id,
            schedule_id=schedule.id,
            revision_no=1,
            mode=mode,
            instruction_raw="每天检查一次",
            normalized_goal="检查一次",
            recurrence_kind=recurrence_kind,
            cron_expr="0 9 * * *" if recurrence_kind == "CRON" else None,
            run_at=None if recurrence_kind == "CRON" else now,
            timezone="Asia/Shanghai",
            schedule_policy={},
            payload_kind={"REMINDER": "REMINDER", "SKILL": "SKILL_PLAN", "AGENTIC": "AGENT_TURN"}[mode],
            payload_json=payload
            or {
                "kind": "AGENT_TURN",
                "prompt": "检查一次",
                "tools_allow": [],
                "budget_ref": "platform",
            },
            delivery_intent="NONE",
            created_by=uuid.uuid4(),
        )
        session.add(revision)
        await session.flush()
        fire = ScheduleFire(
            id=uuid.uuid4(),
            tenant_id=tenant.tenant_id,
            schedule_id=schedule.id,
            schedule_revision_id=revision.id,
            scheduled_at=now,
            status="CREATED",
        )
        session.add(fire)
        await session.flush()
        request = admission_request_from_revision(
            revision,
            tenant_id=tenant.tenant_id,
            agent_id=schedule.agent_id,
            actor_user_id=schedule.owner_user_id,
        )
        seeded = Seeded(
            schedule_id=schedule.id,
            revision_id=revision.id,
            fire_id=fire.id,
            agent_id=schedule.agent_id,
            actor_user_id=schedule.owner_user_id,
            request=request,
        )
        session.expunge_all()
    return seeded


async def test_snapshot_is_reconstructable_from_the_persisted_json_alone(
    tenant: TenantContext,
) -> None:
    seeded = await _seed_fire(tenant)
    outcome = build_snapshot(
        make_materials(mode=ScheduleMode.AGENTIC, memory=_memory_materials()),
        seeded.request,
        default_platform_settings(),
    )
    assert outcome.accepted, outcome.error
    now = datetime.now(UTC)
    async with tenant.session_factory() as session, session.begin():
        fire = await session.get(ScheduleFire, seeded.fire_id)
        assert fire is not None
        fire.status = "EXECUTION_CREATED"
        execution = TaskExecution(
            id=uuid.uuid4(),
            tenant_id=tenant.tenant_id,
            schedule_id=seeded.schedule_id,
            schedule_fire_id=seeded.fire_id,
            schedule_revision_id=seeded.revision_id,
            scheduled_at=now,
            agent_id=seeded.agent_id,
            actor_user_id=seeded.actor_user_id,
            trigger_type="SCHEDULED",
            task_type="AGENTIC",
            status="QUEUED",
            input_json={},
            idempotency_key=f"scheduled-{uuid.uuid4()}",
            not_before=now,
            deadline_at=now + timedelta(hours=1),
            **execution_snapshot_columns(outcome),
        )
        session.add(execution)
        # 循环 FK（fire ↔ execution）不是 DEFERRABLE：先落 execution 再回填 fire。
        await session.flush()
        fire.execution_id = execution.id
        await session.flush()
        execution_id = execution.id

    # 只读落库 JSON 重建：不查任何当前配置，哈希与字节数自洽。
    async with tenant.session_factory() as session:
        stored = await session.get(TaskExecution, execution_id)
        assert stored is not None
        assert stored.execution_snapshot_schema_version == 4
        assert stored.snapshot_bytes == schedule_snapshot_bytes(stored.execution_snapshot_json)
        rebuilt = load_schedule_snapshot(stored.execution_snapshot_json)
        assert rebuilt.model_dump(mode="json")["snapshot_bytes"] == stored.snapshot_bytes
        assert schedule_snapshot_hash(rebuilt.model_dump(mode="json")) == stored.snapshot_hash

    # v3 文档不能冒充 v4：拒绝而不是猜字段。
    with pytest.raises(ValueError):
        load_schedule_snapshot({"schema_version": 3, "agent": {}, "model": {}})


async def test_permanent_failure_marks_attention_without_a_fake_execution(
    tenant: TenantContext,
) -> None:
    seeded = await _seed_fire(tenant)
    moment = datetime.now(UTC)
    async with tenant.session_factory() as session, session.begin():
        fire = await session.get(ScheduleFire, seeded.fire_id)
        schedule = await session.get(TaskSchedule, seeded.schedule_id)
        assert fire is not None and schedule is not None
        await record_admission_failure(
            session,
            fire=fire,
            schedule=schedule,
            error=AdmissionError("SKILL_DISABLED", True, "UPDATE_SCHEDULE_DEPENDENCY"),
            is_once=False,
            moment=moment,
        )
    async with tenant.session_factory() as session:
        fire = await session.get(ScheduleFire, seeded.fire_id)
        schedule = await session.get(TaskSchedule, seeded.schedule_id)
        assert fire is not None and schedule is not None
        assert fire.status == "FAILED_ADMISSION"
        assert fire.admission_error_code == "SKILL_DISABLED"
        assert fire.execution_id is None
        assert schedule.status == "NEEDS_ATTENTION"
        assert schedule.attention_reason == "SKILL_DISABLED"
        executions = await session.scalar(
            sa.select(sa.func.count())
            .select_from(TaskExecution)
            .where(TaskExecution.schedule_fire_id == seeded.fire_id)
        )
        assert executions == 0


async def test_once_permanent_failure_ends_the_schedule(tenant: TenantContext) -> None:
    seeded = await _seed_fire(tenant, recurrence_kind="ONCE")
    async with tenant.session_factory() as session, session.begin():
        fire = await session.get(ScheduleFire, seeded.fire_id)
        schedule = await session.get(TaskSchedule, seeded.schedule_id)
        assert fire is not None and schedule is not None
        await record_admission_failure(
            session,
            fire=fire,
            schedule=schedule,
            error=AdmissionError("AGENT_DISABLED", True, "ENABLE_AGENT"),
            is_once=True,
            moment=datetime.now(UTC),
        )
    async with tenant.session_factory() as session:
        schedule = await session.get(TaskSchedule, seeded.schedule_id)
        assert schedule is not None
        assert schedule.status == "ENDED" and schedule.end_reason == "ADMISSION_FAILED"
        assert schedule.next_fire_at is None and schedule.attention_reason is None


async def test_transient_failure_retries_once_and_keeps_cron_active(tenant: TenantContext) -> None:
    from muad_agent_worker.scheduler.client import ScheduleMaterialsError

    seeded = await _seed_fire(tenant)
    client = FakeMaterialsClient(
        errors=[
            ScheduleMaterialsError("boom"),
            ScheduleMaterialsError("boom again"),
        ]
    )
    service = ScheduleAdmissionService(client, settings=default_platform_settings())
    outcome = await service.admit(seeded.request, retry_transient_once=True)
    assert [call[1].agent_id for call in client.calls] == [seeded.request.agent_id] * 2
    assert not outcome.accepted
    assert outcome.error is not None
    assert outcome.error.code == "RESOLVE_UNAVAILABLE" and outcome.error.permanent is False

    async with tenant.session_factory() as session, session.begin():
        fire = await session.get(ScheduleFire, seeded.fire_id)
        schedule = await session.get(TaskSchedule, seeded.schedule_id)
        assert fire is not None and schedule is not None
        await record_admission_failure(
            session,
            fire=fire,
            schedule=schedule,
            error=outcome.error,
            is_once=False,
            moment=datetime.now(UTC),
        )
    async with tenant.session_factory() as session:
        schedule = await session.get(TaskSchedule, seeded.schedule_id)
        assert schedule is not None
        assert schedule.status == "ACTIVE" and schedule.attention_reason is None


async def test_admission_requests_the_materials_once_and_accepts(
    tenant: TenantContext,
) -> None:
    seeded = await _seed_fire(tenant)
    client = FakeMaterialsClient(
        materials=make_materials(mode=ScheduleMode.AGENTIC, memory=_memory_materials())
    )
    service = ScheduleAdmissionService(client, settings=default_platform_settings())
    outcome = await service.admit(seeded.request)
    assert outcome.accepted, outcome.error
    assert len(client.calls) == 1
    tenant_id, payload = client.calls[0]
    assert tenant_id == tenant.tenant_id
    assert payload.mode is ScheduleMode.AGENTIC


async def test_revision_spec_is_parsed_or_rejected(tenant: TenantContext) -> None:
    seeded = await _seed_fire(tenant)
    assert seeded.request.mode is ScheduleMode.AGENTIC
    assert seeded.request.budget == {}, "预算数值只在 admission 时从平台设置解析"
    assert seeded.request.context["prompt_hash"].startswith("sha256:")

    async with tenant.session_factory() as session, session.begin():
        revision = await session.get(TaskScheduleRevision, seeded.revision_id)
        assert revision is not None
        revision.payload_json = {"kind": "REMINDER", "text": "mismatch"}
    async with tenant.session_factory() as session:
        revision = await session.get(TaskScheduleRevision, seeded.revision_id)
        assert revision is not None
        with pytest.raises(AdmissionRejected) as rejected:
            admission_request_from_revision(
                revision,
                tenant_id=tenant.tenant_id,
                agent_id=seeded.agent_id,
                actor_user_id=seeded.actor_user_id,
            )
    assert rejected.value.error == AdmissionError(
        "SCHEMA_INCOMPATIBLE", True, "UPDATE_SCHEDULE_DEPENDENCY"
    )


async def test_outcome_without_snapshot_cannot_be_persisted() -> None:
    with pytest.raises(ValueError):
        execution_snapshot_columns(AdmissionOutcome(error=AdmissionError("X", True, "Y")))


async def test_session_rolls_back_the_snapshot_with_the_execution(tenant: TenantContext) -> None:
    """快照与 Execution 同事务：注入失败后一起回滚，不留半个执行。"""
    seeded = await _seed_fire(tenant)
    outcome = build_snapshot(
        make_materials(mode=ScheduleMode.AGENTIC), seeded.request, default_platform_settings()
    )
    now = datetime.now(UTC)
    execution_id = uuid.uuid4()
    with pytest.raises(RuntimeError):
        async with tenant.session_factory() as session, session.begin():
            session.add(
                TaskExecution(
                    id=execution_id,
                    tenant_id=tenant.tenant_id,
                    schedule_id=seeded.schedule_id,
                    schedule_fire_id=seeded.fire_id,
                    schedule_revision_id=seeded.revision_id,
                    scheduled_at=now,
                    agent_id=seeded.agent_id,
                    actor_user_id=seeded.actor_user_id,
                    trigger_type="SCHEDULED",
                    task_type="AGENTIC",
                    status="QUEUED",
                    input_json={},
                    idempotency_key=f"scheduled-{uuid.uuid4()}",
                    not_before=now,
                    deadline_at=now + timedelta(hours=1),
                    **execution_snapshot_columns(outcome),
                )
            )
            await session.flush()
            raise RuntimeError("injected failure after the execution insert")
    async with tenant.session_factory() as session:
        assert await session.get(TaskExecution, execution_id) is None




def test_delivery_capability_is_matched_to_the_channel() -> None:
    """投递意图与渠道能力：IM 可 push，WebChat/MSSWWEB 只能 pull。"""
    assert delivery_capability_error("PUSH", "WECOM") is None
    assert delivery_capability_error("NONE", None) is None
    # 渠道尚未解析时不做假设（由 Fire 步骤解析 delivery_route 后传入）。
    assert delivery_capability_error("PUSH", None) is None
    assert delivery_capability_error("PUSH", "WEBCHAT") == AdmissionError(
        "SCHEDULE_DELIVERY_UNSUPPORTED", True, "CHANGE_DELIVERY_INTENT"
    )


async def test_push_to_webchat_fails_before_any_materials_call(tenant: TenantContext) -> None:
    seeded = await _seed_fire(tenant)
    client = FakeMaterialsClient(materials=make_materials(mode=ScheduleMode.AGENTIC))
    service = ScheduleAdmissionService(client, settings=default_platform_settings())
    outcome = await service.admit(
        replace(seeded.request, delivery_intent="PUSH", delivery_channel="WEBCHAT")
    )
    assert not outcome.accepted
    assert outcome.error is not None
    assert outcome.error.code == "SCHEDULE_DELIVERY_UNSUPPORTED"
    assert client.calls == []
