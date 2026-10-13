"""B-03: five independent PG transactions, version chains, and rollback."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import sqlalchemy as sa
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.agent_runtime.memory_redesign_write_support import (
    command,
    commands,
    context,
    counters,
    install_history_failure,
    source_context,
    upsert,
    writer,
)

pytestmark = pytest.mark.integration


async def test_b03_memory_redesign_five_independent_connections_write_one_identity_with_continuous_history(
    async_tool_database,
):
    service, ctx = writer(async_tool_database), context()
    factory = async_sessionmaker(async_tool_database, expire_on_commit=False)
    connections = set()

    async def write(index):
        async with factory.begin() as session:
            connections.add((await session.execute(sa.select(sa.func.pg_backend_pid()))).scalar_one())
            await service.lock_scope(session, ctx)
            current = (
                await session.execute(
                    sa.text(
                        "SELECT version FROM runtime.user_memory WHERE tenant_id=:tenant AND user_id=:user"
                        " AND memory_key='color'"
                    ),
                    {"tenant": ctx.tenant_id, "user": ctx.user_id},
                )
            ).scalar_one_or_none()
            outcome = await service.execute_in_session(
                session, ctx, command(upsert(value=f"value {index}", expected=current))
            )
            return outcome.response.items[0]

    results = await asyncio.gather(*(write(i) for i in range(5)))
    assert len(connections) == 5
    assert len({item.id for item in results}) == 1
    assert sorted(item.version for item in results) == [1, 2, 3, 4, 5]
    async with async_tool_database.connect() as connection:
        rows = (
            await connection.execute(
                sa.text(
                    "SELECT version,after_json FROM runtime.user_memory_history"
                    " WHERE tenant_id=:tenant ORDER BY version"
                ),
                {"tenant": ctx.tenant_id},
            )
        ).all()
        assert [row.version for row in rows] == [1, 2, 3, 4, 5]
        assert all(row.after_json["version"] == row.version for row in rows)
        assert (
            await connection.execute(
                sa.text("SELECT revision FROM runtime.memory_scope WHERE tenant_id=:tenant"),
                {"tenant": ctx.tenant_id},
            )
        ).scalar_one() == 5


async def test_b03_memory_redesign_transaction_time_inversion_does_not_break_version_replay(
    async_tool_database,
):
    service, ctx = writer(async_tool_database), context()
    factory = async_sessionmaker(async_tool_database, expire_on_commit=False)
    async with factory.begin() as older:
        await older.execute(sa.select(sa.func.transaction_timestamp()))
        first = await service.execute(ctx, command(upsert(value="A")))
        await service.execute_in_session(older, ctx, command(upsert(value="B", expected=1)))
    async with async_tool_database.connect() as connection:
        history = (
            await connection.execute(
                sa.text(
                    "SELECT version,create_time,after_json FROM runtime.user_memory_history"
                    " WHERE tenant_id=:tenant ORDER BY version"
                ),
                {"tenant": ctx.tenant_id},
            )
        ).all()
        assert [row.version for row in history] == [1, 2]
        assert history[0].create_time > history[1].create_time
        assert [row.after_json["content_json"]["value"] for row in history] == ["A", "B"]
        assert all(row.after_json["id"] == str(first.response.items[0].id) for row in history)


async def test_b03_memory_redesign_history_insert_failure_rolls_back_fact_scope_and_receipt(
    async_tool_database,
):
    ctx, service = context(), writer(async_tool_database)
    await install_history_failure(async_tool_database)
    with pytest.raises(AppError) as exc:
        await service.execute(ctx, command(upsert()))
    assert exc.value.code == ErrorCode.COMMON_INTERNAL_ERROR
    assert all(value == 0 for value in (await counters(async_tool_database, ctx)).values())


async def test_b03_memory_redesign_replay_preserves_first_response_and_fingerprint_binds_scope_and_endpoint(
    async_tool_database,
):
    ctx, service = context(), writer(async_tool_database)
    original = command(upsert(), command_id="same-command")
    first, second = await asyncio.gather(service.execute(ctx, original), service.execute(ctx, original))
    assert first.response.model_dump(mode="json") == second.response.model_dump(mode="json")
    assert sorted([first.replayed, second.replayed]) == [False, True]
    assert (await counters(async_tool_database, ctx))["user_memory_history"] == 1
    for other_ctx, changed in [
        (ctx, command(upsert(value="other"), command_id="same-command")),
        (ctx, command(upsert(), command_id="same-command", endpoint="memory.other")),
        (context(user=uuid4()), original),
    ]:
        with pytest.raises(AppError) as exc:
            await service.execute(other_ctx, changed)
        assert exc.value.code == ErrorCode.IDEMPOTENCY_MISMATCH


async def test_b03_memory_redesign_delete_restore_and_clear_keep_identity_and_epoch_replay(
    async_tool_database,
):
    ctx, service = context(), writer(async_tool_database)
    first = (await service.execute(ctx, command(upsert()))).response.items[0]
    deleted = await service.execute(
        ctx, command(commands().MemoryMutation(action="DELETE", memory_key="color", expected_version=1))
    )
    assert deleted.response.forget_epoch == 1 and deleted.response.items[0].version == 2
    restored = await service.execute(ctx, command(upsert(value="new", expected=2, restore=True)))
    assert restored.response.items[0].id == first.id and restored.response.items[0].version == 3
    clear = command(commands().MemoryMutation(action="CLEAR"))
    cleared = await service.execute(ctx, clear)
    replay = await service.execute(ctx, clear)
    assert cleared.response.forget_epoch == replay.response.forget_epoch == 2
    assert cleared.response.items[0].version == 4 and replay.replayed is True


async def test_b03_memory_redesign_compare_and_swap_does_not_accept_stale_expected_version(
    async_tool_database,
):
    ctx, service = context(), writer(async_tool_database)
    await service.execute(ctx, command(upsert()))
    results = await asyncio.gather(
        *(service.execute(ctx, command(upsert(value=f"v{i}", expected=1))) for i in range(5)),
        return_exceptions=True,
    )
    assert sum(not isinstance(item, BaseException) for item in results) == 1
    failures = [item for item in results if isinstance(item, AppError)]
    assert len(failures) == 4 and all(item.code == ErrorCode.MEMORY_VERSION_CONFLICT for item in failures)
    assert (await counters(async_tool_database, ctx))["user_memory_history"] == 2


async def test_b03_memory_redesign_caller_rollback_keeps_batch_uncommitted(async_tool_database):
    ctx, service = context(), writer(async_tool_database)
    factory = async_sessionmaker(async_tool_database, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            outcome = await service.execute_in_session(session, ctx, command(upsert("a"), upsert("b")))
            assert outcome.response.scope_revision == 1 and outcome.response.changed_count == 2
            assert all(value == 0 for value in (await counters(async_tool_database, ctx)).values())
            await session.rollback()
    assert all(value == 0 for value in (await counters(async_tool_database, ctx)).values())


async def test_b03_memory_redesign_receipt_failure_rolls_back_entire_caller_transaction(async_tool_database):
    ctx, service = context(), writer(async_tool_database)
    async with async_tool_database.begin() as connection:
        await connection.execute(
            sa.text(
                "ALTER TABLE runtime.memory_command_receipt"
                " ADD CONSTRAINT memory_test_receipt_failure CHECK (status = 'NEVER')"
            )
        )
    factory = async_sessionmaker(async_tool_database, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            await session.execute(
                sa.text("INSERT INTO runtime.memory_scope (tenant_id,user_id) VALUES (:tenant,:user)"),
                {"tenant": ctx.tenant_id, "user": uuid4()},
            )
            with pytest.raises(AppError) as exc:
                await service.execute_in_session(session, ctx, command(upsert()))
            assert exc.value.code == ErrorCode.COMMON_INTERNAL_ERROR
    assert all(value == 0 for value in (await counters(async_tool_database, ctx)).values())


async def test_b03_memory_redesign_confirmation_replay_never_increments_confirmed_count(async_tool_database):
    ctx = await source_context(async_tool_database, context())
    service, original = writer(async_tool_database), command(upsert())
    first = await service.execute(ctx, original)
    replay = await service.execute(ctx, original)
    duplicate = await service.execute(ctx, command(upsert(expected=1)))
    assert replay.replayed and duplicate.response.status == "UNCHANGED"
    assert first.response.scope_revision == duplicate.response.scope_revision == 1
    async with async_tool_database.connect() as connection:
        row = (
            await connection.execute(
                sa.text(
                    "SELECT confirmed_count,version,confirmation_event_id"
                    " FROM runtime.user_memory WHERE tenant_id=:tenant"
                ),
                {"tenant": ctx.tenant_id},
            )
        ).one()
        assert row.confirmed_count == row.version == 1
        assert row.confirmation_event_id == ctx.confirmation_event_id
        receipt = (
            await connection.execute(
                sa.text(
                    "SELECT response_json FROM runtime.memory_command_receipt"
                    " WHERE tenant_id=:tenant AND command_id=:command"
                ),
                {"tenant": ctx.tenant_id, "command": original.command_id},
            )
        ).scalar_one()
        assert "content_json" not in str(receipt) and "blue" not in str(receipt)


@pytest.mark.parametrize("invalid", ["missing", "other_user", "model_event"])
async def test_b03_memory_redesign_user_write_requires_own_real_confirmation(async_tool_database, invalid):
    ctx = await source_context(
        async_tool_database,
        context(),
        event_type="ASSISTANT_MESSAGE" if invalid == "model_event" else "USER_MESSAGE",
    )
    if invalid == "missing":
        ctx = replace(ctx, confirmation_event_id=None)
    elif invalid == "other_user":
        ctx = replace(ctx, user_id=uuid4())
    with pytest.raises(AppError) as exc:
        await writer(async_tool_database).execute(ctx, command(upsert()))
    assert exc.value.code == ErrorCode.MEMORY_CONFIRMATION_INVALID
    assert all(value == 0 for value in (await counters(async_tool_database, ctx)).values())


async def test_b03_memory_redesign_history_contains_complete_immutable_after_states(async_tool_database):
    from muad_agent_runtime.infrastructure.models.runtime import UserMemory

    ctx, service = context(), writer(async_tool_database)
    await service.execute(ctx, command(upsert()))
    await service.execute(
        ctx, command(commands().MemoryMutation(action="DISABLE", memory_key="color", expected_version=1))
    )
    await service.execute(
        ctx, command(commands().MemoryMutation(action="DELETE", memory_key="color", expected_version=2))
    )
    async with async_tool_database.connect() as connection:
        history = (
            await connection.execute(
                sa.text(
                    "SELECT change_reason,after_json,command_id"
                    " FROM runtime.user_memory_history WHERE tenant_id=:tenant ORDER BY version"
                ),
                {"tenant": ctx.tenant_id},
            )
        ).all()
    assert [row.change_reason for row in history] == ["CREATE", "DISABLE", "ADMIN_DELETE"]
    assert [row.after_json["version"] for row in history] == [1, 2, 3]
    assert [row.after_json["enabled"] for row in history] == [True, False, False]
    assert [row.after_json["is_deleted"] for row in history] == [False, False, True]
    assert history[-1].after_json["retirement_revision"] == 3
    assert all(set(row.after_json) == set(UserMemory.__table__.columns.keys()) for row in history)
    assert len({row.command_id for row in history}) == 3


async def test_b03_memory_redesign_stale_revision_and_epoch_store_replayable_zero_effect(async_tool_database):
    ctx, service = context(), writer(async_tool_database)
    await service.execute(ctx, command(upsert()))
    stale = commands().MemoryCommand(
        command_id=uuid4().hex, endpoint="memory.job", expected_scope_revision=0, mutations=[upsert("new")]
    )
    first = await service.execute(ctx, stale)
    replay = await service.execute(ctx, stale)
    epoch = await service.execute(replace(ctx, expected_forget_epoch=1), command(upsert("new")))
    assert first.response.status == epoch.response.status == "STALE" and replay.replayed
    assert first.response.items == epoch.response.items == []
    assert first.response.scope_revision == epoch.response.scope_revision == 1
    assert (await counters(async_tool_database, ctx))["user_memory_history"] == 1


async def test_b03_memory_redesign_cross_user_command_collision_is_serialized(async_tool_database):
    service = writer(async_tool_database)
    contexts = [context(), context()]
    original = command(upsert(), command_id="tenant-wide-identity")
    results = await asyncio.gather(
        *(service.execute(ctx, original) for ctx in contexts), return_exceptions=True
    )
    failures = [item for item in results if isinstance(item, AppError)]
    assert len(failures) == 1 and failures[0].code == ErrorCode.IDEMPOTENCY_MISMATCH
    assert sum(not isinstance(item, BaseException) for item in results) == 1
    assert (await counters(async_tool_database, contexts[0]))["memory_scope"] == 1


async def test_b03_memory_redesign_enable_restore_keep_author_and_require_new_expiry(async_tool_database):
    from tests.agent_runtime.memory_redesign_write_support import seed_facts

    ctx, service = context(), writer(async_tool_database)
    await seed_facts(async_tool_database, ctx, 1, author="AGENT", expired=True)
    with pytest.raises(AppError) as exc:
        await service.execute(
            ctx, command(commands().MemoryMutation(action="ENABLE", memory_key="seed.1", expected_version=1))
        )
    assert exc.value.code == ErrorCode.MEMORY_VALUE_INVALID
    await service.execute(
        ctx,
        command(
            commands().MemoryMutation(
                action="ENABLE",
                memory_key="seed.1",
                expected_version=1,
                expires_at=datetime.now(UTC) + timedelta(days=3),
            )
        ),
    )
    row = (await service.list_entries(ctx.tenant_id, ctx.user_id))[0]
    assert row["author"] == "AGENT" and row["expires_at"] is not None
