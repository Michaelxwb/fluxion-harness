"""TASK-003: PENDING candidates target existing rows and ACCEPT is explicit."""

from __future__ import annotations

from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker

from muad_agent_runtime.application.memory_commands import MemoryWriteContext
from muad_agent_runtime.application.memory_writer import MemoryV2Command, MemoryV2Writer
from muad_contracts.memory import RememberMemoryInputV2
from tests.async_tool_datastores import async_tool_database as async_tool_database

pytestmark = pytest.mark.integration


def _ctx(actor="AGENT", *, seq=1, run_id=None):
    from muad_contracts.memory import MemorySourceEvidence

    return MemoryWriteContext(
        tenant_id="v2-candidates",
        user_id=uuid4(),
        actor_type=actor,
        actor_id="tester",
        source_run_id=run_id or uuid4(),
        evidence=MemorySourceEvidence(
            source_event_id=uuid4(), source_seq=seq, source_scope_revision=0
        ),
    )


def _same_ctx(ctx, *, seq):
    from dataclasses import replace

    from muad_contracts.memory import MemorySourceEvidence

    return replace(
        ctx,
        evidence=MemorySourceEvidence(
            source_event_id=uuid4(), source_seq=seq, source_scope_revision=0
        ),
    )


def _candidate(value="PDF"):
    return RememberMemoryInputV2.model_validate(
        {
            "memory_kind": "PREFERENCE",
            "predicate": "report_format",
            "content": {"format": value},
            "title": "内部报告",
        }
    ).normalized(taxonomy_version=1)


def _writer(db):
    return MemoryV2Writer(async_sessionmaker(db, expire_on_commit=False))


async def test_agent_different_value_creates_target_bound_pending(async_tool_database):
    ctx, writer = _ctx(), _writer(async_tool_database)
    created = await writer.execute(ctx, MemoryV2Command("seed", "memory.create", "UPSERT", _candidate()))
    pending = await writer.execute(
        _same_ctx(ctx, seq=2),
        MemoryV2Command("propose", "memory.infer", "UPSERT", _candidate("WORD")),
    )
    assert pending.status == "CANDIDATE"
    assert pending.memory_key == created.memory_key
    async with async_tool_database.begin() as connection:
        row = (
            await connection.execute(
                sa.text(
                    "SELECT memory_key, base_version, status, forget_epoch FROM runtime.memory_conflict_candidate"
                    " WHERE tenant_id=:t AND user_id=:u AND id=:id"
                ),
                {"t": ctx.tenant_id, "u": ctx.user_id, "id": pending.candidate_id},
            )
        ).one()
    assert row.memory_key == created.memory_key
    assert row.base_version == 1 and row.status == "PENDING"


async def test_accept_candidate_writes_current_value_and_authority(async_tool_database):
    infer, writer = _ctx(), _writer(async_tool_database)
    await writer.execute(infer, MemoryV2Command("seed2", "memory.create", "UPSERT", _candidate()))
    pending = await writer.execute(
        _same_ctx(infer, seq=2),
        MemoryV2Command("propose2", "memory.infer", "UPSERT", _candidate("WORD")),
    )
    admin = MemoryWriteContext(
        tenant_id=infer.tenant_id, user_id=infer.user_id, actor_type="ADMIN", actor_id="admin"
    )
    accepted = await writer.accept_candidate(admin, pending.candidate_id, expected_version=1)
    assert accepted.status == "ACCEPTED" and accepted.version == 2
    async with async_tool_database.begin() as connection:
        row = (
            await connection.execute(
                sa.text(
                    "SELECT content_json, author, authority, version FROM runtime.user_memory"
                    " WHERE tenant_id=:t AND user_id=:u AND memory_key=:k"
                ),
                {"t": admin.tenant_id, "u": admin.user_id, "k": admin_context_key(await _key(connection, admin))},
            )
        ).one()
    assert row.content_json == {"format": "WORD"}
    assert row.author == "ADMIN" and row.authority == "ADMIN_EDIT" and row.version == 2


async def _key(connection, ctx):
    return (
        await connection.execute(
            sa.text("SELECT memory_key FROM runtime.user_memory WHERE tenant_id=:t AND user_id=:u"),
            {"t": ctx.tenant_id, "u": ctx.user_id},
        )
    ).scalar_one()


def admin_context_key(value):
    return value
