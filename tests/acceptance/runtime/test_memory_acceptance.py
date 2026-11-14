"""TASK-009: v2 acceptance on real PostgreSQL fixtures."""

from __future__ import annotations

from uuid import uuid4

import pytest
import sqlalchemy as sa
from muad_agent_runtime.application.memory_commands import MemoryWriteContext
from muad_agent_runtime.application.memory_writer import MemoryWriteCommand, MemoryWriter
from muad_contracts.memory import RememberMemoryInput
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.async_tool_datastores import async_tool_database as async_tool_database
from tests.async_tool_datastores import async_tool_datastore as async_tool_datastore

pytestmark = pytest.mark.integration


async def test_v2_delete_candidate_and_restore_are_observable_in_pg(async_tool_database):
    ctx = MemoryWriteContext(
        tenant_id="v2-acceptance", user_id=uuid4(), actor_type="ADMIN", actor_id="admin"
    )
    writer = MemoryWriter(async_sessionmaker(async_tool_database, expire_on_commit=False))
    candidate = RememberMemoryInput.model_validate(
        {
            "memory_kind": "PREFERENCE",
            "predicate": "report_format",
            "content": {"format": "PDF"},
            "title": "报告",
        }
    ).normalized(taxonomy_version=1)
    created = await writer.execute(ctx, MemoryWriteCommand("a-create", "accept", "UPSERT", candidate))
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
        ctx, MemoryWriteCommand("a-delete", "accept", "DELETE", memory_id=memory_id, expected_version=1)
    )
    restored = await writer.restore(ctx, memory_id, expected_version=deleted.version)
    assert restored.memory_key == created.memory_key
    async with async_tool_database.begin() as connection:
        history = (
            await connection.execute(
                sa.text(
                    "SELECT change_reason FROM runtime.user_memory_history"
                    " WHERE tenant_id=:t AND user_id=:u ORDER BY version"
                ),
                {"t": ctx.tenant_id, "u": ctx.user_id},
            )
        ).scalars().all()
    assert history[-2:] == ["ADMIN_DELETE", "RESTORE"]
