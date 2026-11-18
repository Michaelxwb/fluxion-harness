"""TASK-003: late results become model-visible facts without reviving the Run."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from muad_agent_runtime.application.async_tools.materialization import (
    materialize_late_results,
)
from muad_agent_runtime.infrastructure.models.async_tools import ToolOperation, ToolResultInbox
from muad_agent_runtime.infrastructure.models.runtime import (
    CanonicalEvent,
    Conversation,
    RunRecord,
)
from muad_contracts import RunStatus
from sqlalchemy import func, select

from tests.async_tool_continuations import operation, receipt, running_source

pytestmark = pytest.mark.integration


async def _receipt_late(async_tool_database):
    factory, context, skill, run, _ = await running_source(async_tool_database)
    op = await operation(factory, context, skill)
    async with factory() as session, session.begin():
        row = await session.get(RunRecord, run.id)
        assert row is not None
        row.status = RunStatus.COMPLETED
        row.end_time = datetime.now(UTC)
    await receipt(factory, run, op, {"late": "original result"})
    async with factory() as session:
        inbox = await session.scalar(select(ToolResultInbox))
        assert inbox is not None and inbox.late and not inbox.materialized
        assert (
            await session.scalar(
                select(CanonicalEvent.id).where(CanonicalEvent.event_type == "BACKGROUND_RESULT")
            )
            is None
        )
    return factory, run, op


async def test_late_result_is_materialized_once_without_reviving_run(async_tool_database) -> None:
    factory, run, op = await _receipt_late(async_tool_database)

    assert await materialize_late_results(factory) == 1
    assert await materialize_late_results(factory) == 0

    async with factory() as session:
        stored_run = await session.get(RunRecord, run.id)
        assert stored_run is not None and stored_run.status == RunStatus.COMPLETED
        stored_op = await session.get(ToolOperation, op.id)
        assert stored_op is not None and stored_op.status.value == "LATE"
        inbox = await session.scalar(select(ToolResultInbox))
        assert inbox is not None and inbox.materialized and inbox.late
        events = list(
            await session.scalars(
                select(CanonicalEvent).where(CanonicalEvent.event_type == "BACKGROUND_RESULT")
            )
        )
        assert len(events) == 1
        assert "original result" in events[0].payload_json["content"]


async def test_deleted_conversation_is_not_revived(async_tool_database) -> None:
    factory, run, _ = await _receipt_late(async_tool_database)
    async with factory() as session, session.begin():
        conversation = await session.scalar(
            select(Conversation).where(Conversation.id == run.conversation_id)
        )
        assert conversation is not None
        conversation.is_deleted = True

    assert await materialize_late_results(factory) == 0
    async with factory() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(CanonicalEvent)
                .where(CanonicalEvent.event_type == "BACKGROUND_RESULT")
            )
            == 0
        )
        inbox = await session.scalar(select(ToolResultInbox))
        assert inbox is not None and not inbox.materialized
