"""TASK-011 auxiliary B-15 evidence; final Gateway/resume scenario remains TASK-017's."""

import asyncio
import json
from uuid import uuid4

import pytest
import sqlalchemy as sa
from muad_agent_core.agent.continuation import RunnerCheckpoint
from muad_agent_runtime.infrastructure.models.runtime import RunRecord, UserMemory
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts.memory import memory_hash

from tests.agent_runtime.memory_confirmation_support import (
    candidate,
    complete_round,
    run_model_round,
    service,
    source,
    state,
)
from tests.agent_runtime.memory_redesign_write_support import command, context, upsert, writer

pytestmark = pytest.mark.integration


async def test_local_b15_proposal_does_not_write_fact_interrupt_or_release_run(async_tool_database):
    factory, run, identity, submission = await source(async_tool_database)
    memory = service(factory, identity, submission)
    receipt = await memory.propose(candidate(), call_id="remember-1")
    current, interrupts, saved, events = await state(factory, run)
    assert receipt.saved is False and receipt.confirmation_required is True
    assert current.status == "RUNNING" and current.lease_owner == identity.owner
    assert interrupts == [] and saved is None
    assert memory.suspend_request.proposal.candidate_hash == memory_hash(candidate().model_dump(mode="json"))
    async with factory() as session:
        assert await session.scalar(sa.select(sa.func.count()).select_from(UserMemory)) == 0


@pytest.mark.parametrize("stored", ["deleted", "disabled", "expired"])
async def test_local_b15_proposal_reads_all_states_and_binds_version_epoch_and_deadline(
    async_tool_database, stored
):
    factory, run, identity, submission = await source(async_tool_database, deadline_seconds=120)
    ctx = context(tenant=run.tenant_id, user=run.user_id)
    await writer(async_tool_database).execute(ctx, command(upsert()))
    async with factory.begin() as session:
        await session.execute(
            sa.text(
                "UPDATE runtime.user_memory SET is_deleted=:deleted,enabled=:enabled,"
                " expires_at=CASE WHEN :expired THEN now()-interval '1 day' ELSE NULL END"
                " WHERE tenant_id=:tenant"
            ),
            {
                "deleted": stored == "deleted",
                "enabled": stored != "disabled",
                "expired": stored == "expired",
                "tenant": run.tenant_id,
            },
        )
        await session.execute(
            sa.text("UPDATE runtime.memory_scope SET forget_epoch=7 WHERE tenant_id=:tenant"),
            {"tenant": run.tenant_id},
        )
    memory = service(factory, identity, submission)
    await memory.propose(candidate(), call_id="remember-1")
    proposal = memory.suspend_request.proposal
    assert proposal.expected_version == 1 and proposal.forget_epoch == 7
    assert proposal.tenant_id == run.tenant_id and proposal.user_id == run.user_id
    assert proposal.submission_id == submission and proposal.run_id == run.id
    async with factory() as session:
        current = await session.get(RunRecord, run.id)
    assert proposal.expires_at == current.deadline_at
    assert {"deleted": "已删除", "disabled": "已禁用", "expired": "已过期"}[
        stored
    ] in memory.suspend_request.prompt
    assert "blue" in memory.suspend_request.prompt and "color" in memory.suspend_request.prompt


async def test_local_b15_second_remember_is_rejected_without_overwriting_first(async_tool_database):
    factory, run, identity, submission = await source(async_tool_database)
    memory = service(factory, identity, submission)
    outcomes = await asyncio.gather(
        memory.propose(candidate(), call_id="one"),
        memory.propose(candidate(value="red"), call_id="two"),
        return_exceptions=True,
    )
    failures = [item for item in outcomes if isinstance(item, AppError)]
    assert len(failures) == 1 and failures[0].code == ErrorCode.MEMORY_CONFIRMATION_PENDING
    assert memory.suspend_request.proposal.candidate.value in ("blue", "red")


async def test_local_b15_complete_checkpoint_persists_pending_once_and_leaves_tool_result_immutable(
    async_tool_database,
):
    factory, run, identity, submission = await source(async_tool_database)
    memory = service(factory, identity, submission)
    await memory.propose(candidate(), call_id="remember-1")
    proposal = memory.suspend_request
    await complete_round(factory, run)
    await memory.suspend(RunnerCheckpoint(turns=1, tool_calls=1, input_tokens=3, output_tokens=2))
    current, interrupts, saved, events = await state(factory, run)
    assert current.status == "WAITING_INPUT" and current.lease_owner is current.lease_until is None
    assert len(interrupts) == 1 and interrupts[0].proposal_json == proposal.proposal.model_dump(mode="json")
    assert (
        interrupts[0].prompt_text == proposal.prompt
        and interrupts[0].expires_at == proposal.proposal.expires_at
    )
    assert saved.turns == saved.tool_calls == 1 and saved.input_tokens == 3
    assert saved.context_upto_seq == events[-1].seq
    assert events[-1].event_type == "MEMORY_CONFIRMATION_REQUIRED"
    assert [event.payload_json["preview"] for event in events if event.event_type == "TOOL_CALL"] == [
        '{"saved":false}'
    ]
    fresh = service(factory, identity, submission)
    with pytest.raises(AppError) as exc:
        await fresh.propose(candidate(value="red"), call_id="new")
    assert exc.value.code == ErrorCode.MEMORY_CONFIRMATION_PENDING


