"""TASK-007: resolution/recall surface and forget-by-id through MemoryService."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts.memory import ForgetMemoryInputV2, RememberMemoryInputV2
from sqlalchemy.ext.asyncio import async_sessionmaker

from muad_agent_runtime.application.memory_commands import MemoryWriteContext
from muad_agent_runtime.application.memory_context import MemoryContextLoader
from muad_agent_runtime.application.memory_queries import MemoryCandidateRowV2, MemorySnapshotV2
from muad_agent_runtime.application.memory_service import MemoryService
from muad_contracts.platform_settings import MemoryPolicySettings
from tests.async_tool_datastores import async_tool_database as async_tool_database

NOW = datetime(2026, 11, 14, tzinfo=UTC)


def _snapshot():
    row = MemoryCandidateRowV2(
        id=uuid4(),
        memory_key="v1.slot.preference.report_format.x",
        memory_kind="PREFERENCE",
        predicate="report_format",
        scope_state="ABSENT",
        scope={},
        content={"format": "PDF"},
        title="报告 PDF",
        summary="内部报告默认 PDF",
        tags=(),
        cardinality="SINGLE",
        priority="REQUIRED",
        author="USER",
        authority="USER_EXPLICIT",
        version=1,
        enabled=True,
        expires_at=None,
        last_confirmed_at=NOW,
    )
    return MemorySnapshotV2(None, 1, 0, NOW, (row,))


class _Cache:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    async def read_for_injection(self, *, max_items, max_bytes):
        return self.snapshot

    def invalidate(self):
        self.snapshot = None


async def test_resolution_returns_memory_id_and_structured_fields():
    loader = MemoryContextLoader(
        _Cache(_snapshot()), settings=MemoryPolicySettings(), query="报告 PDF"
    )
    component = await loader.refresh()
    resolution = loader.resolution
    assert component is not None and component.text is not None
    assert resolution is not None and resolution.status == "resolved"
    assert resolution.clarification_reason is None
    entry = resolution.entries[0]
    assert entry.id is not None
    assert entry.memory_key.startswith("v1.slot.preference.")
    assert entry.content == {"format": "PDF"}


def test_forget_input_rejects_legacy_key_handle():
    with pytest.raises(Exception):
        ForgetMemoryInputV2.model_validate(
            {"memory_key": "color", "expected_version": 1}
        )


async def test_forget_uses_memory_id_and_version(async_tool_database):
    ctx = MemoryWriteContext(
        tenant_id="v2-forget", user_id=uuid4(), actor_type="ADMIN", actor_id="admin"
    )
    service = MemoryService(async_sessionmaker(async_tool_database, expire_on_commit=False))
    candidate = RememberMemoryInputV2.model_validate(
        {
            "memory_kind": "PREFERENCE",
            "predicate": "report_format",
            "content": {"format": "PDF"},
            "title": "报告",
        }
    ).normalized(taxonomy_version=1)
    created = await service.apply_candidate(
        ctx, candidate, command_id="f-create", endpoint="memory.create"
    )
    import sqlalchemy as sa

    async with async_tool_database.begin() as connection:
        memory_id = (
            await connection.execute(
                sa.text(
                    "SELECT id FROM runtime.user_memory WHERE tenant_id=:t AND user_id=:u AND memory_key=:k"
                ),
                {"t": ctx.tenant_id, "u": ctx.user_id, "k": created.memory_key},
            )
        ).scalar_one()
    outcome = await service.delete_memory(
        ctx,
        memory_id,
        expected_version=1,
        command_id="f-delete",
        endpoint="memory.delete",
    )
    assert outcome.status == "DELETED"

    with pytest.raises(AppError) as exc:
        await service.delete_memory(
            ctx,
            memory_id,
            expected_version=1,
            command_id="f-delete-stale",
            endpoint="memory.delete",
        )
    assert exc.value.code == ErrorCode.MEMORY_VERSION_CONFLICT
