"""TASK-015 B-15 auxiliary coverage; final B-15 is owned by TASK-017."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import sqlalchemy as sa
from muad_agent_runtime.infrastructure.models.runtime import (
    CanonicalEvent,
    MemoryCommandReceipt,
    MemoryScope,
    RunInterrupt,
    RunRecord,
    RunSubmission,
    UserMemory,
    UserMemoryHistory,
)
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts.memory import memory_hash
from muad_contracts.platform_settings import MemoryPolicySettings

from tests.agent_runtime.memory_confirmation_resume_support import confirmation_stack, rows
from tests.agent_runtime.memory_context_support import memory_text
from tests.agent_runtime.memory_redesign_write_support import command, commands, context, upsert, writer

pytestmark = pytest.mark.integration


async def test_local_b15_real_gateway_confirmation_saves_displayed_content_and_new_receipt(
    async_tool_database, monkeypatch
):
    async with confirmation_stack(async_tool_database, monkeypatch) as stack:
        before = (await rows(stack, RunInterrupt))[0]
        events = await stack.submit(f"确认 {stack.proposal_id}")
        memories, history, receipts = (
            await rows(stack, UserMemory),
            await rows(stack, UserMemoryHistory),
            await rows(stack, MemoryCommandReceipt),
        )
        assert len(memories) == len(history) == len(receipts) == 1
        row = memories[0]
        assert row.author == "USER" and row.content_json == {"value": "blue"}
        assert row.explicit_request and row.confirmed_count == row.version == 1
        assert row.source_quote == "记住蓝色" and "blue" in before.prompt_text
        canonical = await rows(stack, CanonicalEvent)
        user = next(event for event in canonical if event.id == row.confirmation_event_id)
        assert user.event_type == "USER_MESSAGE" and user.payload_json["text"] == f"确认 {stack.proposal_id}"
        assert user.payload_json["source_scope_revision"] == 0
        resolved = next(event for event in canonical if event.event_type == "MEMORY_CONFIRMATION_RESOLVED")
        assert resolved.payload_json["saved"] is True and resolved.payload_json["version"] == row.version
        assert resolved.source_event_id == stack.proposal_id
        assert [event.payload_json["preview"] for event in canonical if event.event_type == "TOOL_CALL"] == [
            '{"saved":false}'
        ]
        assert events[-1].data["status"] == "COMPLETED"
        assert row.memory_key in memory_text(stack.captured[0])
        assert any(
            "saved" in item["content"] and "true" in item["content"]
            for item in stack.captured[0]["messages"]
            if item["role"] == "assistant"
        )


async def test_local_b15_reject_consumes_only_memory_interrupt_and_resumes_without_saving(
    async_tool_database, monkeypatch
):
    async with confirmation_stack(async_tool_database, monkeypatch) as stack:
        async with stack.factory.begin() as session:
            session.add(
                RunInterrupt(
                    tenant_id=stack.run.tenant_id,
                    run_id=stack.run.id,
                    conversation_id=stack.run.conversation_id,
                    interrupt_type="OTHER",
                    prompt_text="other",
                )
            )
        events = await stack.submit(f"拒绝 {stack.proposal_id}")
        assert await rows(stack, UserMemory) == await rows(stack, MemoryCommandReceipt) == []
        interrupts = {row.interrupt_type: row for row in await rows(stack, RunInterrupt)}
        assert interrupts["MEMORY_CONFIRMATION"].status == "RESOLVED"
        assert interrupts["MEMORY_CONFIRMATION"].resolution_json["outcome"] == "REJECTED"
        assert interrupts["OTHER"].status == "WAITING"
        assert events[-1].data["status"] == "COMPLETED" and len(stack.captured) == 1


@pytest.mark.parametrize("text", ["确认 2", "yes", "不要确认 {id}", "确认 {id} 吗", '{"action":"CONFIRM"}'])
async def test_local_b15_invalid_input_keeps_waiting_and_writes_nothing(
    async_tool_database, monkeypatch, text
):
    async with confirmation_stack(async_tool_database, monkeypatch) as stack:
        before = len(await rows(stack, CanonicalEvent)), len(await rows(stack, RunSubmission))
        with pytest.raises(AppError) as exc:
            await stack.submit(text.format(id=stack.proposal_id) if "{id}" in text else text)
        assert exc.value.code == ErrorCode.MEMORY_CONFIRMATION_INVALID
        assert (await rows(stack, RunRecord))[0].status == "WAITING_INPUT"
        assert (await rows(stack, RunInterrupt))[0].status == "WAITING"
        assert before == (len(await rows(stack, CanonicalEvent)), len(await rows(stack, RunSubmission)))
        assert stack.captured == [] and await rows(stack, UserMemory) == []


@pytest.mark.parametrize("stale", ["expiry", "version", "epoch", "hash", "hash_recomputed", "user"])
async def test_local_b15_stale_valid_choice_resumes_without_granting_memory(
    async_tool_database, monkeypatch, stale
):
    async with confirmation_stack(async_tool_database, monkeypatch) as stack:
        if stale in ("version", "epoch"):
            ctx = context(tenant=stack.run.tenant_id, user=stack.run.user_id)
            key = "color" if stale == "version" else "unrelated"
            await writer(async_tool_database).execute(ctx, command(upsert(key, "red")))
            if stale == "epoch":
                await writer(async_tool_database).execute(
                    ctx,
                    command(commands().MemoryMutation(action="DELETE", memory_key=key, expected_version=1)),
                )
        async with stack.factory.begin() as session:
            interrupt = await session.get(RunInterrupt, stack.proposal_id)
            if stale == "expiry":
                interrupt.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            elif stale not in ("version", "epoch"):
                proposal = dict(interrupt.proposal_json)
                if stale == "user":
                    proposal["user_id"] = str(uuid4())
                else:
                    proposal["candidate"] = {**proposal["candidate"], "value": "red"}
                    if stale == "hash_recomputed":
                        proposal["candidate_hash"] = memory_hash(proposal["candidate"])
                interrupt.proposal_json = proposal
        events = await stack.submit(f"确认 {stack.proposal_id}")
        assert all(row.author != "USER" for row in await rows(stack, UserMemory))
        interrupt = (await rows(stack, RunInterrupt))[0]
        assert interrupt.status == "RESOLVED" and interrupt.resolution_json["outcome"] == "STALE"
        assert events[-1].data["status"] == "COMPLETED"


async def test_local_b15_expired_absolute_run_deadline_prevents_save_and_resume(
    async_tool_database, monkeypatch
):
    async with confirmation_stack(async_tool_database, monkeypatch) as stack:
        async with stack.factory.begin() as session:
            run = await session.get(RunRecord, stack.run.id)
            run.deadline_at = datetime.now(UTC) - timedelta(seconds=1)
        with pytest.raises(AppError) as exc:
            await stack.submit(f"确认 {stack.proposal_id}")
        assert exc.value.code == ErrorCode.MEMORY_CONFIRMATION_EXPIRED
        assert (await rows(stack, RunRecord))[0].status == "WAITING_INPUT"
        assert (await rows(stack, RunInterrupt))[0].status == "WAITING"
        assert await rows(stack, UserMemory) == [] and stack.captured == []


async def test_local_b15_other_user_cannot_confirm_in_gateway_or_explicit_resume(
    async_tool_database, monkeypatch
):
    async with confirmation_stack(async_tool_database, monkeypatch) as stack:
        wrong = uuid4()
        with pytest.raises(AppError):
            await stack.submit(f"确认 {stack.proposal_id}", user=wrong)
        response = await stack.explicit(f"确认 {stack.proposal_id}", user=wrong)
        assert response.status_code == 404
        assert (await rows(stack, RunInterrupt))[0].status == "WAITING"
        assert await rows(stack, UserMemory) == [] and stack.captured == []
        response = await stack.explicit(f"确认 {stack.proposal_id}")
        assert response.status_code == 200 and len(await rows(stack, UserMemory)) == 1


async def test_local_b15_repeated_message_replays_and_changed_payload_is_rejected(
    async_tool_database, monkeypatch
):
    async with confirmation_stack(async_tool_database, monkeypatch) as stack:
        first = await stack.submit(f"确认 {stack.proposal_id}", message_id="stable-confirm")
        replay = await stack.submit(f"确认 {stack.proposal_id}", message_id="stable-confirm")
        assert first == replay and len(stack.captured) == 1
        assert (await rows(stack, UserMemory))[0].confirmed_count == 1
        assert len(await rows(stack, UserMemoryHistory)) == len(await rows(stack, MemoryCommandReceipt)) == 1
        with pytest.raises(AppError) as exc:
            await stack.submit(f"拒绝 {stack.proposal_id}", message_id="stable-confirm")
        assert exc.value.code == ErrorCode.IDEMPOTENCY_MISMATCH


async def test_local_b15_result_event_failure_rolls_back_fact_history_receipt_and_resume(
    async_tool_database, monkeypatch
):
    async with confirmation_stack(async_tool_database, monkeypatch) as stack:
        async with stack.factory.begin() as session:
            await session.execute(
                sa.text(
                    "CREATE FUNCTION runtime.fail_confirmation_result() RETURNS trigger "
                    "LANGUAGE plpgsql AS $$ "
                    "BEGIN IF NEW.event_type='MEMORY_CONFIRMATION_RESOLVED' THEN RAISE EXCEPTION 'fault'; "
                    "END IF; RETURN NEW; END $$"
                )
            )
            await session.execute(
                sa.text(
                    "CREATE TRIGGER fail_confirmation_result BEFORE INSERT ON runtime.canonical_event "
                    "FOR EACH ROW EXECUTE FUNCTION runtime.fail_confirmation_result()"
                )
            )
        before = len(await rows(stack, CanonicalEvent)), len(await rows(stack, RunSubmission))
        with pytest.raises(AppError) as exc:
            await stack.submit(f"确认 {stack.proposal_id}")
        assert exc.value.code == ErrorCode.COMMON_INTERNAL_ERROR
        assert (
            await rows(stack, UserMemory)
            == await rows(stack, UserMemoryHistory)
            == await rows(stack, MemoryCommandReceipt)
            == []
        )
        assert before == (len(await rows(stack, CanonicalEvent)), len(await rows(stack, RunSubmission)))
        assert (await rows(stack, RunRecord))[0].status == "WAITING_INPUT"
        assert (await rows(stack, RunInterrupt))[0].status == "WAITING" and stack.captured == []


async def test_local_b15_frozen_write_disabled_prevents_confirmation(async_tool_database, monkeypatch):
    async with confirmation_stack(
        async_tool_database, monkeypatch, policy=MemoryPolicySettings(write_enabled=False)
    ) as stack:
        with pytest.raises(AppError) as exc:
            await stack.submit(f"确认 {stack.proposal_id}")
        assert exc.value.code == ErrorCode.FORBIDDEN
        assert (await rows(stack, RunInterrupt))[0].status == "WAITING"
        assert await rows(stack, UserMemory) == []


async def test_local_b15_concurrent_same_message_replays_one_durable_confirmation(
    async_tool_database, monkeypatch
):
    async with confirmation_stack(async_tool_database, monkeypatch) as stack:
        replies = await asyncio.gather(
            stack.submit(f"确认 {stack.proposal_id}", message_id="concurrent-confirm"),
            stack.submit(f"确认 {stack.proposal_id}", message_id="concurrent-confirm"),
        )
        assert replies[0] == replies[1]
        assert len(stack.captured) == 1
        assert (await rows(stack, UserMemory))[0].confirmed_count == 1
        assert len(await rows(stack, MemoryCommandReceipt)) == 1


async def test_local_b15_scope_lock_wait_rechecks_absolute_deadline_and_rolls_back(
    async_tool_database, monkeypatch
):
    async with confirmation_stack(async_tool_database, monkeypatch) as stack:
        async with stack.factory.begin() as session:
            session.add(MemoryScope(tenant_id=stack.run.tenant_id, user_id=stack.run.user_id))
            await session.execute(
                sa.update(RunRecord)
                .where(RunRecord.id == stack.run.id)
                .values(deadline_at=sa.func.statement_timestamp() + sa.text("interval '1 second'"))
            )
        async with stack.factory.begin() as blocker:
            await blocker.scalar(
                sa.select(MemoryScope).where(MemoryScope.tenant_id == stack.run.tenant_id).with_for_update()
            )
            pending = asyncio.create_task(stack.submit(f"确认 {stack.proposal_id}"))
            try:
                for _ in range(50):
                    async with async_tool_database.connect() as probe:
                        waiting = await probe.scalar(
                            sa.text(
                                "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                                "WHERE datname=current_database() "
                                "AND wait_event_type='Lock' AND query LIKE '%memory_scope%')"
                            )
                        )
                    if waiting:
                        break
                    await asyncio.sleep(0.01)
                assert waiting, "resume must reach the real scope row lock"
                await asyncio.sleep(1.05)
            finally:
                await blocker.rollback()
            with pytest.raises(AppError) as exc:
                await pending
        assert exc.value.code == ErrorCode.MEMORY_CONFIRMATION_EXPIRED
        assert (await rows(stack, RunRecord))[0].status == "WAITING_INPUT"
        assert (await rows(stack, RunInterrupt))[0].status == "WAITING"
        assert await rows(stack, UserMemory) == await rows(stack, MemoryCommandReceipt) == []
        assert stack.captured == []


async def test_local_b15_quota_failure_keeps_candidate_and_all_inbound_effects_uncommitted(
    async_tool_database, monkeypatch
):
    async with confirmation_stack(
        async_tool_database, monkeypatch, policy=MemoryPolicySettings(max_entries_per_user=1)
    ) as stack:
        ctx = context(tenant=stack.run.tenant_id, user=stack.run.user_id)
        await writer(async_tool_database).execute(ctx, command(upsert("protected", "existing")))
        before = len(await rows(stack, CanonicalEvent)), len(await rows(stack, RunSubmission))
        with pytest.raises(AppError) as exc:
            await stack.submit(f"确认 {stack.proposal_id}")
        assert exc.value.code == ErrorCode.MEMORY_QUOTA_EXCEEDED
        assert before == (len(await rows(stack, CanonicalEvent)), len(await rows(stack, RunSubmission)))
        assert (await rows(stack, RunInterrupt))[0].status == "WAITING"
        assert (await rows(stack, RunRecord))[0].status == "WAITING_INPUT"
        assert [row.memory_key for row in await rows(stack, UserMemory)] == ["protected"]
        assert len(await rows(stack, MemoryCommandReceipt)) == len(await rows(stack, UserMemoryHistory)) == 1
        assert stack.captured == []


async def test_local_b15_confirmation_reports_and_uses_frozen_ttl(async_tool_database, monkeypatch):
    async with confirmation_stack(
        async_tool_database, monkeypatch, policy=MemoryPolicySettings(ttl_days_user=2)
    ) as stack:
        before = datetime.now(UTC)
        await stack.submit(f"确认 {stack.proposal_id}")
        memory = (await rows(stack, UserMemory))[0]
        assert before + timedelta(days=2) <= memory.expires_at <= datetime.now(UTC) + timedelta(days=2)
        resolved = next(
            row
            for row in await rows(stack, CanonicalEvent)
            if row.event_type == "MEMORY_CONFIRMATION_RESOLVED"
        )
        assert datetime.fromisoformat(resolved.payload_json["expires_at"]) == memory.expires_at


async def test_local_b15_explicit_resume_keeps_real_input_id_distinct_from_transport_key(
    async_tool_database, monkeypatch
):
    async with confirmation_stack(async_tool_database, monkeypatch) as stack:
        response = await stack.explicit(
            f"确认 {stack.proposal_id}", message_id="real-input-id", key="transport-key"
        )
        assert response.status_code == 200
        memory = (await rows(stack, UserMemory))[0]
        user = next(
            row for row in await rows(stack, CanonicalEvent) if row.id == memory.confirmation_event_id
        )
        assert user.payload_json["message_id"] == "real-input-id"
        submission = next(row for row in await rows(stack, RunSubmission) if row.id == user.submission_id)
        assert submission.idempotency_key == "transport-key"


@pytest.mark.parametrize("terminal", ["COMPLETED", "FAILED", "CANCELLED"])
async def test_local_b15_terminal_run_takes_priority_over_confirmation(
    async_tool_database, monkeypatch, terminal
):
    async with confirmation_stack(async_tool_database, monkeypatch) as stack:
        async with stack.factory.begin() as session:
            await session.execute(
                sa.update(RunRecord).where(RunRecord.id == stack.run.id).values(status=terminal)
            )
        before = len(await rows(stack, CanonicalEvent)), len(await rows(stack, RunSubmission))
        response = await stack.explicit(f"确认 {stack.proposal_id}")
        assert response.status_code == 200 and terminal in response.text
        assert (await rows(stack, RunRecord))[0].status == terminal
        assert (await rows(stack, RunInterrupt))[0].status == "WAITING"
        assert before == (len(await rows(stack, CanonicalEvent)), len(await rows(stack, RunSubmission)))
        assert await rows(stack, UserMemory) == [] and stack.captured == []
