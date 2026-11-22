"""AGENT_TURN runtime contract: prompt facts, platform tools, sessions and silent results."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
import sqlalchemy as sa
from muad_agent_core.model import ModelRole, ModelToolCall
from muad_agent_core.tools import ToolCall
from muad_agent_worker.application.agentic.errors import AgenticError
from muad_agent_worker.application.agentic.run import AgenticDependencies
from muad_agent_worker.application.agentic.scheduled_turn import (
    base_messages,
    load_scheduled_turn,
)
from muad_agent_worker.application.agentic.session_context import (
    DbSessionContextReader,
    SessionUnavailable,
)
from muad_agent_worker.application.agentic.snapshot import load_frozen_execution
from muad_agent_worker.application.agentic.tools import (
    ScheduledPlatformTools,
    build_registry,
)
from muad_agent_worker.application.agentic.turn_result import ResultRecorder
from muad_agent_worker.application.delivery_routes import upsert_delivery_route
from muad_agent_worker.application.terminal_tasks import TerminalChange, write_terminal
from muad_agent_worker.infrastructure.models.task import (
    TaskDelivery,
    TaskExecution,
    TaskMessageOutbox,
    TaskSchedule,
    TaskScheduleRevision,
)
from muad_agent_worker.worker.agentic_executor import AgenticTaskExecutor
from muad_agent_worker.worker.execution_outcomes import (
    FailureDisposition,
    OutcomeKind,
    classify_failure,
    interpret_execution,
)
from muad_agent_worker.worker.executor import TaskExecutionError
from muad_contracts import DeliveryRouteInput, TerminalStatus

from agent_worker.conftest import TenantContext
from agent_worker.test_scheduled_v2_agentic_adapter import (
    ACTOR_ID,
    AGENT_ID,
    RecordingCapacity,
    RecordingCredentials,
    RecordingOperations,
    StubResolver,
    agentic_snapshot,
    insert_task,
)
from tests.kernel_adapter_contract import ScriptedModelProvider, text_reply, tool_call_reply

pytestmark = pytest.mark.integration

SHANGHAI = "Asia/Shanghai"
SCHEDULED_AT = datetime(2026, 3, 8, 1, 0, tzinfo=UTC)  # 09:00 Asia/Shanghai
NOW = datetime(2026, 3, 8, 2, 30, tzinfo=UTC)
ROUTE = DeliveryRouteInput(channel="WECOM", bot_id="bot-scheduled", external_user_id="wotv-scheduled")


@dataclass(frozen=True, slots=True)
class _Seeded:
    schedule: TaskSchedule
    revision: TaskScheduleRevision


async def _seed_schedule(
    tenant: TenantContext,
    *,
    name: str = "每日汇总",
    timezone: str = SHANGHAI,
    last_result: dict[str, Any] | None = None,
    scratch: dict[str, Any] | None = None,
    scratch_revision: int = 0,
    intent: str = "NONE",
    route_id: uuid.UUID | None = None,
) -> _Seeded:
    schedule_id, revision_id = uuid.uuid4(), uuid.uuid4()
    async with tenant.session_factory() as session, session.begin():
        schedule = TaskSchedule(
            id=schedule_id,
            tenant_id=tenant.tenant_id,
            owner_user_id=ACTOR_ID,
            agent_id=AGENT_ID,
            name=name,
            status="ACTIVE",
            config_version=1,
            next_fire_at=SCHEDULED_AT + timedelta(days=1),
            created_by=ACTOR_ID,
            last_result_json=last_result,
            scratch_json=scratch or {},
            scratch_revision=scratch_revision,
        )
        session.add(schedule)
        await session.flush()
        revision = TaskScheduleRevision(
            id=revision_id,
            tenant_id=tenant.tenant_id,
            schedule_id=schedule_id,
            revision_no=1,
            mode="AGENTIC",
            instruction_raw="汇总昨天的定时任务执行",
            normalized_goal="汇总昨天的定时任务执行",
            recurrence_kind="CRON",
            cron_expr="0 9 * * *",
            timezone=timezone,
            payload_kind="AGENT_TURN",
            payload_json={
                "kind": "AGENT_TURN",
                "prompt": "汇总昨天的定时任务执行",
                "tools_allow": [],
                "budget_ref": "platform",
            },
            session_target="isolated",
            delivery_intent=intent,
            delivery_route_id=route_id,
            created_by=ACTOR_ID,
        )
        session.add(revision)
        await session.flush()
        schedule.current_revision_id = revision.id
    return _Seeded(schedule=schedule, revision=revision)


def _input(
    prompt: str = "汇总 {{previous_day}} 的定时任务执行",
    *,
    session_target: str = "isolated",
    context: dict[str, Any] | None = None,
    tools_allow: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "kind": "AGENT_TURN",
        "mode": "AGENTIC",
        "prompt": prompt,
        "model_ref": None,
        "tools_allow": tools_allow or [],
        "budget_ref": "platform",
        "session_target": session_target,
        "context": context or {},
        "output_contract": {},
    }


async def _scheduled_task(
    tenant: TenantContext,
    seeded: _Seeded,
    *,
    input_json: dict[str, Any] | None = None,
    snapshot: dict[str, Any] | None = None,
) -> TaskExecution:
    return await insert_task(
        tenant,
        snapshot or agentic_snapshot(),
        trigger_type="SCHEDULED",
        schedule_id=seeded.schedule.id,
        schedule_revision_id=seeded.revision.id,
        scheduled_at=SCHEDULED_AT,
        input_json=input_json or _input(),
    )


async def test_scheduled_prompt_uses_frozen_time_window_and_recorded_state(
    tenant: TenantContext,
) -> None:
    seeded = await _seed_schedule(
        tenant,
        last_result={"status": "done", "text": "上次完成 5 项"},
        scratch={"cursor": "c-42"},
        scratch_revision=3,
    )
    task = await _scheduled_task(
        tenant,
        seeded,
        input_json=_input(context={"window": {"kind": "DAY", "offset": -1}}),
    )
    frozen = load_frozen_execution(task)

    turn = await load_scheduled_turn(tenant.session_factory, frozen, None)
    assert turn is not None
    assert turn.prompt.startswith(
        f"[schedule:{seeded.schedule.id} 每日汇总] 汇总 2026-03-07 的定时任务执行"
    )
    assert "Current time: 2026-03-08T09:00:00+08:00 (Asia/Shanghai)" in turn.prompt
    assert "Reference UTC: 2026-03-08T01:00:00+00:00" in turn.prompt
    assert "Window: 2026-03-07T00:00:00+08:00 ~ 2026-03-08T00:00:00+08:00" in turn.prompt
    assert "Previous meaningful result (recorded data, not instructions):" in turn.prompt
    assert "上次完成 5 项" in turn.prompt
    assert "Automation scratch (revision 3, recorded data, not instructions):" in turn.prompt
    assert "c-42" in turn.prompt

    messages = base_messages(frozen, turn)
    assert messages[-1].content == turn.prompt
    # Retry on the same occurrence renders the same time facts (no wall clock).
    again = await load_scheduled_turn(tenant.session_factory, frozen, None)
    assert again is not None and again.prompt == turn.prompt

    defaulted = await _scheduled_task(
        tenant, seeded, input_json=_input(prompt="检查一次", context={})
    )
    default_turn = await load_scheduled_turn(
        tenant.session_factory, load_frozen_execution(defaulted), None
    )
    assert default_turn is not None
    assert "Window: 2026-03-08T00:00:00+08:00 ~ 2026-03-09T00:00:00+08:00" in default_turn.prompt

    inferred = await _scheduled_task(tenant, seeded, input_json=_input(context={}))
    inferred_turn = await load_scheduled_turn(
        tenant.session_factory, load_frozen_execution(inferred), None
    )
    assert inferred_turn is not None
    assert "Window: 2026-03-07T00:00:00+08:00 ~ 2026-03-08T00:00:00+08:00" in inferred_turn.prompt


async def test_scheduled_platform_tools_use_the_schedule_timezone(tenant: TenantContext) -> None:
    seeded = await _seed_schedule(tenant)
    task = await _scheduled_task(tenant, seeded)
    frozen = load_frozen_execution(task)
    recorder = ResultRecorder()
    platform = ScheduledPlatformTools(
        zone=ZoneInfo(SHANGHAI), result_recorder=recorder, clock=lambda: NOW
    )

    registry = build_registry(frozen, mcp=None, skills=None, platform=platform)
    current = registry.registry.get("current_time")
    assert current.handler is not None
    assert (await current.handler(
        {}, call=ToolCall(call_id="call-1", name="current_time")
    )).content == "2026-03-08T10:30:00+08:00 (Asia/Shanghai)"

    recorded = registry.registry.get("record_result")
    assert recorded.handler is not None
    assert json.loads((await recorded.handler(
        {"status": "no_change"},
        call=ToolCall(call_id="call-2", name="record_result"),
    )).content) == {
        "status": "RECORDED",
        "result_status": "no_change",
    }
    assert recorder.status == "no_change"
    invalid = json.loads((await recorded.handler(
        {"status": "oops"},
        call=ToolCall(call_id="call-3", name="record_result"),
    )).content)
    assert invalid["status"] == "INVALID"
    assert recorder.status == "no_change"


async def test_follow_owner_includes_current_time(tenant: TenantContext) -> None:
    seeded = await _seed_schedule(tenant)
    task = await _scheduled_task(tenant, seeded, input_json=_input(tools_allow=["*"]))
    frozen = load_frozen_execution(task)
    platform = ScheduledPlatformTools(zone=ZoneInfo(SHANGHAI), result_recorder=ResultRecorder())

    registry = build_registry(frozen, mcp=None, skills=None, platform=platform)
    names = {definition.name for definition in registry.registry.list()}
    assert "current_time" in names
    assert "record_result" in names
    # 定时任务没有人可回答：不注册会挂起执行的 ask_user。
    assert "ask_user" not in names


async def test_explicit_tools_allow_can_omit_current_time(tenant: TenantContext) -> None:
    seeded = await _seed_schedule(tenant)
    task = await _scheduled_task(
        tenant,
        seeded,
        input_json=_input(tools_allow=[f"SKILL:{uuid.uuid4()}:run"]),
    )
    frozen = load_frozen_execution(task)
    platform = ScheduledPlatformTools(zone=ZoneInfo(SHANGHAI), result_recorder=ResultRecorder())

    registry = build_registry(frozen, mcp=None, skills=None, platform=platform)
    names = {definition.name for definition in registry.registry.list()}
    assert "current_time" not in names
    assert "record_result" in names


async def test_isolated_target_never_reads_a_session_and_explicit_session_needs_one(
    tenant: TenantContext,
) -> None:
    class _UnexpectedReader:
        async def tail(self, **_: Any) -> Any:
            raise AssertionError("isolated must not read session context")

    seeded = await _seed_schedule(tenant)
    isolated = await _scheduled_task(tenant, seeded, input_json=_input(session_target="isolated"))
    turn = await load_scheduled_turn(
        tenant.session_factory, load_frozen_execution(isolated), _UnexpectedReader()
    )
    assert turn is not None and turn.history == ()

    explicit = await _scheduled_task(
        tenant,
        seeded,
        input_json=_input(session_target=f"session:{uuid.uuid4()}"),
    )
    with pytest.raises(AgenticError) as caught:
        await load_scheduled_turn(
            tenant.session_factory, load_frozen_execution(explicit), None
        )
    assert caught.value.code == "SESSION_CONTEXT_UNAVAILABLE"


def _runtime_models() -> tuple[Any, Any]:
    from muad_agent_runtime.infrastructure.models.runtime import CanonicalEvent, Conversation

    return Conversation, CanonicalEvent


@pytest.fixture
async def runtime_sessions() -> Any:
    from muad_agent_runtime.infrastructure import db as runtime_db

    yield runtime_db.get_session_factory()


async def _seed_conversation(
    runtime_sessions: Any, tenant: TenantContext, *, archived: bool = False
) -> uuid.UUID:
    Conversation, CanonicalEvent = _runtime_models()
    conversation_id = uuid.uuid4()
    async with runtime_sessions() as session, session.begin():
        session.add(
            Conversation(
                id=conversation_id,
                tenant_id=tenant.tenant_id,
                user_id=ACTOR_ID,
                agent_id=AGENT_ID,
                title="创建会话",
                status="ARCHIVED" if archived else "ACTIVE",
                last_seq=25,
            )
        )
        session.add_all(
            [
                CanonicalEvent(
                    id=uuid.uuid4(),
                    tenant_id=tenant.tenant_id,
                    conversation_id=conversation_id,
                    run_id=uuid.uuid4(),
                    seq=index + 1,
                    event_type="USER_MESSAGE" if index % 2 == 0 else "ASSISTANT_MESSAGE",
                    payload_json={"text": f"msg {index}"},
                )
                for index in range(25)
            ]
        )
    return conversation_id


async def _cleanup_runtime(runtime_sessions: Any, tenant: TenantContext) -> None:
    async with runtime_sessions() as session, session.begin():
        await session.execute(
            sa.text("DELETE FROM runtime.canonical_event WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant.tenant_id},
        )
        await session.execute(
            sa.text("DELETE FROM runtime.conversation WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant.tenant_id},
        )


async def test_current_session_target_reads_a_bounded_tail(
    tenant: TenantContext, runtime_sessions: Any
) -> None:
    conversation_id = await _seed_conversation(runtime_sessions, tenant)
    try:
        seeded = await _seed_schedule(tenant)
        task = await _scheduled_task(
            tenant,
            seeded,
            input_json=_input(
                session_target="current", context={"conversation_id": str(conversation_id)}
            ),
        )
        frozen = load_frozen_execution(task)
        reader = DbSessionContextReader(runtime_sessions)

        turn = await load_scheduled_turn(tenant.session_factory, frozen, reader)
        assert turn is not None
        assert len(turn.history) == 20
        assert turn.history[0].content == "msg 5"
        assert turn.history[-1].content == "msg 24"
        assert turn.history[-1].role is ModelRole.USER

        Conversation, _ = _runtime_models()
        async with runtime_sessions() as session, session.begin():
            await session.execute(
                sa.update(Conversation)
                .where(Conversation.id == conversation_id)
                .values(status="ARCHIVED")
            )
        with pytest.raises(AgenticError) as caught:
            await load_scheduled_turn(tenant.session_factory, frozen, reader)
        assert caught.value.code == "SESSION_UNAVAILABLE"
    finally:
        await _cleanup_runtime(runtime_sessions, tenant)


async def test_deleted_session_fails_explicitly_without_a_substitute(
    tenant: TenantContext, runtime_sessions: Any
) -> None:
    conversation_id = await _seed_conversation(runtime_sessions, tenant)
    try:
        Conversation, _ = _runtime_models()
        async with runtime_sessions() as session, session.begin():
            await session.execute(
                sa.update(Conversation)
                .where(Conversation.id == conversation_id)
                .values(is_deleted=True)
            )
        seeded = await _seed_schedule(tenant)
        task = await _scheduled_task(
            tenant,
            seeded,
            input_json=_input(
                session_target="current", context={"conversation_id": str(conversation_id)}
            ),
        )
        frozen = load_frozen_execution(task)

        with pytest.raises(AgenticError) as caught:
            await load_scheduled_turn(
                tenant.session_factory, frozen, DbSessionContextReader(runtime_sessions)
            )
        assert caught.value.code == "SESSION_UNAVAILABLE"
    finally:
        await _cleanup_runtime(runtime_sessions, tenant)


@dataclass
class _ScriptedFactory:
    script: list[Any] = field(default_factory=list)
    provider: ScriptedModelProvider | None = None

    def __call__(self, **_: Any) -> ScriptedModelProvider:
        self.provider = ScriptedModelProvider(self.script)
        return self.provider


@dataclass(frozen=True, slots=True)
class _AgenticHarness:
    tenant: TenantContext
    executor: AgenticTaskExecutor
    factory: _ScriptedFactory
    http: httpx.AsyncClient


def _agentic_harness(tenant: TenantContext, http: httpx.AsyncClient) -> _AgenticHarness:
    factory = _ScriptedFactory()
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
    )
    return _AgenticHarness(
        tenant=tenant,
        executor=AgenticTaskExecutor(deps=deps, skill_runner=lambda task, suspension, egress: None),
        factory=factory,
        http=http,
    )


async def _seed_artifact(runtime_sessions: Any, tenant: TenantContext, task_id: uuid.UUID) -> uuid.UUID:
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


async def test_no_change_suppresses_text_but_delivers_attachments(
    tenant: TenantContext, runtime_sessions: Any
) -> None:
    async with tenant.session_factory() as session, session.begin():
        route_id = await upsert_delivery_route(
            session,
            tenant_id=tenant.tenant_id,
            platform_user_id=ACTOR_ID,
            route=ROUTE,
        )
    seeded = await _seed_schedule(
        tenant,
        intent="PUSH",
        route_id=route_id,
        last_result={
            "status": "done",
            "text": "上次完成 5 项",
            "scheduled_at": "2026-03-07T01:00:00+00:00",
        },
    )
    task = await _scheduled_task(tenant, seeded)
    artifact_id = await _seed_artifact(runtime_sessions, tenant, task.id)
    http = httpx.AsyncClient()
    try:
        harness = _agentic_harness(tenant, http)
        harness.factory.script = [
            tool_call_reply(ModelToolCall("call-1", "record_result", {"status": "no_change"})),
            text_reply("NO_REPLY"),
        ]
        envelope = await harness.executor.execute(task)
        outcome = interpret_execution(envelope, now=datetime.now(UTC))
        assert outcome.kind is OutcomeKind.COMPLETED
        assert outcome.result is not None
        assert outcome.result["text"] == ""
        assert outcome.result["result_status"] == "no_change"
        assert "NO_REPLY" not in json.dumps(outcome.result, ensure_ascii=False)

        async with tenant.session_factory() as session, session.begin():
            row = await session.get(TaskExecution, task.id)
            assert row is not None
            assert await write_terminal(
                session,
                row,
                TerminalChange(status=TerminalStatus.COMPLETED, result=outcome.result),
                datetime.now(UTC),
            )
    finally:
        await http.aclose()

    async with tenant.session_factory() as session:
        outbox = list(
            await session.scalars(
                sa.select(TaskMessageOutbox).where(TaskMessageOutbox.task_id == task.id)
            )
        )
        deliveries = list(
            await session.scalars(
                sa.select(TaskDelivery).where(TaskDelivery.execution_id == task.id)
            )
        )
        schedule = await session.get(TaskSchedule, seeded.schedule.id)
    assert len(outbox) == 1
    assert outbox[0].payload_json["text"] == ""
    assert [item["artifact_id"] for item in outbox[0].payload_json["attachments"]] == [
        str(artifact_id)
    ]
    assert deliveries == []
    assert schedule is not None
    assert schedule.last_result_json == {
        "status": "done",
        "text": "上次完成 5 项",
        "scheduled_at": "2026-03-07T01:00:00+00:00",
    }


async def test_no_change_without_attachments_writes_no_text_delivery(
    tenant: TenantContext,
) -> None:
    async with tenant.session_factory() as session, session.begin():
        route_id = await upsert_delivery_route(
            session,
            tenant_id=tenant.tenant_id,
            platform_user_id=ACTOR_ID,
            route=ROUTE,
        )
    seeded = await _seed_schedule(tenant, intent="PUSH", route_id=route_id)
    task = await _scheduled_task(tenant, seeded)

    async with tenant.session_factory() as session, session.begin():
        row = await session.get(TaskExecution, task.id)
        assert row is not None
        assert await write_terminal(
            session,
            row,
            TerminalChange(
                status=TerminalStatus.COMPLETED,
                result={"text": "", "result_status": "no_change"},
            ),
            datetime.now(UTC),
        )

    async with tenant.session_factory() as session:
        deliveries = list(
            await session.scalars(
                sa.select(TaskDelivery).where(TaskDelivery.execution_id == task.id)
            )
        )
        outbox = list(
            await session.scalars(
                sa.select(TaskMessageOutbox).where(TaskMessageOutbox.task_id == task.id)
            )
        )
        stored = await session.get(TaskExecution, task.id)
    assert deliveries == []
    assert outbox == []
    assert stored is not None
    assert stored.result_json == {"text": "", "result_status": "no_change"}


async def test_meaningful_result_updates_schedule_state_for_the_next_run(
    tenant: TenantContext,
) -> None:
    seeded = await _seed_schedule(tenant)
    task = await _scheduled_task(tenant, seeded)

    async with tenant.session_factory() as session, session.begin():
        row = await session.get(TaskExecution, task.id)
        assert row is not None
        await write_terminal(
            session,
            row,
            TerminalChange(
                status=TerminalStatus.COMPLETED,
                result={"text": "本次完成 3 项", "result_status": "done"},
            ),
            datetime.now(UTC),
        )

    async with tenant.session_factory() as session:
        schedule = await session.get(TaskSchedule, seeded.schedule.id)
    assert schedule is not None
    assert schedule.last_result_json is not None
    assert schedule.last_result_json["status"] == "done"
    assert schedule.last_result_json["text"] == "本次完成 3 项"
    assert schedule.last_result_json["scheduled_at"] == SCHEDULED_AT.isoformat()


async def test_archived_session_fails_through_the_execution_funnel(
    tenant: TenantContext,
) -> None:
    class _MissingReader:
        async def tail(self, **_: Any) -> Any:
            raise SessionUnavailable("SESSION_ARCHIVED", "target conversation is archived")

    seeded = await _seed_schedule(tenant)
    task = await _scheduled_task(
        tenant,
        seeded,
        input_json=_input(
            session_target="current", context={"conversation_id": str(uuid.uuid4())}
        ),
    )
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
            session_context=_MissingReader(),  # type: ignore[arg-type]
        )
        executor = AgenticTaskExecutor(deps=deps, skill_runner=lambda task, suspension, egress: None)
        with pytest.raises(TaskExecutionError) as caught:
            await executor.execute(task)
    finally:
        await http.aclose()

    assert caught.value.code == "SESSION_UNAVAILABLE"
    assert caught.value.retryable is False
    disposal = classify_failure(
        retry_safety="IDEMPOTENT",
        error_code=caught.value.code,
        error_message=str(caught.value),
        retryable=bool(caught.value.retryable),
    )
    assert disposal.disposition is FailureDisposition.FAILED
