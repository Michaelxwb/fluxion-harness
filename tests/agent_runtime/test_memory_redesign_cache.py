"""B-23: real PostgreSQL snapshots, transactional writers, TTL and read failures."""

import asyncio
import logging
from contextlib import contextmanager
from importlib import import_module
from uuid import uuid4

import pytest
import sqlalchemy as sa
from muad_agent_core.memory.rank import select_memories
from muad_api import render_metrics
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.agent_runtime.memory_redesign_write_support import (
    command,
    commands,
    context,
    seed_facts,
    source_context,
    upsert,
    writer,
)

pytestmark = pytest.mark.integration


def queries(engine):
    module = import_module("muad_agent_runtime.application.memory_queries")
    return module.MemoryQueries(async_sessionmaker(engine, expire_on_commit=False))


def cache(engine, ctx, *, repository=None, run_id=None):
    module = import_module("muad_agent_runtime.application.memory_cache")
    query_module = import_module("muad_agent_runtime.application.memory_queries")
    return module.MemoryCandidateCache(
        repository or queries(engine),
        scope=query_module.MemoryReadScope(tenant_id=ctx.tenant_id, user_id=ctx.user_id),
        run_id=run_id or uuid4(),
    )


async def load(reader, *, max_items=6, max_bytes=2048):
    return await reader.read_for_injection(max_items=max_items, max_bytes=max_bytes)


@contextmanager
def query_counts(engine):
    counts = {"full": 0, "head": 0}

    def before_execute(connection, cursor, statement, parameters, execution_context, executemany):
        if not statement.lstrip().upper().startswith("SELECT"):
            return
        if "runtime.user_memory" in statement:
            counts["full"] += 1
        elif "runtime.memory_scope" in statement:
            counts["head"] += 1

    sa.event.listen(engine.sync_engine, "before_cursor_execute", before_execute)
    try:
        yield counts
    finally:
        sa.event.remove(engine.sync_engine, "before_cursor_execute", before_execute)


async def test_b23_memory_redesign_cold_and_warm_reads_use_one_statement_each(async_tool_database):
    ctx = context()
    await writer(async_tool_database).execute(ctx, command(upsert()))
    reader = cache(async_tool_database, ctx)
    with query_counts(async_tool_database) as counts:
        cold = await load(reader)
        warm = await load(reader)
    assert counts == {"full": 1, "head": 1}
    assert cold.revision == warm.revision == 1
    assert [row.memory_key for row in warm.records] == ["color"]
    assert warm.as_of > cold.as_of


async def test_b23_memory_redesign_reload_uses_actual_snapshot_revision_after_interleaved_commit(
    async_tool_database,
    async_tool_datastore,
):
    ctx, service = context(), writer(async_tool_database)
    await service.execute(ctx, command(upsert(value="A")))
    base = type(queries(async_tool_database))

    class InterleavedQueries(base):
        """Keep both real reads; commit an independent writer between their boundaries."""

        async def read_scope_version(self, scope):
            head = await super().read_scope_version(scope)
            if head.revision == 2:
                await service.execute(ctx, command(upsert(value="C", expected=2)))
            return head

    engine = create_async_engine(async_tool_datastore["database_url"])
    try:
        repository = InterleavedQueries(async_sessionmaker(engine, expire_on_commit=False))
        reader = cache(engine, ctx, repository=repository)
        with query_counts(engine) as counts:
            await load(reader)
            await service.execute(ctx, command(upsert(value="B", expected=1)))
            second = await load(reader)
            third = await load(reader)
        assert second.revision == third.revision == 3
        assert second.records[0].content.value == third.records[0].content.value == "C"
        assert counts == {"full": 2, "head": 2}
        assert reader.cached_revision == 3
    finally:
        await engine.dispose()


