"""TASK-002: Run terminal notification suppression is based on accepted DETACH operations."""

from __future__ import annotations

import pytest
from muad_agent_runtime.application.run_service import _submitted_detached_task
from muad_agent_runtime.infrastructure.models.async_tools import ToolOperation
from muad_contracts import CompletionMode
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.async_tool_helpers import seed_operation

pytestmark = pytest.mark.integration


async def _set_operation(factory, operation_id, **values) -> None:
    async with factory() as session, session.begin():
        await session.execute(
            update(ToolOperation).where(ToolOperation.id == operation_id).values(**values)
        )


async def test_detached_operation_suppresses_run_terminal_notification(async_tool_database) -> None:
    factory = async_sessionmaker(async_tool_database, expire_on_commit=False)
    task, operation, run = await seed_operation(factory, mode=CompletionMode.DETACH)
    await _set_operation(factory, operation.id, task_id=task.id)

    async with factory() as session:
        assert await _submitted_detached_task(session, run) is True


async def test_join_operation_does_not_suppress_run_terminal_notification(async_tool_database) -> None:
    factory = async_sessionmaker(async_tool_database, expire_on_commit=False)
    task, operation, run = await seed_operation(factory, mode=CompletionMode.JOIN)
    await _set_operation(factory, operation.id, task_id=task.id)

    async with factory() as session:
        assert await _submitted_detached_task(session, run) is False


async def test_detached_operation_without_task_is_not_accepted(async_tool_database) -> None:
    factory = async_sessionmaker(async_tool_database, expire_on_commit=False)
    _, operation, run = await seed_operation(factory, mode=CompletionMode.DETACH)
    await _set_operation(factory, operation.id, task_id=None)

    async with factory() as session:
        assert await _submitted_detached_task(session, run) is False
