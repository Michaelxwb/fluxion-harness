"""B-04/B-05: real PostgreSQL generations, checkpoints and execution guards."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from muad_agent_core.agent.continuation import RunnerCheckpoint, WaitDecision
from muad_agent_core.agent.runner import AgentPolicy, AgentRunner, AgentRunRequest, AgentRunStatus
from muad_agent_core.model import ModelMessage, ModelResponse, ModelRole
from muad_agent_core.tools import ToolRegistry
from muad_agent_runtime.application.async_tools.continuation_service import (
    ExecutionLeaseLost,
    checkpoint_execution,
    claim_continuation,
    renew_execution,
    try_wait,
)
from muad_agent_runtime.application.async_tools.materialization import materialize_results
from muad_agent_runtime.infrastructure.models.async_tools import RunContinuation
from muad_agent_runtime.infrastructure.models.runtime import CanonicalEvent, Conversation, RunRecord
from muad_contracts import RunStatus
from sqlalchemy import select

from tests.async_tool_continuations import operation, receipt, running_source


async def test_b04_durable_created_start_can_be_claimed_without_request_generator(async_tool_database):
    from muad_agent_runtime.infrastructure.models.runtime import RunSubmission
    factory, context, skill, run, identity = await running_source(async_tool_database)
    async with factory() as session, session.begin():
        saved = await session.get(RunRecord, run.id)
        saved.status, saved.execution_epoch = RunStatus.CREATED, 0
        saved.lease_owner = saved.lease_until = None
        session.add(RunSubmission(tenant_id=run.tenant_id, run_id=run.id,
            conversation_id=run.conversation_id, idempotency_key=str(uuid4()), endpoint="create-run",
            request_fingerprint="sha256:" + "e" * 64, actor_user_id=run.user_id))
    claims = await asyncio.gather(*[claim_continuation(factory, instance_id=owner, lease_sec=30)
        for owner in ("start-a", "start-b")])
    assert sum(row is not None for row in claims) == 1
    winner = next(row for row in claims if row is not None)
    assert winner.id == run.id and winner.status == RunStatus.RUNNING and winner.execution_epoch == 1
    async with factory() as session:
        assert await session.scalar(select(RunContinuation.id)) is None


@pytest.mark.parametrize("status", [RunStatus.WAITING_TOOL, RunStatus.WAITING_INPUT])
async def test_b05_absolute_deadline_closes_wait_and_late_success_cannot_resume(async_tool_database, status):
    from muad_agent_runtime.application.run_deadline import expire_runs
    from muad_agent_runtime.infrastructure.models.async_tools import ToolControlOutbox, ToolResultInbox
    from muad_agent_runtime.infrastructure.models.runtime import RunInterrupt, RunSubmission
    factory, context, skill, run, identity = await running_source(async_tool_database)
    op = await operation(factory, context, skill)
    await try_wait(factory, identity, RunnerCheckpoint(turns=1))
    async with factory() as session, session.begin():
        saved = await session.get(RunRecord, run.id)
        saved.status, saved.deadline_at = status, datetime.now(UTC) - timedelta(seconds=1)
        session.add(RunInterrupt(tenant_id=run.tenant_id, run_id=run.id,
            conversation_id=run.conversation_id, interrupt_type="CONFIRMATION", prompt_text="approve?"))
        session.add(RunSubmission(tenant_id=run.tenant_id, run_id=run.id,
            conversation_id=run.conversation_id, idempotency_key=str(uuid4()), endpoint="create-run",
            request_fingerprint="sha256:" + "d" * 64, actor_user_id=run.user_id))
    assert await expire_runs(factory) == 1
    await receipt(factory, run, op, {"late_success": True})
    async with factory() as session:
        saved = await session.get(RunRecord, run.id)
        assert saved.status == RunStatus.FAILED and saved.error_code == "RUN_DEADLINE_EXCEEDED"
        assert saved.lease_owner is None and saved.lease_until is None
        assert (await session.scalar(select(RunInterrupt))).status == "CANCELLED"
        submission = await session.scalar(select(RunSubmission))
        assert submission.status == "CLOSED" and submission.last_seq is not None
        inbox = await session.scalar(select(ToolResultInbox))
        assert inbox.late and not inbox.materialized
        assert await session.scalar(
            select(ToolControlOutbox.id).where(ToolControlOutbox.command == "CANCEL_OPERATION")
        )
        assert (
            await session.scalar(
                select(CanonicalEvent.id).where(CanonicalEvent.event_type == "BACKGROUND_RESULT")
            )
            is None
        )
    assert await claim_continuation(factory, instance_id="late", lease_sec=30) is None


async def test_b04_ready_run_is_not_starved_by_older_unready_waiters(async_tool_database):
    factory, context, skill, run, identity = await running_source(async_tool_database)
    op = await operation(factory, context, skill)
    await try_wait(factory, identity, RunnerCheckpoint())
    async with factory() as session, session.begin():
        for _ in range(128):
            waiting_id = uuid4()
            conversation_id = uuid4()
            session.add(Conversation(id=conversation_id, tenant_id=run.tenant_id,
                user_id=run.user_id, agent_id=run.agent_id, status="ACTIVE", last_seq=0))
            await session.flush()
            session.add(RunRecord(id=waiting_id, tenant_id=run.tenant_id,
                conversation_id=conversation_id, user_id=run.user_id, agent_id=run.agent_id,
                snapshot_id=run.snapshot_id, status=RunStatus.WAITING_TOOL, input_text="waiting",
                trace_id=run.trace_id, cancel_requested=False,
                update_time=datetime.now(UTC) - timedelta(minutes=1)))
            await session.flush()
            session.add(RunContinuation(tenant_id=run.tenant_id, run_id=waiting_id,
                snapshot_id=run.snapshot_id, runner_state_json={}, context_upto_seq=0,
                consumed_event_seq=0, turns=0, tool_calls=0, wait_generation=1))
    await receipt(factory, run, op, {"ready": True})
    async with factory() as session, session.begin():
        saved = await session.scalar(select(RunContinuation).where(RunContinuation.run_id == run.id))
        saved.ready = False  # The wake-up hint is not the authoritative queue.
    claimed = await claim_continuation(factory, instance_id="fair-owner", lease_sec=30)
    assert claimed is not None and claimed.id == run.id


async def test_b04_single_claim_generation_epoch_and_expired_owner(async_tool_database):
    factory, context, skill, run, identity = await running_source(async_tool_database)
    op = await operation(factory, context, skill)
    progress = RunnerCheckpoint(turns=2, tool_calls=1, input_tokens=12, output_tokens=None)
    assert await try_wait(factory, identity, progress, assistant_text="working") == WaitDecision.WAIT
    async with factory() as session:
        saved = await session.get(RunRecord, run.id)
        cp = (await session.scalars(select(RunContinuation))).one()
        assert saved.status == RunStatus.WAITING_TOOL and saved.lease_owner is None
        assert saved.lease_until is None and cp.wait_generation == 1
        assert cp.turns == 2 and cp.output_tokens is None
        history = list(await session.scalars(select(CanonicalEvent).order_by(CanonicalEvent.seq)))
        assert [item.event_type for item in history] == ["ASSISTANT_MESSAGE", "RUN_WAITING_TOOL"]
    assert await claim_continuation(factory, instance_id="idle", lease_sec=30) is None
    await receipt(factory, run, op, {"actual": "done"})
    assert await claim_continuation(factory, instance_id="stale", lease_sec=30, wait_generation=0) is None
    winners = await asyncio.gather(
        *[
            claim_continuation(factory, instance_id=owner, lease_sec=30, wait_generation=1)
            for owner in ("new-a", "new-b")
        ]
    )
    winner = next(value for value in winners if value is not None)
    assert sum(value is not None for value in winners) == 1
    assert winner.execution_epoch == 2 and winner.status == RunStatus.RUNNING
    assert not await renew_execution(factory, identity, lease_sec=30)
    with pytest.raises(ExecutionLeaseLost):
        await checkpoint_execution(factory, identity, progress)
    with pytest.raises(ExecutionLeaseLost):
        await try_wait(factory, replace(identity, owner=winner.lease_owner), progress)
    resumed = replace(identity, owner=winner.lease_owner, epoch=2)
    async with factory() as session, session.begin():
        saved = await session.get(RunRecord, run.id)
        saved.lease_until = datetime.now(UTC) - timedelta(seconds=1)
    assert not await renew_execution(factory, resumed, lease_sec=30)
    with pytest.raises(ExecutionLeaseLost):
        await checkpoint_execution(factory, resumed, progress)


async def test_b04_unconsumed_batch_cannot_sleep_or_repeat(async_tool_database):
    factory, context, skill, run, identity = await running_source(async_tool_database)
    ops = [await operation(factory, context, skill, call=f"call-{index}") for index in range(3)]
    await checkpoint_execution(factory, identity, RunnerCheckpoint())
    for index, op in enumerate(ops):
        await receipt(factory, run, op, {"index": index})
    first = await materialize_results(factory, identity, batch_limit=1)
    assert len(first) == 1
    assert await try_wait(factory, identity, RunnerCheckpoint()) == WaitDecision.CONTINUE
    rest = await materialize_results(factory, identity, batch_limit=1)
    assert len(rest) == 1 and rest[0].seq > first[0].seq
    assert (
        await try_wait(factory, identity, RunnerCheckpoint(consumed_event_seq=first[0].seq))
        == WaitDecision.CONTINUE
    )
    last = await materialize_results(factory, identity, batch_limit=1)
    assert len(last) == 1 and last[0].seq > rest[0].seq
    assert await materialize_results(factory, identity, batch_limit=1) == ()
    progress = RunnerCheckpoint(turns=1, consumed_event_seq=last[0].seq)
    await checkpoint_execution(factory, identity, progress)
    assert await try_wait(factory, identity, progress) == WaitDecision.FINISH
    async with factory() as session:
        events = list(
            await session.scalars(
                select(CanonicalEvent).where(CanonicalEvent.event_type == "BACKGROUND_RESULT")
            )
        )
        assert len(events) == 3 and len({row.source_event_id for row in events}) == 3


async def test_b04_waiting_input_never_claimed_and_running_never_double_claimed(async_tool_database):
    factory, context, skill, run, identity = await running_source(async_tool_database)
    op = await operation(factory, context, skill)
    await checkpoint_execution(factory, identity, RunnerCheckpoint())
    await receipt(factory, run, op, {"result": True})
    assert await claim_continuation(factory, instance_id="other", lease_sec=30) is None
    async with factory() as session, session.begin():
        saved = await session.get(RunRecord, run.id)
        saved.status = RunStatus.WAITING_INPUT
        saved.lease_owner = saved.lease_until = None
    assert await claim_continuation(factory, instance_id="other", lease_sec=30) is None
    async with factory() as session:
        cp = (await session.scalars(select(RunContinuation))).one()
        assert cp.ready and (await session.get(RunRecord, run.id)).status == RunStatus.WAITING_INPUT


async def test_b05_multiple_waits_preserve_budget_unknown_usage_and_deadline(async_tool_database):
    factory, context, skill, run, identity = await running_source(async_tool_database)
    original_deadline = run.deadline_at
    progress = RunnerCheckpoint(turns=3, tool_calls=2, input_tokens=None, output_tokens=19)
    for generation in (1, 2):
        op = await operation(factory, context, skill, call=f"wait-{generation}")
        assert await try_wait(factory, identity, progress) == WaitDecision.WAIT
        await receipt(factory, run, op, {"round": generation})
        claimed = await claim_continuation(factory, instance_id=f"owner-{generation}", lease_sec=60)
        identity = replace(identity, owner=claimed.lease_owner, epoch=claimed.execution_epoch)
        events = await materialize_results(factory, identity)
        progress = replace(progress, consumed_event_seq=events[-1].seq)
        await checkpoint_execution(factory, identity, progress)
        async with factory() as session:
            saved = await session.get(RunRecord, run.id)
            cp = (await session.scalars(select(RunContinuation))).one()
            assert saved.deadline_at == original_deadline and cp.wait_generation == generation
            assert (cp.turns, cp.tool_calls, cp.input_tokens, cp.output_tokens) == (3, 2, None, 19)

    class MustNotCall:
        async def complete(self, request):
            raise AssertionError("exhausted frozen budget must prevent provider IO")

    runner = AgentRunner(provider=MustNotCall(), registry=ToolRegistry())
    result = await runner.run(
        AgentRunRequest(model_id="probe", instructions="", policy=AgentPolicy(max_turns=3)),
        checkpoint=progress,
    )
    assert result.status == AgentRunStatus.BUDGET_EXCEEDED and result.turns == 3
    assert result.usage.input_tokens is None and result.usage.output_tokens == 19
    with pytest.raises(ExecutionLeaseLost):
        await checkpoint_execution(factory, identity, progress, now=original_deadline + timedelta(seconds=1))


async def test_b05_restored_runner_reports_unknown_provider_usage():
    class NoUsage:
        async def complete(self, request):
            return ModelResponse(content="done", finish_reason="stop")

    runner = AgentRunner(provider=NoUsage(), registry=ToolRegistry())
    result = await runner.run(
        AgentRunRequest(model_id="probe", instructions="", messages=(ModelMessage(ModelRole.USER, "go"),)),
        checkpoint=RunnerCheckpoint(turns=1, input_tokens=5, output_tokens=2),
    )
    assert result.turns == 2 and result.usage.input_tokens is None and result.usage.output_tokens is None


async def test_b05_checkpoint_rejects_incomplete_tool_round(async_tool_database):
    from muad_agent_runtime.application.run_events import EventWriter
    factory, context, skill, run, identity = await running_source(async_tool_database)
    async with factory() as session, session.begin():
        await EventWriter(session).append(tenant_id=run.tenant_id, conversation_id=run.conversation_id,
            run_id=run.id, event_type="ASSISTANT_TURN", payload={"tool_calls": [
                {"id": "unfinished", "name": "execute_skill", "arguments": {}}]})
    with pytest.raises(ValueError, match="complete tool round"):
        await checkpoint_execution(factory, identity, RunnerCheckpoint(turns=1))
    async with factory() as session:
        assert await session.scalar(select(RunContinuation.id)) is None


async def test_b05_exhausted_restored_budget_persists_failed_and_cancels_join(
    async_tool_database, monkeypatch, fake_resolve
):
    from uuid import uuid4

    from muad_agent_runtime.application import run_service
    from muad_agent_runtime.application.async_tools.supervisor import ExecutionSupervisor
    from muad_agent_runtime.application.context_builder import DbBackedContextBuilder
    from muad_agent_runtime.application.executor import AgentRunnerExecutor
    from muad_agent_runtime.application.ports import NullPlatformSettingsClient
    from muad_agent_runtime.application.run_submission import ENDPOINT_CREATE_RUN, RunSubmissionService
    from muad_agent_runtime.infrastructure.models.async_tools import ToolControlOutbox
    from muad_contracts import ControlCommand
    factory, context, skill, run, identity = await running_source(async_tool_database)
    await operation(factory, context, skill)
    await checkpoint_execution(factory, identity, RunnerCheckpoint(turns=3, tool_calls=1, input_tokens=None))
    monkeypatch.setattr(run_service, "get_session_factory", lambda: factory)
    class MustNotCall:
        async def complete(self, request):
            raise AssertionError("restored exhausted budget must prevent model invocation")
    async def executor_factory(request):
        runner = AgentRunner(
            provider=MustNotCall(), registry=ToolRegistry(), runtime_events=request.runtime_events
        )
        return AgentRunnerExecutor(runner=runner, request=request)
    agent = context.agent.model_copy(update={"runtime_config": {"max_turns": 3}})
    supervisor = ExecutionSupervisor()
    async with factory() as session:
        run = await session.get(RunRecord, run.id)
        submission = await RunSubmissionService(lambda: factory).record_in(session, tenant_id=run.tenant_id,
            idempotency_key=str(uuid4()), endpoint=ENDPOINT_CREATE_RUN, actor_user_id=run.user_id,
            run_id=run.id, conversation_id=run.conversation_id, request_fingerprint="sha256:" + "c" * 64)
        await session.commit()
        service = run_service.RunService(session, fake_resolve, identity.owner, NullPlatformSettingsClient(),
            supervisor=supervisor, executor_factory=executor_factory,
            context_builder=DbBackedContextBuilder(session_factory=lambda: factory))
        events = [event async for event in service._stream_run(run=run, agent=agent, model=context.model,
            skills=(), mcp_servers=(), mcp_secrets={}, history=(), submission_id=submission.id, resumed=True)]
    assert events[-1].type == "run.failed" and events[-1].data["error_code"] == "RUN_BUDGET_EXCEEDED"
    async with factory() as session:
        saved = await session.get(RunRecord, run.id)
        assert saved.status == RunStatus.FAILED and saved.lease_owner is None
        cp = (await session.scalars(select(RunContinuation))).one()
        assert cp.turns == 3 and cp.tool_calls == 1 and cp.input_tokens is None
        assert await session.scalar(
            select(ToolControlOutbox.id).where(
                ToolControlOutbox.command == ControlCommand.CANCEL_OPERATION
            )
        )
    await supervisor.close()


async def test_b04_real_runner_waits_then_reconstructs_without_duplicate_tool_response(
    async_tool_database, monkeypatch, fake_resolve
):
    import json
    from uuid import uuid4

    from muad_agent_core.model import ModelToolCall
    from muad_agent_core.tools import ToolDefinition, ToolEffect
    from muad_agent_runtime.application import run_service
    from muad_agent_runtime.application.async_tools.supervisor import ExecutionSupervisor
    from muad_agent_runtime.application.context_builder import DbBackedContextBuilder
    from muad_agent_runtime.application.executor import AgentRunnerExecutor
    from muad_agent_runtime.application.ports import NullPlatformSettingsClient
    from muad_agent_runtime.application.run_submission import ENDPOINT_CREATE_RUN, RunSubmissionService
    factory, context, skill, source, identity = await running_source(async_tool_database)
    monkeypatch.setattr(run_service, "get_session_factory", lambda: factory)
    submitted, requests = [], []
    async def submit(arguments, *, call_id):
        op = await operation(factory, context, skill, call=call_id)
        submitted.append(op)
        return json.dumps({"operation_id": str(op.id), "submission_status": "PENDING", "task_id": None})
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            "execute_skill", "submit", {"type": "object", "properties": {}}, ToolEffect.EXTERNAL, submit
        )
    )
    class Provider:
        async def complete(self, request):
            requests.append(request)
            if len(requests) == 1:
                call = ModelToolCall("original-call", "execute_skill", {})
                return ModelResponse(content="", finish_reason="tool_calls", tool_calls=(call,))
            external = [
                message
                for message in request.messages
                if message.role == ModelRole.USER and "BACKGROUND_RESULT" in str(message.content)
            ]
            final = str(external[-1].content) if external else "working"
            return ModelResponse(content=final, finish_reason="stop")
    provider = Provider()
    async def executor_factory(request):
        runner = AgentRunner(provider=provider, registry=registry, runtime_events=request.runtime_events)
        return AgentRunnerExecutor(runner=runner, request=request)
    supervisor = ExecutionSupervisor()
    async with factory() as session:
        run = await session.get(RunRecord, source.id)
        submission = await RunSubmissionService(lambda: factory).record_in(session, tenant_id=run.tenant_id,
            idempotency_key=str(uuid4()), endpoint=ENDPOINT_CREATE_RUN, actor_user_id=run.user_id,
            run_id=run.id, conversation_id=run.conversation_id, request_fingerprint="sha256:" + "d" * 64)
        await session.commit()
        service = run_service.RunService(session, fake_resolve, identity.owner, NullPlatformSettingsClient(),
            supervisor=supervisor, executor_factory=executor_factory,
            context_builder=DbBackedContextBuilder(session_factory=lambda: factory))
        first = [
            event
            async for event in service._stream_run(
                run=run,
                agent=context.agent,
                model=context.model,
                skills=(),
                mcp_servers=(),
                mcp_secrets={},
                history=(ModelMessage(ModelRole.USER, "do the work"),),
                submission_id=submission.id,
                resumed=False,
            )
        ]
        assert not any(event.type == "run.completed" for event in first)
        async with factory() as inspect_session:
            saved = await inspect_session.get(RunRecord, run.id)
            cp = (await inspect_session.scalars(select(RunContinuation))).one()
            assert saved.status == RunStatus.WAITING_TOOL and saved.lease_owner is None
            assert (cp.turns, cp.tool_calls) == (2, 1)
        await receipt(factory, source, submitted[0], {"actual_result": "from PostgreSQL"})
        restored = await claim_continuation(factory, instance_id="restorer", lease_sec=60)
        second = [
            event
            async for event in service._stream_run(
                run=restored,
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
        assert second[-1].type == "run.completed" and "from PostgreSQL" in second[-1].data["final_text"]
    assert sum(message.role == ModelRole.TOOL for message in requests[-1].messages) == 1
    assert sum("BACKGROUND_RESULT" in str(message.content) for message in requests[-1].messages) == 1
    assert len(submitted) == 1
    await supervisor.close()
