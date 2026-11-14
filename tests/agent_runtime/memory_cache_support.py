"""Shared cache/query fixtures for memory tests."""

from contextlib import contextmanager
from importlib import import_module
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker


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
