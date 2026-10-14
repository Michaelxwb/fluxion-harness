"""B-10/11/16/17: durable ownership and atomic effects, through real PG and HTTP."""

import asyncio
import json
import os
import sys
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from importlib import import_module

import pytest
import sqlalchemy as sa
from muad_agent_runtime.application.memory_service import MemoryService
from muad_agent_runtime.infrastructure.models.runtime import MemoryConsolidationJob, UserMemory

from tests.agent_runtime.memory_consolidation_effect_support import (
    admin_context,
    counts,
    current_revision,
    execution,
    output,
    pending,
    seed_admin,
    seed_author,
)
from tests.agent_runtime.memory_consolidation_support import model_stack
from tests.agent_runtime.memory_redesign_write_support import command, commands

pytestmark = pytest.mark.integration


async def test_b11_memory_redesign_credential_http_timeout_is_retryable_without_run_failure(
    async_tool_database,
):
    import httpx

    jobs = import_module("muad_agent_runtime.application.memory_jobs")
    async with model_stack(async_tool_database, credential_delay=0.2, output=output("color")) as stack:
        factory, run, job, _, client, requests, *_ = stack
        client._client.timeout = httpx.Timeout(0.02)
        await pending(factory, job)
        result = await execution(factory, client).execute(await jobs.MemoryJobs(factory).claim("runtime"))
        assert result is None and requests == []
        async with factory() as session:
            row = await session.get(MemoryConsolidationJob, job.id)
            assert row.status == "RETRY" and row.error_code == "FAILED" and row.input_json is None
        assert await counts(factory, run) == {
            "memory": 0,
            "history": 0,
            "completed": 0,
            "run_status": "COMPLETED",
        }


async def test_b11_memory_redesign_attempt_limit_records_one_failure_event(async_tool_database):
    jobs = import_module("muad_agent_runtime.application.memory_jobs")
    async with model_stack(async_tool_database, output=output("a", "b")) as stack:
        factory, run, job, _, client, requests, *_ = stack
        await pending(factory, job)
        await install_rejection(factory, "history")
        worker, owners = execution(factory, client), jobs.MemoryJobs(factory)
        assert await worker.execute(await owners.claim("first")) is None
        async with factory.begin() as session:
            row = await session.get(MemoryConsolidationJob, job.id)
            before_hash = row.input_hash
            row.next_attempt_at = datetime.now(UTC)
        result = await worker.execute(await owners.claim("second"))
        assert result["reason"] == "FAILED" and result["attempt"] == 2 and result["written"] == 0
        assert await owners.claim("third") is None
        assert len(requests) == 2
        assert await counts(factory, run) == {
            "memory": 0,
            "history": 0,
            "completed": 1,
            "run_status": "COMPLETED",
        }
        async with factory() as session:
            row = await session.get(MemoryConsolidationJob, job.id)
            assert row.status == "FAILED" and row.input_hash == before_hash and row.lease_owner is None


async def test_b17_memory_redesign_late_old_source_cannot_replace_newer_source(async_tool_database):
    from dataclasses import replace

    from muad_agent_runtime.infrastructure.models.runtime import CanonicalEvent
    from muad_contracts.memory import MemorySourceEvidence

    from tests.agent_runtime.memory_redesign_write_support import upsert
    from tests.agent_runtime.memory_resume_support import inbound

    jobs = import_module("muad_agent_runtime.application.memory_jobs")
    async with model_stack(async_tool_database, output=output("color")) as stack:
        factory, run, job, _, client, requests, *_ = stack
        await seed_author(factory, run, "AGENT")
        _, seq = await inbound(factory, run, "newer green basis")
        async with factory() as session:
            event = await session.scalar(
                sa.select(CanonicalEvent).where(
                    CanonicalEvent.run_id == run.id,
                    CanonicalEvent.seq == seq,
                    CanonicalEvent.event_type == "USER_MESSAGE",
                )
            )
        ctx = replace(
            admin_context(run),
            actor_type="SYSTEM",
            source_run_id=run.id,
            conversation_id=run.conversation_id,
            evidence=MemorySourceEvidence(
                source_event_id=event.id,
                source_seq=seq,
                source_scope_revision=event.payload_json["source_scope_revision"],
            ),
        )
        await MemoryService(factory).execute(
            ctx, command(upsert(key="infer.color", value="green", expected=1))
        )
        await pending(factory, job, revision=await current_revision(factory, run))
        result = await execution(factory, client).execute(
            await jobs.MemoryJobs(factory).claim("late-old-run")
        )
        assert result["written"] == 0 and result["skipped"] == 1 and len(requests) == 1
        async with factory() as session:
            row = await session.scalar(sa.select(UserMemory))
            assert row.content_json == {"value": "green"} and row.version == 2
            assert row.confirmed_count == 0 and row.last_source_seq == seq