async def test_local_b15_incomplete_round_refuses_pause_and_keeps_original_lease(async_tool_database):
    factory, run, identity, submission = await source(async_tool_database)
    memory = service(factory, identity, submission)
    await memory.propose(candidate(), call_id="remember-1")
    await complete_round(factory, run, call_ids=("remember-1", "other"), answer_ids=("remember-1",))
    with pytest.raises(ValueError, match="complete tool round"):
        await memory.suspend(RunnerCheckpoint(turns=1, tool_calls=2))
    current, interrupts, saved, _ = await state(factory, run)
    assert current.status == "RUNNING" and current.lease_owner == identity.owner
    assert interrupts == [] and saved is None


async def test_local_b15_event_failure_rolls_back_interrupt_checkpoint_status_and_lease(async_tool_database):
    factory, run, identity, submission = await source(async_tool_database)
    memory = service(factory, identity, submission)
    await memory.propose(candidate(), call_id="remember-1")
    await complete_round(factory, run)
    async with factory.begin() as session:
        await session.execute(
            sa.text(
                "CREATE FUNCTION runtime.fail_memory_proposal() RETURNS trigger"
                " LANGUAGE plpgsql AS $$ BEGIN IF NEW.event_type='MEMORY_CONFIRMATION_REQUIRED' THEN"
                " RAISE EXCEPTION 'proposal_event_failure'; END IF; RETURN NEW; END $$"
            )
        )
        await session.execute(
            sa.text(
                "CREATE TRIGGER fail_memory_proposal BEFORE INSERT ON runtime.canonical_event"
                " FOR EACH ROW EXECUTE FUNCTION runtime.fail_memory_proposal()"
            )
        )
    with pytest.raises(AppError) as exc:
        await memory.suspend(RunnerCheckpoint(turns=1, tool_calls=1))
    assert exc.value.code == ErrorCode.COMMON_INTERNAL_ERROR
    current, interrupts, saved, events = await state(factory, run)
    assert current.status == "RUNNING" and current.lease_owner == identity.owner
    assert interrupts == [] and saved is None and events[-1].event_type == "TOOL_CALL"


@pytest.mark.parametrize("invalid", ["actor", "event", "lease"])
async def test_local_b15_proposal_rejects_untrusted_source_or_executor(async_tool_database, invalid):
    factory, run, identity, submission = await source(async_tool_database)
    async with factory.begin() as session:
        if invalid == "actor":
            await session.execute(
                sa.text("UPDATE runtime.run_submission SET actor_user_id=:actor"), {"actor": uuid4()}
            )
        elif invalid == "event":
            await session.execute(
                sa.text("UPDATE runtime.canonical_event SET event_type='ASSISTANT_MESSAGE'")
            )
        else:
            await session.execute(sa.text("UPDATE runtime.run_record SET lease_owner='other-owner'"))
    with pytest.raises((AppError, RuntimeError)):
        await service(factory, identity, submission).propose(candidate(), call_id="remember-1")


async def test_local_b15_quote_is_evidence_only_and_forbidden_value_never_becomes_proposal(
    async_tool_database,
):
    factory, run, identity, submission = await source(async_tool_database)
    memory = service(factory, identity, submission)
    with pytest.raises(AppError) as exc:
        await memory.propose(candidate(value="password=secret"), call_id="forbidden")
    assert exc.value.code == ErrorCode.MEMORY_FORBIDDEN_CONTENT and memory.suspend_request is None
    await memory.propose(candidate(source_quote="记住"), call_id="quote")
    assert memory.suspend_request.proposal.evidence.source_quote is None
    assert memory.suspend_request.proposal.candidate.source_quote is None