async def test_b23_memory_redesign_cold_sql_keeps_revision_and_rows_in_one_mvcc_snapshot(
    async_tool_database,
    async_tool_datastore,
):
    ctx, service = context(), writer(async_tool_database)
    await service.execute(ctx, command(upsert(value="A")))
    async with async_tool_database.begin() as connection:
        await connection.execute(sa.text("ALTER TABLE runtime.user_memory RENAME TO memory_snapshot_storage"))
        await connection.execute(
            sa.text(
                "CREATE FUNCTION runtime.memory_snapshot_delay() RETURNS boolean"
                " LANGUAGE plpgsql AS $$ BEGIN IF"
                " current_setting('application_name')='memory_snapshot_reader'"
                " THEN PERFORM pg_sleep(0.5); END IF; RETURN true; END $$"
            )
        )
        await connection.execute(
            sa.text(
                "CREATE VIEW runtime.user_memory AS SELECT * FROM"
                " runtime.memory_snapshot_storage WHERE runtime.memory_snapshot_delay()"
            )
        )
    engine = create_async_engine(
        async_tool_datastore["database_url"],
        connect_args={"server_settings": {"application_name": "memory_snapshot_reader"}},
    )
    started = asyncio.Event()

    def before_read(connection, cursor, statement, parameters, execution_context, executemany):
        if "runtime.user_memory" in statement and statement.lstrip().startswith("SELECT"):
            started.set()

    sa.event.listen(engine.sync_engine, "before_cursor_execute", before_read)
    try:
        pending = asyncio.create_task(load(cache(engine, ctx)))
        await asyncio.wait_for(started.wait(), timeout=5)
        await asyncio.sleep(0.1)
        await service.execute(ctx, command(upsert(value="B", expected=1)))
        assert not pending.done()
        snapshot = await pending
        assert snapshot.revision == snapshot.records[0].version == 1
        assert snapshot.records[0].content.value == "A"
        assert (await queries(async_tool_database).read_snapshot(snapshot.scope)).revision == 2
    finally:
        await engine.dispose()


async def test_b23_memory_redesign_ttl_uses_current_statement_time_without_revision_change(
    async_tool_database,
):
    ctx = context()
    await writer(async_tool_database).execute(ctx, command(upsert()))
    async with async_tool_database.begin() as connection:
        await connection.execute(
            sa.text(
                "UPDATE runtime.user_memory SET"
                " expires_at=statement_timestamp()+interval '0.2 second' WHERE tenant_id=:tenant"
            ),
            {"tenant": ctx.tenant_id},
        )
    reader = cache(async_tool_database, ctx)
    with query_counts(async_tool_database) as counts:
        first = await load(reader)
        await asyncio.sleep(0.3)
        second = await load(reader)
    assert len(first.records) == 1 and second.records == ()
    assert first.revision == second.revision == 1 and second.as_of > first.as_of
    assert counts == {"full": 1, "head": 1}


@pytest.mark.parametrize("failure", ["head", "full"])
async def test_b23_memory_redesign_read_failure_clears_cache_and_emits_warning_and_metric(
    async_tool_database,
    failure,
    caplog,
):
    ctx, service = context(), writer(async_tool_database)
    await service.execute(ctx, command(upsert(value="CANDIDATE-MUST-NOT-BE-LOGGED")))
    reader = cache(async_tool_database, ctx)
    await load(reader)
    if failure == "full":
        await service.execute(ctx, command(upsert(value="changed", expected=1)))
    table, column = ("memory_scope", "revision") if failure == "head" else ("user_memory", "content_json")
    async with async_tool_database.begin() as connection:
        await connection.execute(
            sa.text(f"ALTER TABLE runtime.{table} RENAME COLUMN {column} TO unavailable")
        )
    before = render_metrics()
    with caplog.at_level(logging.WARNING):
        result = await load(reader)
    assert result is None and reader.cached_revision is None
    assert "memory_snapshot_read_failed" in caplog.text
    assert "CANDIDATE-MUST-NOT-BE-LOGGED" not in caplog.text
    assert render_metrics() != before and 'outcome="ERROR"' in render_metrics()
    async with async_tool_database.begin() as connection:
        await connection.execute(
            sa.text(f"ALTER TABLE runtime.{table} RENAME COLUMN unavailable TO {column}")
        )
    with query_counts(async_tool_database) as counts:
        recovered = await load(reader)
    assert recovered is not None and counts == {"full": 1, "head": 0}


