"""B-04/B-18: real concurrent PG capacity and failed batch rollback."""

import asyncio

import pytest
import sqlalchemy as sa
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts.platform_settings import MemoryPolicySettings
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.agent_runtime.memory_redesign_write_support import (
    command,
    commands,
    context,
    counters,
    install_history_failure,
    seed_facts,
    upsert,
    writer,
)

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("dimension", ["count", "bytes"])
async def test_b04_memory_redesign_independent_writers_cannot_take_the_last_count_or_byte_slot(
    async_tool_database, dimension
):
    settings = (
        MemoryPolicySettings()
        if dimension == "count"
        else MemoryPolicySettings(max_memory_bytes_per_user=1024)
    )
    ctx, service = context(settings=settings), writer(async_tool_database)
    if dimension == "count":
        await seed_facts(async_tool_database, ctx, 199)
    value = "blue" if dimension == "count" else "中" * 170
    factory = async_sessionmaker(async_tool_database, expire_on_commit=False)
    connections, barrier = set(), asyncio.Barrier(2)

    async def write(mutation):
        async with factory.begin() as session:
            connections.add((await session.execute(sa.select(sa.func.pg_backend_pid()))).scalar_one())
            await asyncio.wait_for(barrier.wait(), timeout=10)
            return await service.execute_in_session(session, ctx, command(mutation))

    results = await asyncio.gather(
        write(upsert("new.a", value)),
        write(upsert("new.b", value)),
        return_exceptions=True,
    )
    assert len(connections) == 2
    assert sum(not isinstance(item, BaseException) for item in results) == 1
    failure = next(item for item in results if isinstance(item, AppError))
    assert failure.code == ErrorCode.MEMORY_QUOTA_EXCEEDED
    counts = await counters(async_tool_database, ctx)
    assert counts["user_memory"] == (200 if dimension == "count" else 1)
    assert counts["user_memory_history"] == counts["memory_command_receipt"] == 1
    if dimension == "bytes":
        from muad_contracts.memory import memory_content_bytes

        async with async_tool_database.connect() as connection:
            rows = (
                await connection.execute(
                    sa.text(
                        "SELECT content_json,source_quote FROM runtime.user_memory"
                        " WHERE tenant_id=:tenant AND is_deleted=false"
                    ),
                    {"tenant": ctx.tenant_id},
                )
            ).all()
        assert sum(memory_content_bytes(row.content_json, row.source_quote) for row in rows) <= 1024


@pytest.mark.parametrize("author", ["USER", "ADMIN"])
async def test_b18_memory_redesign_full_confirmed_capacity_refuses_without_eviction(
    async_tool_database, author
):
    ctx = context(settings=MemoryPolicySettings(max_entries_per_user=1))
    await seed_facts(async_tool_database, ctx, 1, author=author)
    with pytest.raises(AppError) as exc:
        await writer(async_tool_database).execute(ctx, command(upsert("new")))
    assert exc.value.code == ErrorCode.MEMORY_QUOTA_EXCEEDED
    assert (await counters(async_tool_database, ctx))["user_memory_history"] == 0
    async with async_tool_database.connect() as connection:
        assert (
            await connection.execute(
                sa.text("SELECT is_deleted FROM runtime.user_memory WHERE tenant_id=:tenant"),
                {"tenant": ctx.tenant_id},
            )
        ).scalar_one() is False


async def test_b18_memory_redesign_eviction_history_failure_preserves_all_facts(async_tool_database):
    ctx = context(settings=MemoryPolicySettings(max_entries_per_user=1))
    await seed_facts(async_tool_database, ctx, 1, author="AGENT")
    await install_history_failure(async_tool_database, reason="QUOTA_EVICT")
    with pytest.raises(AppError) as exc:
        await writer(async_tool_database).execute(ctx, command(upsert("new")))
    assert exc.value.code == ErrorCode.COMMON_INTERNAL_ERROR
    assert await counters(async_tool_database, ctx) == {
        "user_memory": 1,
        "user_memory_history": 0,
        "memory_command_receipt": 0,
        "memory_scope": 0,
    }