async def test_b10_memory_redesign_lease_expiring_during_effect_flush_rolls_everything_back(
    async_tool_database,
):
    jobs = import_module("muad_agent_runtime.application.memory_jobs")
    async with model_stack(async_tool_database, output=output("color")) as stack:
        factory, run, job, _, client, requests, *_ = stack
        await pending(factory, job)
        claim = await jobs.MemoryJobs(factory).claim("expiring-owner")
        async with factory.begin() as session:
            await session.execute(
                sa.text(
                    "CREATE FUNCTION runtime.slow_effect() RETURNS trigger LANGUAGE plpgsql AS $$ "
                    "BEGIN PERFORM pg_sleep(1); RETURN NEW; END $$"
                )
            )
            await session.execute(
                sa.text(
                    "CREATE TRIGGER slow_effect BEFORE INSERT ON runtime.user_memory "
                    "FOR EACH ROW EXECUTE FUNCTION runtime.slow_effect()"
                )
            )
            row = await session.get(MemoryConsolidationJob, job.id)
            row.lease_until = datetime.now(UTC) + timedelta(seconds=0.5)
        assert await execution(factory, client).execute(claim) is None
        assert len(requests) == 1
        assert await counts(factory, run) == {
            "memory": 0,
            "history": 0,
            "completed": 0,
            "run_status": "COMPLETED",
        }
        async with factory() as session:
            row = await session.get(MemoryConsolidationJob, job.id)
            assert row.status == "RUNNING" and row.claim_epoch == claim.epoch


@pytest.mark.parametrize("phase", ["before_commit", "after_commit"])
async def test_b11_memory_redesign_sigkill_before_and_after_commit_has_one_effect_set(
    async_tool_database, phase
):
    jobs = import_module("muad_agent_runtime.application.memory_jobs")
    async with model_stack(async_tool_database, output=output("color")) as stack:
        factory, run, job, snapshot, client, requests, *_ = stack
        await pending(factory, job)
        claim = await jobs.MemoryJobs(factory).claim("crash-owner")
        worker = execution(factory, client)
        async with worker.invoker.open(job, snapshot) as invocation:
            payload = await worker.jobs.freeze_input(claim, credential_canaries=invocation.canaries)
            batch = await invocation.generate(payload)
        child = await crash_child(async_tool_database, claim, batch, phase)
        try:
            async with asyncio.timeout(15):
                assert await child.stdout.readline() == b"READY\n", (await child.stderr.read()).decode()
            count = int(phase == "after_commit")
            assert await counts(factory, run) == {
                "memory": count,
                "history": count,
                "completed": count,
                "run_status": "COMPLETED",
            }
        finally:
            if child.returncode is None:
                child.kill()
            await child.wait()
        result = await worker.complete(claim, batch, credential_canaries=())
        assert result["written"] == 1 and len(requests) == 1
        assert await counts(factory, run) == {
            "memory": 1,
            "history": 1,
            "completed": 1,
            "run_status": "COMPLETED",
        }


async def crash_child(engine, claim, batch, phase):
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tests.agent_runtime.memory_consolidation_crash_worker",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=os.environ | {"MUAD_CRASH_DATABASE_URL": engine.url.render_as_string(hide_password=False)},
    )
    data = {"claim": asdict(claim), "batch": batch.model_dump(mode="json"), "phase": phase}
    child.stdin.write((json.dumps(data, default=str) + "\n").encode())
    await child.stdin.drain()
    child.stdin.close()
    return child


