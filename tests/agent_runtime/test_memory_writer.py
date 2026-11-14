"""TASK-003: stable identities, receipts, delete/clear epoch semantics."""

from __future__ import annotations

from uuid import uuid4

import pytest
import sqlalchemy as sa
from muad_agent_runtime.application.memory_commands import MemoryWriteContext
from muad_agent_runtime.application.memory_service import MemoryService
from muad_agent_runtime.application.memory_writer import MemoryWriteCommand, MemoryWriter
from muad_contracts.memory import RememberMemoryInput
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.async_tool_datastores import async_tool_database as async_tool_database

pytestmark = pytest.mark.integration


def _ctx(*, actor="USER", confirmation=True):
    return MemoryWriteContext(
        tenant_id="v2-writer",
        user_id=uuid4(),
        actor_type=actor,
        actor_id="tester",
        confirmation_event_id=uuid4() if confirmation else None,
    )


def _candidate(**overrides):
    payload = {
        "memory_kind": "PREFERENCE",
        "predicate": "report_format",
        "scope_state": "ABSENT",
        "content": {"format": "PDF"},
        "title": "内部报告",
        "tags": ["topic:报告格式"],
    }
    payload.update(overrides)
    return RememberMemoryInput.model_validate(payload).normalized(taxonomy_version=1)


def _writer(db):
    return MemoryWriter(async_sessionmaker(db, expire_on_commit=False))


async def test_single_identity_create_and_replay(async_tool_database):
    ctx, writer = _ctx(), _writer(async_tool_database)
    command = MemoryWriteCommand("cmd-1", "memory.create", "UPSERT", _candidate())
    created = await writer.execute(ctx, command)
    replay = await writer.execute(ctx, command)
    assert created.status == "CREATED" and created.memory_key.startswith("v1.slot.preference.report_format.")
    assert replay.replayed is True and replay.memory_key == created.memory_key


async def test_same_command_with_different_content_conflicts(async_tool_database):
    ctx, writer = _ctx(), _writer(async_tool_database)
    await writer.execute(ctx, MemoryWriteCommand("cmd-2", "memory.create", "UPSERT", _candidate()))
    with pytest.raises(Exception) as exc:
        await writer.execute(
            ctx,
            MemoryWriteCommand("cmd-2", "memory.create", "UPSERT", _candidate(content={"format": "WORD"})),
        )
    assert "MEMORY_IDEMPOTENCY_CONFLICT" in str(exc.value)


async def test_delete_only_revokes_its_key_and_does_not_advance_epoch(async_tool_database):
    ctx, writer = _ctx(actor="ADMIN", confirmation=False), _writer(async_tool_database)
    created = await writer.execute(
        ctx, MemoryWriteCommand("cmd-create", "memory.create", "UPSERT", _candidate())
    )
    assert created.memory_key is not None
    async with async_tool_database.begin() as connection:
        before = (
            await connection.execute(
                sa.text(
                    "SELECT revision, forget_epoch FROM runtime.memory_scope"
                    " WHERE tenant_id=:t AND user_id=:u"
                ),
                {"t": ctx.tenant_id, "u": ctx.user_id},
            )
        ).one()
        memory_id = (
            await connection.execute(
                sa.text(
                    "SELECT id FROM runtime.user_memory"
                    " WHERE tenant_id=:t AND user_id=:u AND memory_key=:k"
                ),
                {"t": ctx.tenant_id, "u": ctx.user_id, "k": created.memory_key},
            )
        ).scalar_one()
    deleted = await writer.execute(
        ctx,
        MemoryWriteCommand("cmd-delete", "memory.delete", "DELETE", memory_id=memory_id, expected_version=1),
    )
    assert deleted.status == "DELETED"
    async with async_tool_database.begin() as connection:
        after = (
            await connection.execute(
                sa.text(
                    "SELECT revision, forget_epoch FROM runtime.memory_scope"
                    " WHERE tenant_id=:t AND user_id=:u"
                ),
                {"t": ctx.tenant_id, "u": ctx.user_id},
            )
        ).one()
    assert after.revision == before.revision + 1
    assert after.forget_epoch == before.forget_epoch