@pytest.mark.parametrize("actor", ["ADMIN", "USER", "AGENT", "SYSTEM"])
async def test_b23_memory_redesign_all_writer_sources_invalidate_warm_rows(async_tool_database, actor):
    ctx = context()
    service = writer(async_tool_database)
    reader = cache(async_tool_database, ctx)
    assert (await load(reader)).revision == 0
    if actor in ("USER", "AGENT"):
        ctx = await source_context(async_tool_database, ctx, actor=actor)
    if actor == "SYSTEM":
        await seed_facts(async_tool_database, ctx, 1, author="AGENT", expired=True)
        from dataclasses import replace

        mutation = commands().MemoryMutation(
            action="RETIRE", memory_key="seed.1", expected_version=1, retirement_reason="EXPIRE"
        )
        await service.execute(replace(ctx, actor_type="SYSTEM"), command(mutation))
    else:
        await service.execute(ctx, command(upsert()))
    updated = await load(reader)
    assert updated.revision == 1
    assert len(updated.records) == (0 if actor == "SYSTEM" else 1)


@pytest.mark.parametrize("budget", [(0, 2048), (6, 0), (0, 0)])
async def test_b23_memory_redesign_zero_budget_makes_no_query_but_recall_stays_independent(
    async_tool_database,
    budget,
):
    ctx = context()
    await writer(async_tool_database).execute(ctx, command(upsert()))
    reader = cache(async_tool_database, ctx)
    with query_counts(async_tool_database) as counts:
        assert await load(reader, max_items=budget[0], max_bytes=budget[1]) is None
        assert counts == {"full": 0, "head": 0}
        recalled = await queries(async_tool_database).read_snapshot(reader.scope)
    assert len(recalled.records) == 1 and counts == {"full": 1, "head": 0}


async def test_b23_memory_redesign_cache_is_bound_to_run_tenant_and_user(async_tool_database):
    ctx = context()
    await writer(async_tool_database).execute(ctx, command(upsert()))
    other_user = context()
    other_tenant = context(tenant="other", user=ctx.user_id)
    readers = [cache(async_tool_database, scope) for scope in (ctx, ctx, other_user, other_tenant)]
    assert len({reader.run_id for reader in readers}) == 4
    with query_counts(async_tool_database) as counts:
        snapshots = [await load(reader) for reader in readers]
    assert [len(item.records) for item in snapshots] == [1, 1, 0, 0]
    assert counts == {"full": 4, "head": 0}
    async with async_tool_database.connect() as connection:
        assert (
            await connection.execute(sa.text("SELECT count(*) FROM runtime.memory_scope"))
        ).scalar_one() == 1


async def test_b23_memory_redesign_complete_rows_include_old_match_and_ignore_frozen_count(
    async_tool_database,
):
    ctx = context()
    await seed_facts(async_tool_database, ctx, 250)
    async with async_tool_database.begin() as connection:
        await connection.execute(
            sa.text(
                "UPDATE runtime.user_memory SET content_json='{" + '"value":"uniquematch"' + "}',"
                " update_time=now()-interval '30 days' WHERE tenant_id=:tenant AND memory_key='seed.1'"
            ),
            {"tenant": ctx.tenant_id},
        )
    snapshot = await load(cache(async_tool_database, ctx), max_items=1, max_bytes=1)
    assert len(snapshot.records) == 250
    matched = select_memories(snapshot.records, query="uniquematch", now=snapshot.as_of)
    assert [row.memory_key for row in matched.items] == ["seed.1"]


@pytest.mark.parametrize("dimension", ["count", "bytes"])
async def test_b23_memory_redesign_absolute_storage_bound_fails_closed(async_tool_database, dimension):
    ctx = context()
    await seed_facts(async_tool_database, ctx, 1001 if dimension == "count" else 700)
    if dimension == "bytes":
        async with async_tool_database.begin() as connection:
            await connection.execute(
                sa.text(
                    "UPDATE runtime.user_memory SET"
                    " content_json=jsonb_build_object('value',repeat('中',512)) WHERE tenant_id=:tenant"
                ),
                {"tenant": ctx.tenant_id},
            )
    reader = cache(async_tool_database, ctx)
    assert await load(reader) is None and reader.cached_revision is None


async def test_b23_memory_redesign_reader_releases_transaction_between_calls(async_tool_datastore):
    engine = create_async_engine(async_tool_datastore["database_url"], pool_size=1, max_overflow=0)
    try:
        reader = cache(engine, context())
        first = await load(reader)
        connection = await asyncio.wait_for(engine.connect(), timeout=1)
        try:
            await connection.execute(sa.text("SELECT pg_sleep(0.02)"))
        finally:
            await connection.close()
        second = await load(reader)
        assert second.as_of > first.as_of
    finally:
        await engine.dispose()
