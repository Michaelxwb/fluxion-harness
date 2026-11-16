"""TASK-009: v2 tenant isolation is enforced by query scope."""

from __future__ import annotations

from uuid import uuid4

import pytest
from muad_agent_runtime.application.memory_commands import MemoryWriteContext
from muad_agent_runtime.application.memory_queries import MemoryQueries, MemoryReadScope
from muad_agent_runtime.application.memory_writer import MemoryWriteCommand, MemoryWriter
from muad_contracts.memory import RememberMemoryInput
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.async_tool_datastores import async_tool_database as async_tool_database
from tests.async_tool_datastores import async_tool_datastore as async_tool_datastore

pytestmark = pytest.mark.integration


async def test_tenant_scope_does_not_see_other_users_memory(async_tool_database):
    owner = MemoryWriteContext(tenant_id="tenant-a", user_id=uuid4(), actor_type="ADMIN", actor_id="a")
    writer = MemoryWriter(async_sessionmaker(async_tool_database, expire_on_commit=False))
    candidate = RememberMemoryInput.model_validate(
        {
            "memory_kind": "NOTE",
            "predicate": "general",
            "content": {"text": "tenant secret"},
            "title": "secret",
        }
    ).normalized(taxonomy_version=1)
    await writer.execute(owner, MemoryWriteCommand("sec-create", "accept", "UPSERT", candidate))

    queries = MemoryQueries(async_sessionmaker(async_tool_database, expire_on_commit=False))
    snapshot = await queries.read_snapshot(
        MemoryReadScope(tenant_id="tenant-b", user_id=owner.user_id)
    )
    assert snapshot.rows == ()
