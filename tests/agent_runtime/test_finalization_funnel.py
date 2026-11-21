"""统一收尾漏斗：RUN_FAILED.finalization + ASSISTANT_MESSAGE / 取消不写失败。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

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

from tests.async_tool_continuations import running_source


class _OutcomeExecutor:
    def __init__(self, data: dict | None) -> None:
        self._data = data

    async def run(self):
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
):
    factory, context, _, run, identity = await running_source(async_tool_database)
    monkeypatch.setattr(run_service, "get_session_factory", lambda: factory)

    async def executor_factory(request):
        return _OutcomeExecutor(data)

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


async def _insert_audit(factory, run, **fields) -> None:
    async with factory() as session:
        session.add(
            ToolCallAudit(
                id=uuid.uuid4(),
                tenant_id=run.tenant_id,
                run_id=run.id,
                conversation_id=run.conversation_id,
                tool_call_id=f"call-{uuid.uuid4().hex[:8]}",
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


async def test_business_failure_is_read_from_tool_audit(
    async_tool_database, monkeypatch, fake_resolve
) -> None:
    snapshot = TerminationSnapshot(
        status="NO_PROGRESS",
        turns_used=1,
        turns_limit=20,
        tool_calls_used=1,
        tool_calls_limit=30,
        failure_state=FailureState(
            last_failure=FailureEvidence(
                tool="execute",
                code="PLATFORM_AUTH_REJECTED",
                message="mssw session rejected: HTTP 401",
                status="FAILED",
                failure_layer="CONTENT",
                exit_code=3,
            ),
            identical_failures={"execute:PLATFORM_AUTH_REJECTED:CONTENT": 1},
            no_progress=True,
        ),
    )

    async def prepare(factory, run):
        await _insert_audit(
            factory,
            run,
            tool_name="execute",
            business_status="FAILED",
            failure_layer="CONTENT",
            error_code="PLATFORM_AUTH_REJECTED",
            exit_code=3,
            output_truncated=False,
            evidence_preview="mssw session rejected: HTTP 401",
        )

    factory, run, events = await _stream(
        async_tool_database,
        monkeypatch,
        fake_resolve,
        data=_outcome_data("NO_PROGRESS", snapshot),
        prepare=prepare,
    )
    try:
        payload = events[-1].data
        assert payload["error_code"] == "RUN_NO_PROGRESS"
        evidence = payload["finalization"]["business_failure_evidence"]
        assert payload["finalization"]["evidence_status"] == "CONFIRMED_FAILURE"
        assert evidence["last_failure"]["error_code"] == "PLATFORM_AUTH_REJECTED"
        assert evidence["decisive_failure"]["tool"] == "execute"
        assert "PLATFORM_AUTH_REJECTED" in payload["final_text"]
    finally:
        await _cleanup_audit(factory, run.tenant_id)


async def test_cancel_writes_completed_cancelled_without_run_failed(
    async_tool_database, monkeypatch, fake_resolve
) -> None:
    factory, run, events = await _stream(
        async_tool_database,
        monkeypatch,
        fake_resolve,
        data=None,
        cancel=True,
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