async def test_b18_memory_redesign_successful_batch_retires_only_agents_atomically(async_tool_database):
    ctx = context(settings=MemoryPolicySettings(max_entries_per_user=2))
    await seed_facts(async_tool_database, ctx, 1, author="USER", prefix="confirmed")
    await seed_facts(async_tool_database, ctx, 1, author="AGENT")
    outcome = await writer(async_tool_database).execute(ctx, command(upsert("new")))
    assert outcome.response.scope_revision == 1 and outcome.response.changed_count == 2
    assert outcome.response.forget_epoch == 0
    async with async_tool_database.connect() as connection:
        rows = (
            await connection.execute(
                sa.text(
                    "SELECT memory_key,is_deleted,author FROM runtime.user_memory"
                    " WHERE tenant_id=:tenant ORDER BY memory_key"
                ),
                {"tenant": ctx.tenant_id},
            )
        ).all()
        assert [(row.memory_key, row.is_deleted) for row in rows] == [
            ("confirmed.1", False),
            ("new", False),
            ("seed.1", True),
        ]
        histories = (
            await connection.execute(
                sa.text(
                    "SELECT change_reason,after_json FROM runtime.user_memory_history WHERE tenant_id=:tenant"
                ),
                {"tenant": ctx.tenant_id},
            )
        ).all()
        assert {row.change_reason for row in histories} == {"CREATE", "QUOTA_EVICT"}
        assert all(row.after_json["author"] != "USER" for row in histories)


async def test_b18_memory_redesign_failure_after_eviction_and_first_candidate_restores_entire_batch(
    async_tool_database,
):
    ctx = context(settings=MemoryPolicySettings(max_entries_per_user=2))
    await seed_facts(async_tool_database, ctx, 2, author="AGENT")
    await install_history_failure(async_tool_database, key="new.b")
    with pytest.raises(AppError) as exc:
        await writer(async_tool_database).execute(ctx, command(upsert("new.a"), upsert("new.b")))
    assert exc.value.code == ErrorCode.COMMON_INTERNAL_ERROR
    counts = await counters(async_tool_database, ctx)
    assert counts == {
        "user_memory": 2,
        "user_memory_history": 0,
        "memory_command_receipt": 0,
        "memory_scope": 0,
    }
    async with async_tool_database.connect() as connection:
        assert (
            await connection.execute(
                sa.text(
                    "SELECT count(*) FROM runtime.user_memory WHERE tenant_id=:tenant"
                    " AND is_deleted=false AND version=1"
                ),
                {"tenant": ctx.tenant_id},
            )
        ).scalar_one() == 2


async def test_b18_memory_redesign_expiry_cleanup_failure_rejects_new_write_and_preserves_existing(
    async_tool_database,
):
    ctx = context(settings=MemoryPolicySettings(max_entries_per_user=1))
    await seed_facts(async_tool_database, ctx, 1, author="AGENT", expired=True)
    await install_history_failure(async_tool_database, reason="EXPIRE")
    with pytest.raises(AppError) as exc:
        await writer(async_tool_database).execute(ctx, command(upsert("new")))
    assert exc.value.code == ErrorCode.COMMON_INTERNAL_ERROR
    assert (await counters(async_tool_database, ctx))["user_memory"] == 1
    assert (await counters(async_tool_database, ctx))["user_memory_history"] == 0


async def test_b18_memory_redesign_old_frozen_quota_permits_shrink_and_delete_but_refuses_growth(
    async_tool_database,
):
    ctx = context(settings=MemoryPolicySettings(max_entries_per_user=1))
    await seed_facts(async_tool_database, ctx, 2)
    service = writer(async_tool_database)
    shrunk = await service.execute(ctx, command(upsert("seed.1", value="b", expected=1)))
    assert shrunk.response.items[0].version == 2
    with pytest.raises(AppError) as exc:
        await service.execute(ctx, command(upsert("new")))
    assert exc.value.code == ErrorCode.MEMORY_QUOTA_EXCEEDED

    await service.execute(
        ctx, command(commands().MemoryMutation(action="DELETE", memory_key="seed.2", expected_version=1))
    )
    async with async_tool_database.connect() as connection:
        assert (
            await connection.execute(
                sa.text(
                    "SELECT count(*) FROM runtime.user_memory WHERE tenant_id=:tenant AND NOT is_deleted"
                ),
                {"tenant": ctx.tenant_id},
            )
        ).scalar_one() == 1