async def test_note_gets_stable_uuid_and_service_entry(async_tool_database):
    ctx, writer = _ctx(), _writer(async_tool_database)
    note = RememberMemoryInput.model_validate(
        {
            "memory_kind": "NOTE",
            "predicate": "general",
            "content": {"text": "A 客户关注数据安全"},
            "title": "A 客户备注",
        }
    ).normalized(taxonomy_version=1)
    command = MemoryWriteCommand("note-1", "memory.create", "UPSERT", note)
    created = await writer.execute(ctx, command)
    replay = await writer.execute(ctx, command)
    assert created.memory_key.startswith("v1.note.")
    assert replay.memory_key == created.memory_key
    service = MemoryService(async_sessionmaker(async_tool_database, expire_on_commit=False))
    via_service = await service.apply_candidate(
        ctx, note, command_id="note-2", endpoint="memory.create"
    )
    assert via_service.memory_key.startswith("v1.note.")


async def test_set_member_content_patch_is_immutable(async_tool_database):
    ctx, writer = _ctx(actor="ADMIN", confirmation=False), _writer(async_tool_database)
    owned = RememberMemoryInput.model_validate(
        {
            "memory_kind": "FACT",
            "predicate": "owned_system",
            "content": {"value": "防火墙"},
            "title": "负责系统",
        }
    ).normalized(taxonomy_version=1)
    created = await writer.execute(ctx, MemoryWriteCommand("set-1", "memory.create", "UPSERT", owned))
    async with async_tool_database.begin() as connection:
        memory_id = (
            await connection.execute(
                sa.text(
                    "SELECT id FROM runtime.user_memory"
                    " WHERE tenant_id=:t AND user_id=:u AND memory_key=:k"
                ),
                {"t": ctx.tenant_id, "u": ctx.user_id, "k": created.memory_key},
            )
        ).scalar_one()
    with pytest.raises(Exception) as exc:
        await writer.patch(ctx, memory_id, expected_version=1, updates={"content": {"value": "EDR"}})
    assert "MEMORY_IDENTITY_IMMUTABLE" in str(exc.value)


async def test_restore_keeps_identity_and_does_not_require_epoch_match(async_tool_database):
    ctx, writer = _ctx(actor="ADMIN", confirmation=False), _writer(async_tool_database)
    created = await writer.execute(
        ctx, MemoryWriteCommand("restore-create", "memory.create", "UPSERT", _candidate())
    )
    async with async_tool_database.begin() as connection:
        memory_id = (
            await connection.execute(
                sa.text(
                    "SELECT id FROM runtime.user_memory"
                    " WHERE tenant_id=:t AND user_id=:u AND memory_key=:k"
                ),
                {"t": ctx.tenant_id, "u": ctx.user_id, "k": created.memory_key},
            )
        ).scalar_one()
    deleted = await writer.execute(
        ctx,
        MemoryWriteCommand(
            "restore-delete", "memory.delete", "DELETE", memory_id=memory_id, expected_version=1
        ),
    )
    restored = await writer.restore(ctx, memory_id, expected_version=deleted.version)
    assert restored.status == "RESTORED" and restored.memory_key == created.memory_key


async def test_clear_advances_forget_epoch(async_tool_database):
    ctx, writer = _ctx(actor="ADMIN", confirmation=False), _writer(async_tool_database)
    await writer.execute(ctx, MemoryWriteCommand("cmd-c", "memory.create", "UPSERT", _candidate()))
    cleared = await writer.execute(ctx, MemoryWriteCommand("cmd-clear", "memory.clear", "CLEAR"))
    assert cleared.status == "CLEARED"
    async with async_tool_database.begin() as connection:
        epoch = (
            await connection.execute(
                sa.text("SELECT forget_epoch FROM runtime.memory_scope WHERE tenant_id=:t AND user_id=:u"),
                {"t": ctx.tenant_id, "u": ctx.user_id},
            )
        ).scalar_one()
        assert epoch == 1