@pytest.mark.parametrize("tool_budget", [3, 1])
async def test_local_b15_real_runner_finishes_all_tools_and_audits_then_stops_model_calls(
    async_tool_database,
    tool_budget,
):
    from muad_agent_core.agent import AgentRunStatus
    from muad_agent_runtime.infrastructure.models.runtime import ToolCallAudit

    factory, run, identity, submission = await source(async_tool_database)
    memory = service(factory, identity, submission)
    tool_calls = [
        {"id": key, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
        for key, name, args in [
            ("remember-1", "remember", candidate().model_dump()),
            ("remember-2", "remember", candidate(value="red").model_dump()),
            ("echo", "echo", {}),
        ]
    ]
    result, model_calls = await run_model_round(factory, run, memory, tool_calls, tool_budget)
    assert (
        result.status == AgentRunStatus.WAITING_INPUT
        and result.turns == 1
        and result.tool_calls == tool_budget
    )
    assert model_calls == 1
    current, interrupts, saved, events = await state(factory, run)
    tools = [event for event in events if event.event_type == "TOOL_CALL"]
    by_call = {event.payload_json["tool_call_id"]: event.payload_json for event in tools}
    assert set(by_call) == {"remember-1", "remember-2", "echo"}
    assert json.loads(by_call["remember-1"]["preview"])["saved"] is False
    if tool_budget == 3:
        assert json.loads(by_call["remember-2"]["preview"])["error"]["code"] == "MEMORY_CONFIRMATION_PENDING"
    else:
        assert by_call["remember-2"]["preview"] == by_call["echo"]["preview"] == "tool call budget exhausted"
    assert len(interrupts) == 1 and saved.context_upto_seq == events[-1].seq
    async with factory() as session:
        audits = list(await session.scalars(sa.select(ToolCallAudit).where(ToolCallAudit.run_id == run.id)))
    assert sorted(item.status for item in audits) == (["ERROR", "OK", "OK"] if tool_budget == 3 else ["OK"])
    assert current.status == "WAITING_INPUT" and current.lease_owner is None


async def test_local_b15_verified_quote_remains_evidence_and_expiry_is_at_most_five_minutes(
    async_tool_database,
):
    from datetime import UTC, datetime, timedelta

    factory, run, identity, submission = await source(async_tool_database)
    memory = service(factory, identity, submission)
    before = datetime.now(UTC)
    await memory.propose(candidate(source_quote="记住蓝色"), call_id="remember-1")
    proposal = memory.suspend_request.proposal
    assert before < proposal.expires_at <= datetime.now(UTC) + timedelta(seconds=300)
    assert proposal.evidence.source_quote == proposal.candidate.source_quote == "记住蓝色"
    assert "来源原话：记住蓝色" in memory.suspend_request.prompt
    assert proposal.candidate_hash == memory_hash(proposal.candidate.model_dump(mode="json"))
    async with factory() as session:
        assert await session.scalar(sa.select(sa.func.count()).select_from(UserMemory)) == 0


@pytest.mark.parametrize("gate", ["disabled", "canary"])
async def test_local_b15_frozen_write_policy_and_credentials_gate_proposal(async_tool_database, gate):
    from importlib import import_module

    from muad_contracts.platform_settings import MemoryPolicySettings

    factory, run, identity, submission = await source(async_tool_database)
    memory = import_module("muad_agent_runtime.application.memory_confirmation").MemoryConfirmationService(
        factory,
        identity,
        submission_id=submission,
        settings=MemoryPolicySettings(write_enabled=gate != "disabled"),
        credential_canaries=("blue",) if gate == "canary" else (),
    )
    with pytest.raises(AppError) as exc:
        await memory.propose(candidate(), call_id="remember-1")
    assert exc.value.code == (
        ErrorCode.FORBIDDEN if gate == "disabled" else ErrorCode.MEMORY_FORBIDDEN_CONTENT
    )
    assert memory.suspend_request is None


@pytest.mark.parametrize("changed", ["owner", "epoch", "deadline"])
async def test_local_b15_suspension_rechecks_execution_fence(async_tool_database, changed):
    factory, run, identity, submission = await source(async_tool_database)
    memory = service(factory, identity, submission)
    await memory.propose(candidate(), call_id="remember-1")
    await complete_round(factory, run)
    async with factory.begin() as session:
        current = await session.get(RunRecord, run.id)
        if changed == "owner":
            current.lease_owner = "new-owner"
        elif changed == "epoch":
            current.execution_epoch += 1
        else:
            current.deadline_at = await session.scalar(sa.select(sa.func.statement_timestamp()))
    with pytest.raises(RuntimeError):
        await memory.suspend(RunnerCheckpoint(turns=1, tool_calls=1))
    current, interrupts, saved, _ = await state(factory, run)
    assert current.status == "RUNNING" and interrupts == [] and saved is None


async def test_local_b15_lease_expiring_during_lock_wait_cannot_commit_pause(async_tool_database):
    from datetime import timedelta

    from muad_agent_runtime.infrastructure.models.runtime import Conversation

    factory, run, identity, submission = await source(async_tool_database)
    memory = service(factory, identity, submission)
    await memory.propose(candidate(), call_id="remember-1")
    await complete_round(factory, run)
    async with factory.begin() as session:
        current = await session.get(RunRecord, run.id)
        current.lease_until = await session.scalar(sa.select(sa.func.statement_timestamp())) + timedelta(
            seconds=0.2
        )
    async with factory.begin() as blocker:
        await blocker.scalar(
            sa.select(Conversation).where(Conversation.id == run.conversation_id).with_for_update()
        )
        pending = asyncio.create_task(memory.suspend(RunnerCheckpoint(turns=1, tool_calls=1)))
        await asyncio.sleep(0.06)
        assert not pending.done()
        await asyncio.sleep(0.25)
    with pytest.raises(RuntimeError):
        await pending
    current, interrupts, saved, _ = await state(factory, run)
    assert current.status == "RUNNING" and interrupts == [] and saved is None