async def test_b10_memory_redesign_two_executors_takeover_fences_atomic_effects(async_tool_database):
    jobs = import_module("muad_agent_runtime.application.memory_jobs")
    async with model_stack(async_tool_database, output=output("color")) as stack:
        factory, run, job, _, client, requests, *_ = stack
        await pending(factory, job)
        owners, worker = jobs.MemoryJobs(factory), execution(factory, client)
        claims = await asyncio.gather(owners.claim("runtime-A"), owners.claim("runtime-B"))
        old = next(item for item in claims if item is not None)
        assert sum(item is not None for item in claims) == 1
        async with factory.begin() as session:
            row = await session.get(MemoryConsolidationJob, job.id)
            row.lease_until = datetime.now(UTC) - timedelta(seconds=1)
        new = await owners.claim("runtime-B")
        assert new.epoch > old.epoch
        assert await worker.execute(old) is None
        result = await worker.execute(new)
        assert result["written"] == 1
        assert await worker.execute(new) == result
        assert len(requests) == 1
        assert await counts(factory, run) == {
            "memory": 1,
            "history": 1,
            "completed": 1,
            "run_status": "COMPLETED",
        }


@pytest.mark.parametrize(
    "fault", ["invalid_output", "later_item", "history", "receipt", "event", "job_terminal"]
)
async def test_b11_memory_redesign_whole_batch_failure_keeps_completed_run(async_tool_database, fault):
    jobs = import_module("muad_agent_runtime.application.memory_jobs")
    response = (
        '{"candidates":[{},{}],"task_completed":false}' if fault == "invalid_output" else output("a", "b")
    )
    async with model_stack(async_tool_database, output=response) as stack:
        factory, run, job, _, client, requests, *_ = stack
        await pending(factory, job)
        if fault != "invalid_output":
            await install_rejection(factory, fault)
        claim = await jobs.MemoryJobs(factory).claim("runtime")
        await execution(factory, client).execute(claim)
        assert len(requests) == 1
        assert await counts(factory, run) == {
            "memory": 0,
            "history": 0,
            "completed": int(fault == "invalid_output"),
            "run_status": "COMPLETED",
        }
        async with factory() as session:
            row = await session.get(MemoryConsolidationJob, job.id)
            assert row.status == ("SKIPPED" if fault == "invalid_output" else "RETRY")
            assert row.error_code == ("REJECTED" if fault == "invalid_output" else "FAILED")


async def install_rejection(factory, fault):
    table, condition = {
        "later_item": ("user_memory", "NEW.memory_key = 'infer.b'"),
        "history": ("user_memory_history", "true"),
        "receipt": ("memory_command_receipt", "NEW.endpoint = 'memory.consolidation'"),
        "event": ("canonical_event", "NEW.event_type = 'MEMORY_CONSOLIDATED'"),
        "job_terminal": ("memory_consolidation_job", "NEW.status = 'DONE'"),
    }[fault]
    action = "UPDATE" if fault == "job_terminal" else "INSERT"
    async with factory.begin() as session:
        await session.execute(
            sa.text(
                "CREATE FUNCTION runtime.reject_effect() RETURNS trigger LANGUAGE plpgsql AS $$ "
                f"BEGIN IF {condition} THEN RAISE EXCEPTION 'effect fault'; END IF; RETURN NEW; END $$"
            )
        )
        await session.execute(
            sa.text(
                f"CREATE TRIGGER reject_effect BEFORE {action} ON runtime.{table} "
                "FOR EACH ROW EXECUTE FUNCTION runtime.reject_effect()"
            )
        )


async def test_b16_memory_redesign_forget_before_old_job_has_no_model_or_revival(async_tool_database):
    jobs = import_module("muad_agent_runtime.application.memory_jobs")
    async with model_stack(async_tool_database, output=output("color")) as stack:
        factory, run, job, _, client, requests, *_ = stack
        await seed_admin(factory, run)
        await MemoryService(factory).execute(
            admin_context(run),
            command(commands().MemoryMutation(action="DELETE", memory_key="infer.color", expected_version=1)),
        )
        await pending(factory, job)
        result = await execution(factory, client).execute(await jobs.MemoryJobs(factory).claim("runtime"))
        assert result["reason"] == "STALE" and requests == []
        async with factory() as session:
            row = await session.scalar(sa.select(UserMemory))
            assert row.is_deleted and row.retirement_reason == "ADMIN_DELETE" and row.version == 2


