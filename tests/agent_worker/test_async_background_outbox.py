"""TASK-002: every Run-owned root with an operation publishes its terminal result."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from muad_agent_worker.application.terminal_tasks import TerminalChange, write_terminal
from muad_agent_worker.infrastructure.models.runtime_operations import RuntimeResultOutbox
from muad_agent_worker.infrastructure.models.task import TaskExecution
from muad_contracts import CompletionMode, TerminalStatus
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.async_tool_helpers import seed_operation

pytestmark = pytest.mark.integration


async def _outbox_count(factory, task_id) -> int:
    async with factory() as session:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(RuntimeResultOutbox)
                .where(RuntimeResultOutbox.task_id == task_id)
            )
            or 0
        )


@pytest.mark.parametrize("mode", [CompletionMode.JOIN, CompletionMode.DETACH])
async def test_run_root_publishes_regardless_of_completion_mode(async_tool_database, mode) -> None:
    factory = async_sessionmaker(async_tool_database, expire_on_commit=False)
    task, _, _ = await seed_operation(factory, mode=mode)

    async with factory() as session, session.begin():
        assert await write_terminal(
            session,
            task,
            TerminalChange(status=TerminalStatus.COMPLETED, result={"ok": True}),
            datetime.now(UTC),
        )

    assert await _outbox_count(factory, task.id) == 1


async def test_scheduled_task_does_not_publish_to_runtime(async_tool_database) -> None:
    factory = async_sessionmaker(async_tool_database, expire_on_commit=False)
    task, _, _ = await seed_operation(factory, mode=CompletionMode.JOIN)
    async with factory() as session, session.begin():
        await session.execute(
            update(TaskExecution)
            .where(TaskExecution.id == task.id)
            .values(source_run_id=None, source_operation_id=None, schedule_id=None)
        )
        await session.flush()
        stored = await session.get(TaskExecution, task.id)
        assert stored is not None

    async with factory() as session, session.begin():
        assert await write_terminal(
            session,
            stored,
            TerminalChange(status=TerminalStatus.COMPLETED, result={"ok": True}),
            datetime.now(UTC),
        )

    assert await _outbox_count(factory, task.id) == 0


async def test_run_task_without_operation_does_not_publish(async_tool_database) -> None:
    factory = async_sessionmaker(async_tool_database, expire_on_commit=False)
    task, _, _ = await seed_operation(factory, mode=CompletionMode.DETACH)
    async with factory() as session, session.begin():
        await session.execute(
            update(TaskExecution)
            .where(TaskExecution.id == task.id)
            .values(source_operation_id=None)
        )
        await session.flush()
        stored = await session.get(TaskExecution, task.id)
        assert stored is not None

    async with factory() as session, session.begin():
        assert await write_terminal(
            session,
            stored,
            TerminalChange(status=TerminalStatus.COMPLETED, result={"ok": True}),
            datetime.now(UTC),
        )

    assert await _outbox_count(factory, task.id) == 0
