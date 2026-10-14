"""Local real PG/HTTP background execution and immediate TTL filtering."""

import asyncio
from datetime import UTC, datetime, timedelta
from importlib import import_module

import pytest
import sqlalchemy as sa
from muad_agent_runtime.application.memory_queries import MemoryQueries, MemoryReadScope
from muad_agent_runtime.infrastructure.models.runtime import (
    MemoryConsolidationJob,
    RunRecord,
    UserMemory,
    UserMemoryHistory,
)

from tests.agent_runtime.memory_confirmation_resume_support import confirmation_stack, rows
from tests.agent_runtime.memory_consolidation_effect_support import counts, execution, output, pending
from tests.agent_runtime.memory_consolidation_support import TrackedProvider, model_stack
from tests.agent_runtime.memory_redesign_write_support import context, seed_facts

pytestmark = pytest.mark.integration


async def test_local_runtime_lifespan_pump_executes_real_job_and_closes_clients(
    async_tool_database, async_tool_datastore, monkeypatch, tmp_path
):
    from muad_agent_runtime import main
    from muad_agent_runtime.infrastructure import db

    async with model_stack(async_tool_database, output=output("color")) as stack:
        factory, run, job, _, client, requests, *_ = stack
        await pending(factory, job)
        settings = {
            "DATABASE_URL": async_tool_datastore["database_url"],
            "REDIS_URL": async_tool_datastore["redis_url"],
            "ARTIFACT_ROOT": str(tmp_path),
            "CONSOLE_PLATFORM_URL": str(client._client.base_url),
            "INTERNAL_SERVICE_TOKEN": "test-service-token",
        }
        for key, value in settings.items():
            monkeypatch.setenv(key, value)
        await db.dispose_engine()
        async with main.lifespan(main.app):
            supervisor = main.app.state.memory_supervisor
            owned_credentials = main.app.state.credentials_client
            async with asyncio.timeout(5):
                while True:
                    async with factory() as session:
                        current = await session.get(MemoryConsolidationJob, job.id)
                        if current.status == "DONE":
                            break
                    await asyncio.sleep(0.05)
            assert len(requests) == 1
            assert await counts(factory, run) == {
                "memory": 1,
                "history": 1,
                "completed": 1,
                "run_status": "COMPLETED",
            }
        assert supervisor.execution_count == 0 and await supervisor.run_once() == 0
        assert owned_credentials._client.is_closed


async def test_local_ttl_history_failure_rolls_back_and_is_visible(async_tool_database, caplog):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from tests.agent_runtime.memory_redesign_write_support import install_history_failure
    from tests.agent_runtime.test_memory_redesign_recall import metric

    module = import_module("muad_agent_runtime.application.memory_expiry")
    factory = async_sessionmaker(async_tool_database, expire_on_commit=False)
    ctx = context()
    await seed_facts(async_tool_database, ctx, 1, author="AGENT", expired=True)
    await install_history_failure(async_tool_database)
    before_error = metric("memory_expiry_total", outcome="ERROR")
    before_success = metric("memory_expiry_total", outcome="OK")
    assert await module.expire_memories(factory) == 0
    assert metric("memory_expiry_total", outcome="ERROR") == before_error + 1
    assert metric("memory_expiry_total", outcome="OK") == before_success
    assert any(record.message == "memory_expiry_failed" for record in caplog.records)
    async with factory() as session:
        row = await session.scalar(sa.select(UserMemory))
        assert not row.is_deleted and row.version == 1
        assert await session.scalar(sa.select(sa.func.count()).select_from(UserMemoryHistory)) == 0


async def test_local_background_shutdown_closes_provider_and_another_instance_takes_over(async_tool_database):
    module = import_module("muad_agent_runtime.application.memory_supervisor")
    async with model_stack(async_tool_database, delay=0.3, output=output("color")) as stack:
        factory, run, job, _, client, requests, *_ = stack
        await pending(factory, job)
        worker = execution(factory, client)
        first = module.MemorySupervisor(worker, instance_id="runtime-A", max_concurrency=2)
        second = module.MemorySupervisor(worker, instance_id="runtime-B", max_concurrency=1)
        try:
            assert await first.run_once() == 1
            async with asyncio.timeout(5):
                while not requests:
                    await asyncio.sleep(0.01)
            assert await first.run_once() == 0 and await second.run_once() == 0
            async with factory.begin() as session:
                row = await session.get(MemoryConsolidationJob, job.id)
                row.lease_until = datetime.now(UTC) - timedelta(seconds=1)
            assert await first.run_once() == 0 and first.execution_count == 1
            await first.close()
            assert first.execution_count == 0 and all(
                provider.closed for provider in TrackedProvider.instances
            )
            async with factory.begin() as session:
                row = await session.get(MemoryConsolidationJob, job.id)
                before_hash, before_epoch = row.input_hash, row.claim_epoch
                row.lease_until = datetime.now(UTC) - timedelta(seconds=1)
            assert await first.run_once() == 0 and await second.run_once() == 1
            async with asyncio.timeout(5):
                while second.execution_count:
                    await asyncio.sleep(0.01)
            async with factory() as session:
                row = await session.get(MemoryConsolidationJob, job.id)
                assert (
                    row.status == "DONE" and row.claim_epoch > before_epoch and row.input_hash == before_hash
                )
            assert await counts(factory, run) == {
                "memory": 1,
                "history": 1,
                "completed": 1,
                "run_status": "COMPLETED",
            }
        finally:
            await first.close()
            await second.close()


async def test_local_ttl_read_is_immediate_and_two_sweepers_write_one_expire_history(async_tool_database):
    module = import_module("muad_agent_runtime.application.memory_expiry")
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(async_tool_database, expire_on_commit=False)
    ctx = context()
    await seed_facts(async_tool_database, ctx, 1, author="AGENT", expired=True)
    queries = MemoryQueries(factory)
    assert (
        await queries.read_snapshot(MemoryReadScope(tenant_id=ctx.tenant_id, user_id=ctx.user_id))
    ).records == ()
    results = await asyncio.gather(module.expire_memories(factory), module.expire_memories(factory))
    assert sum(results) == 1 and await module.expire_memories(factory) == 0
    async with factory() as session:
        row = await session.scalar(sa.select(UserMemory))
        history = list(await session.scalars(sa.select(UserMemoryHistory)))
        assert row.is_deleted and row.retirement_reason == "EXPIRE" and row.version == 2
        assert len(history) == 1 and history[0].change_reason == "EXPIRE"


async def test_local_expired_confirmation_sweep_never_saves_and_remains_resumable(
    async_tool_database, monkeypatch
):
    module = import_module("muad_agent_runtime.application.memory_confirmation_expiry")
    from muad_agent_runtime.infrastructure.models.runtime import RunInterrupt

    async with confirmation_stack(async_tool_database, monkeypatch) as stack:
        async with stack.factory.begin() as session:
            row = await session.get(RunInterrupt, stack.proposal_id)
            row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        assert await module.expire_confirmations(stack.factory) == 1
        assert await module.expire_confirmations(stack.factory) == 0
        interrupt = (await rows(stack, RunInterrupt))[0]
        assert interrupt.status == "EXPIRED" and interrupt.resolution_json["saved"] is False
        assert (await rows(stack, RunRecord))[0].status == "WAITING_INPUT"
        assert await rows(stack, UserMemory) == []
        events = await stack.submit("继续处理别的内容")
        assert any(event.type == "run.completed" for event in events)
        assert await rows(stack, UserMemory) == []