async def test_b16_memory_redesign_delete_during_http_model_makes_whole_job_stale(async_tool_database):
    jobs = import_module("muad_agent_runtime.application.memory_jobs")
    async with model_stack(async_tool_database, delay=0.3, output=output("color", "another")) as stack:
        factory, run, job, _, client, requests, *_ = stack
        await seed_admin(factory, run)
        await pending(factory, job, revision=await current_revision(factory, run))
        claim = await jobs.MemoryJobs(factory).claim("runtime")
        task = asyncio.create_task(execution(factory, client).execute(claim))
        try:
            async with asyncio.timeout(5):
                while not requests:
                    await asyncio.sleep(0.01)
            await MemoryService(factory).execute(
                admin_context(run),
                command(
                    commands().MemoryMutation(action="DELETE", memory_key="infer.color", expected_version=1)
                ),
            )
            assert (await task)["reason"] == "STALE"
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        assert await counts(factory, run) == {
            "memory": 1,
            "history": 2,
            "completed": 1,
            "run_status": "COMPLETED",
        }


@pytest.mark.parametrize("author,disabled", [("ADMIN", False), ("USER", False), ("AGENT", True)])
async def test_b17_memory_redesign_confirmed_and_disabled_are_never_downgraded(
    async_tool_database, author, disabled
):
    jobs = import_module("muad_agent_runtime.application.memory_jobs")
    async with model_stack(async_tool_database, output=output("color")) as stack:
        factory, run, job, _, client, requests, *_ = stack
        await seed_author(factory, run, author)
        if disabled:
            await MemoryService(factory).execute(
                admin_context(run),
                command(
                    commands().MemoryMutation(action="DISABLE", memory_key="infer.color", expected_version=1)
                ),
            )
        version = 2 if disabled else 1
        await pending(factory, job, revision=await current_revision(factory, run))
        result = await execution(factory, client).execute(await jobs.MemoryJobs(factory).claim("runtime"))
        assert len(requests) == 1 and result["written"] == 0 and result["skipped"] == 1
        async with factory() as session:
            row = await session.scalar(sa.select(UserMemory))
            assert row.author == author and row.version == version and row.enabled is not disabled
            assert row.confirmed_count == int(author == "USER")


@pytest.mark.parametrize(
    "reason,fresh",
    [("QUOTA_EVICT", False), ("QUOTA_EVICT", True), ("ADMIN_DELETE", True), ("USER_DELETE", True)],
)
async def test_b16_memory_redesign_retirement_requires_new_real_user_basis(
    async_tool_database, reason, fresh
):
    from dataclasses import replace

    from muad_contracts.memory import MemoryRetirementReason

    jobs = import_module("muad_agent_runtime.application.memory_jobs")
    async with model_stack(async_tool_database, output=output("color")) as stack:
        factory, run, job, _, client, requests, *_ = stack
        await seed_author(factory, run, "AGENT")
        automatic = reason == "QUOTA_EVICT"
        ctx = replace(
            admin_context(run),
            actor_type="SYSTEM" if automatic else "USER" if reason == "USER_DELETE" else "ADMIN",
        )
        await MemoryService(factory).execute(
            ctx,
            command(
                commands().MemoryMutation(
                    action="RETIRE" if automatic else "DELETE",
                    memory_key="infer.color",
                    expected_version=1,
                    **({"retirement_reason": MemoryRetirementReason.QUOTA_EVICT} if automatic else {}),
                )
            ),
        )
        revision = await current_revision(factory, run)
        if fresh:
            from tests.agent_runtime.memory_resume_support import inbound

            _, seq = await inbound(factory, run, "new blue basis")
            async with factory.begin() as session:
                (await session.get(MemoryConsolidationJob, job.id)).through_seq = seq
        await pending(factory, job, revision=revision)
        result = await execution(factory, client).execute(await jobs.MemoryJobs(factory).claim("runtime"))
        restored = automatic and fresh
        assert len(requests) == 1 and result["written"] == int(restored)
        async with factory() as session:
            row = await session.scalar(sa.select(UserMemory))
            assert row.is_deleted is not restored and row.author == "AGENT"
            assert row.version == (3 if restored else 2) and row.confirmed_count == 0
