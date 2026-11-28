"""统一收尾漏斗：RUN_FAILED.finalization + ASSISTANT_MESSAGE / 取消不写失败。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from muad_agent_core.agent.termination import (
    BudgetKind,
    FailureEvidence,
    FailureState,
    TerminationSnapshot,
)
from muad_agent_runtime.application import run_service
from muad_agent_runtime.application.async_tools.supervisor import ExecutionSupervisor
from muad_agent_runtime.application.context_builder import DbBackedContextBuilder
from muad_agent_runtime.application.executor import ExecutorEvent
from muad_agent_runtime.application.ports import NullPlatformSettingsClient
from muad_agent_runtime.application.run_submission import (
    ENDPOINT_CREATE_RUN,
    RunSubmissionService,
)
from muad_agent_runtime.infrastructure.models.runtime import (
    CanonicalEvent,
    RunRecord,
    ToolCallAudit,
)
from muad_contracts import RunStatus
from muad_platform_sdk.egress_source_authority import (
    SourceCancellationRequested,
    SourceDeadlineReached,
    SourceLeaseEnded,
)

from tests.async_tool_continuations import running_source


class _OutcomeExecutor:
    def __init__(
        self, data: dict | None, failure: Exception | None = None, *, fail_prepared: bool = False,
    ) -> None:
        self._data = data
        self._failure = failure
        self._fail_prepared = fail_prepared

    async def run(self):
        if self._fail_prepared:
            yield ExecutorEvent(type="tool.started", data={"tool_call_id": "probe"})
            pytest.fail("tool must not dispatch after its prepared event failed to persist")
        if self._failure is not None:
            raise self._failure
        if self._data is not None:
            yield ExecutorEvent(type="runner.outcome", data=self._data)


async def _stream(
    async_tool_database,
    monkeypatch,
    fake_resolve,
    *,
    data: dict | None,
    cancel: bool = False,
    prepare=None,
    failure: Exception | None = None,
    fail_prepared: bool = False,
):
    factory, context, _, run, identity = await running_source(async_tool_database)
    monkeypatch.setattr(run_service, "get_session_factory", lambda: factory)

    async def executor_factory(request):
        return _OutcomeExecutor(data, failure, fail_prepared=fail_prepared)

    supervisor = ExecutionSupervisor()
    async with factory() as session:
        run = await session.get(RunRecord, run.id)
        if cancel:
            run.cancel_requested = True
        if prepare is not None:
            await prepare(factory, run)
        submission = await RunSubmissionService(lambda: factory).record_in(
            session,
            tenant_id=run.tenant_id,
            idempotency_key=str(uuid.uuid4()),
            endpoint=ENDPOINT_CREATE_RUN,
            actor_user_id=run.user_id,
            run_id=run.id,
            conversation_id=run.conversation_id,
            request_fingerprint="sha256:" + "e" * 64,
        )
        await session.commit()
        service = run_service.RunService(
            session,
            fake_resolve,
            identity.owner,
            NullPlatformSettingsClient(),
            supervisor=supervisor,
            executor_factory=executor_factory,
            context_builder=DbBackedContextBuilder(session_factory=lambda: factory),
        )
        events = [
            event
            async for event in service._stream_run(
                run=run,
                agent=context.agent,
                model=context.model,
                skills=(),
                mcp_servers=(),
                mcp_secrets={},
                history=(),
                submission_id=submission.id,
                resumed=True,
            )
        ]
    await supervisor.close()
    return factory, run, events


def _outcome_data(status: str, snapshot: TerminationSnapshot) -> dict:
    return {
        "status": status,
        "final_text": "",
        "termination": snapshot.as_payload(),
    }


@pytest.mark.parametrize(("error_type", "code", "reason"), [
    (SourceDeadlineReached, "RUN_DEADLINE_EXCEEDED", "超过期限"),
    (SourceLeaseEnded, "RUN_ABANDONED", "失去连接"),
])
async def test_source_cutoff_is_a_visible_failed_run(
    async_tool_database, monkeypatch, fake_resolve, error_type, code, reason,
):
    factory, run, events = await _stream(
        async_tool_database, monkeypatch, fake_resolve, data=None,
        failure=error_type("internal egress detail"),
    )
    assert events[-1].data["error_code"] == code
    async with factory() as session:
        reply = await session.scalar(sa.select(CanonicalEvent).where(
            CanonicalEvent.run_id == run.id, CanonicalEvent.event_type == "ASSISTANT_MESSAGE",
        ))
    assert reason in reply.payload_json["text"]
    assert "internal egress detail" not in reply.payload_json["text"]


async def test_prepared_event_write_failure_has_safe_explicit_closeout(
    async_tool_database, monkeypatch, fake_resolve,
):
    async def reject_prepared(factory, _run):
        async with factory.begin() as session:
            await session.execute(sa.text("""
                CREATE FUNCTION runtime.reject_prepared_probe() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN RAISE EXCEPTION 'fixture database detail'; END; $$
            """))
            await session.execute(sa.text("""
                CREATE TRIGGER reject_prepared_probe BEFORE INSERT ON runtime.canonical_event
                FOR EACH ROW WHEN (NEW.event_type = 'TOOL_CALL_STARTED')
                EXECUTE FUNCTION runtime.reject_prepared_probe()
            """))

    factory, run, events = await _stream(
        async_tool_database, monkeypatch, fake_resolve, data=None,
        prepare=reject_prepared, fail_prepared=True,
    )
    terminal = events[-1].data
    assert terminal["error_code"] == "RUN_EVENT_PERSISTENCE_FAILED"
    assert "无法保存本轮执行记录" in terminal["final_text"]
    assert "fixture database detail" not in str(terminal)
    assert terminal["finalization"]["termination"]["error_code"] == "RUN_EVENT_PERSISTENCE_FAILED"
    assert "technical_details" not in terminal
    async with factory() as session:
        stored = await session.scalars(sa.select(CanonicalEvent.event_type).where(
            CanonicalEvent.run_id == run.id,
        ))
        kinds = list(stored)
    assert kinds.count("RUN_FAILED") == 1
    assert "TOOL_CALL_STARTED" not in kinds


async def _insert_audit(factory, run, **fields) -> None:
    async with factory() as session:
        session.add(
            ToolCallAudit(
                id=uuid.uuid4(),
                tenant_id=run.tenant_id,
                run_id=run.id,
                conversation_id=run.conversation_id,
                tool_call_id=fields.pop("tool_call_id", f"call-{uuid.uuid4().hex[:8]}"),
                tool_name=fields.pop("tool_name", "execute"),
                tool_kind="SKILL",
                prepared_args_hash="sha256:" + "0" * 64,
                args_preview_json={},
                status=fields.pop("status", "OK"),
                start_time=datetime.now(UTC),
                end_time=datetime.now(UTC),
                latency_ms=1,
                **fields,
            )
        )
        await session.commit()


async def _cleanup_audit(factory, tenant_id: str) -> None:
    async with factory() as session:
        await session.execute(
            sa.delete(ToolCallAudit).where(ToolCallAudit.tenant_id == tenant_id)
        )
        await session.commit()


async def test_turn_budget_persists_platform_finalization_and_message(
    async_tool_database, monkeypatch, fake_resolve
) -> None:
    snapshot = TerminationSnapshot(
        status="BUDGET_EXCEEDED",
        budget_kind=BudgetKind.TURN,
        turns_used=20,
        turns_limit=20,
        tool_calls_used=20,
        tool_calls_limit=30,
        elapsed_ms=99_963,
        deadline_ms=3_600_000,
    )
    factory, run, events = await _stream(
        async_tool_database,
        monkeypatch,
        fake_resolve,
        data=_outcome_data("BUDGET_EXCEEDED", snapshot),
    )

    terminal = events[-1]
    assert terminal.type == "run.failed"
    payload = terminal.data
    assert payload["error_code"] == "RUN_BUDGET_EXCEEDED"
    assert payload["finalization"]["termination"]["reason"] == "TURN_BUDGET"
    assert payload["finalization"]["usage"]["turns_used"] == 20
    assert payload["final_text"]
    assert "原因：本轮执行达到最大轮次上限" in payload["final_text"]

    async with factory() as session:
        saved = await session.get(RunRecord, run.id)
        assert saved.status is RunStatus.FAILED
        message = await session.scalar(
            sa.select(CanonicalEvent).where(
                CanonicalEvent.run_id == run.id,
                CanonicalEvent.event_type == "ASSISTANT_MESSAGE",
            )
        )
        assert message is not None
        assert message.payload_json["text"] == payload["final_text"]


@pytest.mark.parametrize("failure_reply", [None, "没有完成查询，平台拒绝了当前登录状态。请重新登录后再试。"])
async def test_business_failure_is_read_from_tool_audit(
    async_tool_database, monkeypatch, fake_resolve, failure_reply
) -> None:
    call_id = "call-business"
    snapshot = TerminationSnapshot(
        status="NO_PROGRESS",
        turns_used=1,
        turns_limit=20,
        tool_calls_used=1,
        tool_calls_limit=30,
        failure_state=FailureState(
            last_failure=FailureEvidence(
                tool="execute",
                tool_call_id=call_id,
                code="PLATFORM_AUTH_REJECTED",
                message="mssw session rejected: HTTP 401",
                status="FAILED",
                failure_layer="CONTENT",
                exit_code=3,
            ),
            identical_failures={"execute:PLATFORM_AUTH_REJECTED:CONTENT": 1},
            no_progress=True,
            no_progress_trigger_call_id=call_id,
            no_progress_source_call_id=call_id,
        ),
    )

    async def prepare(factory, run):
        await _insert_audit(
            factory,
            run,
            tool_call_id=call_id,
            tool_name="execute",
            business_status="FAILED",
            failure_layer="CONTENT",
            error_code="PLATFORM_AUTH_REJECTED",
            exit_code=3,
            output_truncated=False,
            evidence_preview="mssw session rejected: HTTP 401",
            retryable=False,
            retry_disposition="BLOCK",
            agent_disposition="STOP",
            diagnostic_json={
                "detail": {"reason": "PLATFORM_AUTH_REJECTED"},
                "action_owner": "ADMIN",
                "message_source": "PLATFORM",
            },
        )
        await _insert_audit(
            factory,
            SimpleNamespace(
                tenant_id=run.tenant_id,
                id=uuid.uuid4(),
                conversation_id=run.conversation_id,
            ),
            tool_name="execute",
            business_status="FAILED",
            failure_layer="CONTENT",
            error_code="PLATFORM_AUTH_REJECTED",
        )

    factory, run, events = await _stream(
        async_tool_database,
        monkeypatch,
        fake_resolve,
        data={**_outcome_data("NO_PROGRESS", snapshot), "failure_reply": failure_reply},
        prepare=prepare,
    )
    try:
        payload = events[-1].data
        assert payload["error_code"] == "RUN_NO_PROGRESS"
        evidence = payload["finalization"]["business_failure_evidence"]
        assert payload["finalization"]["evidence_status"] == "CONFIRMED_FAILURE"
        assert payload["finalization"]["tool_outcomes"]["total"] == 1
        assert evidence["last_failure"]["error_code"] == "PLATFORM_AUTH_REJECTED"
        assert evidence["decisive_failure"]["tool"] == "execute"
        assert evidence["source_tool_call_id"] == call_id
        assert evidence["decisive_failure"]["retryable"] is False
        assert evidence["decisive_failure"]["retry_disposition"] == "BLOCK"
        assert evidence["decisive_failure"]["agent_disposition"] == "STOP"
        assert evidence["decisive_failure"]["diagnostic"]["action_owner"] == "ADMIN"
        assert "PLATFORM_AUTH_REJECTED" not in payload["final_text"]
        assert "technical_details" not in payload
        if failure_reply:
            assert payload["final_text"] != failure_reply
            async with factory() as session:
                messages = (
                    await session.scalars(sa.select(CanonicalEvent).where(
                        CanonicalEvent.run_id == run.id,
                        CanonicalEvent.event_type == "ASSISTANT_MESSAGE",
                    ))
                ).all()
                assert [message.payload_json["text"] for message in messages] == [payload["final_text"]]
    finally:
        await _cleanup_audit(factory, run.tenant_id)


async def test_unresolved_tool_failure_is_a_real_failed_run(
    async_tool_database, monkeypatch, fake_resolve
) -> None:
    call_id = "call-business"
    snapshot = TerminationSnapshot(
        status="TOOL_FAILURE",
        turns_used=1,
        turns_limit=20,
        tool_calls_used=1,
        tool_calls_limit=30,
        failure_state=FailureState(
            last_failure=FailureEvidence(
                tool="draft_schedule",
                tool_call_id=call_id,
                code="SCHEDULE_INVOCATION_CONTRACT_MISSING",
                message="preflight failed",
                status="FAILED",
                failure_layer="TOOL",
                action_owner="ADMIN",
                hint_known=True,
            ),
            failure_pending=True,
            no_progress_trigger_call_id=call_id,
            no_progress_source_call_id=call_id,
        ),
    )

    async def prepare(factory, run):
        await _insert_audit(
            factory,
            run,
            tool_call_id=call_id,
            tool_name="draft_schedule",
            business_status="FAILED",
            failure_layer="TOOL",
            error_code="SCHEDULE_INVOCATION_CONTRACT_MISSING",
            retryable=False,
            diagnostic_json={
                "detail": {"reason": "CONTRACT_MISSING", "repair": "REIMPORT_SKILL"},
                "action_owner": "ADMIN",
                "message_source": "PLATFORM",
            },
        )

    factory, run, events = await _stream(
        async_tool_database,
        monkeypatch,
        fake_resolve,
        data=_outcome_data("TOOL_FAILURE", snapshot),
        prepare=prepare,
    )
    try:
        terminal = events[-1]
        assert terminal.type == "run.failed"
        payload = terminal.data
        assert payload["error_code"] == "RUN_TOOL_FAILURE_UNRESOLVED"
        assert payload["finalization"]["termination"]["reason"] == "TOOL_FAILURE"
        assert "管理员重新导入" in payload["final_text"]
        assert "SCHEDULE_INVOCATION_CONTRACT_MISSING" not in payload["final_text"]
        assert "technical_details" not in payload
        assert (
            payload["finalization"]["business_failure_evidence"]["decisive_failure"][
                "error_code"
            ]
            == "SCHEDULE_INVOCATION_CONTRACT_MISSING"
        )
    finally:
        await _cleanup_audit(factory, run.tenant_id)


async def test_incomplete_facts_override_a_guessing_model_reply(
    async_tool_database, monkeypatch, fake_resolve
) -> None:
    call_id = "call-code-only"
    snapshot = TerminationSnapshot(
        status="NO_PROGRESS",
        turns_used=2,
        turns_limit=20,
        tool_calls_used=2,
        tool_calls_limit=30,
        failure_state=FailureState(
            last_failure=FailureEvidence(
                tool="draft_schedule",
                tool_call_id=call_id,
                code="SCHEDULE_INVOCATION_CONTRACT_MISSING",
                message="该 Skill 缺少可调度的调用契约",
                status="FAILED",
                failure_layer="TOOL",
            ),
            no_progress=True,
            no_progress_trigger_call_id=call_id,
            no_progress_source_call_id=call_id,
        ),
    )

    async def prepare(factory, run):
        await _insert_audit(
            factory,
            run,
            tool_call_id=call_id,
            tool_name="draft_schedule",
            business_status="FAILED",
            failure_layer="TOOL",
            error_code="SCHEDULE_INVOCATION_CONTRACT_MISSING",
        )

    guessed = "可能是 default_script 缺失；我改用 AGENT_TURN 再试一次。"
    factory, run, events = await _stream(
        async_tool_database,
        monkeypatch,
        fake_resolve,
        data={**_outcome_data("NO_PROGRESS", snapshot), "failure_reply": guessed},
        prepare=prepare,
    )
    try:
        payload = events[-1].data
        body = payload["final_text"]
        assert guessed not in body
        assert "具体配置原因尚未确定" in body
        assert "default_script" not in body
        assert "AGENT_TURN" not in body
        assert "SCHEDULE_INVOCATION_CONTRACT_MISSING" not in body
        assert "technical_details" not in payload
        assert payload["finalization"]["business_failure_evidence"]["decisive_failure"][
            "tool_call_id"
        ] == call_id
        deltas = [
            str(event.data.get("delta") or "")
            for event in events
            if event.type == "message.delta"
        ]
        assert all(guessed not in delta for delta in deltas)
        assert all("AGENT_TURN" not in delta for delta in deltas)
        async with factory() as session:
            message = await session.scalar(
                sa.select(CanonicalEvent).where(
                    CanonicalEvent.run_id == run.id,
                    CanonicalEvent.event_type == "ASSISTANT_MESSAGE",
                )
            )
        assert message is not None
        assert message.payload_json["text"] == payload["final_text"]
    finally:
        await _cleanup_audit(factory, run.tenant_id)


@pytest.mark.parametrize("failure", [None, SourceCancellationRequested("source cancelled")])
async def test_cancel_writes_completed_cancelled_without_run_failed(
    async_tool_database, monkeypatch, fake_resolve, failure
) -> None:
    factory, run, events = await _stream(
        async_tool_database,
        monkeypatch,
        fake_resolve,
        data=None,
        cancel=True,
        failure=failure,
    )

    terminal = events[-1]
    assert terminal.type == "run.completed"
    assert terminal.data["status"] == "CANCELLED"
    assert terminal.data["reason"] == "cancelled"
    assert "用户停止" in terminal.data["final_text"]

    async with factory() as session:
        saved = await session.get(RunRecord, run.id)
        assert saved.status is RunStatus.CANCELLED
        assert saved.error_code is None
        failed = await session.scalar(
            sa.select(CanonicalEvent.id).where(
                CanonicalEvent.run_id == run.id,
                CanonicalEvent.event_type == "RUN_FAILED",
            )
        )
        assert failed is None, "取消不是失败，不写 RUN_FAILED"
        cancel_event = await session.scalar(
            sa.select(CanonicalEvent).where(
                CanonicalEvent.run_id == run.id,
                CanonicalEvent.event_type == "CANCEL",
            )
        )
        assert cancel_event is not None
        assert cancel_event.payload_json["final_text"] == terminal.data["final_text"]
