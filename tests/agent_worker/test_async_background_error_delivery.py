"""TASK-001: raw stderr stays visible, bounded outbox projection, terminal survives big results."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from muad_agent_worker.application.terminal_tasks import TerminalChange, write_terminal
from muad_agent_worker.infrastructure.models.runtime_operations import RuntimeResultOutbox
from muad_agent_worker.infrastructure.models.task import TaskExecution
from muad_agent_worker.worker.execution_outcomes import (
    FailureDisposition,
    classify_failure,
)
from muad_agent_worker.worker.service import WorkerLoop
from muad_contracts import (
    MAX_TOOL_ERROR_MESSAGE_CHARS,
    CompletionMode,
    TaskArtifactWriteResponse,
    TerminalStatus,
    ToolRetrySafety,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.async_tool_helpers import seed_operation

pytestmark = pytest.mark.integration


async def _task_and_outbox(factory, task_id: UUID):
    async with factory() as session:
        task = await session.scalar(select(TaskExecution).where(TaskExecution.id == task_id))
        outbox = await session.scalar(
            select(RuntimeResultOutbox).where(RuntimeResultOutbox.task_id == task_id)
        )
        count = await session.scalar(
            select(func.count())
            .select_from(RuntimeResultOutbox)
            .where(RuntimeResultOutbox.task_id == task_id)
        )
    return task, outbox, int(count or 0)


def test_non_retryable_failure_keeps_original_stderr() -> None:
    outcome = classify_failure(
        retry_safety=ToolRetrySafety.NON_RETRYABLE,
        error_code="SKILL_EXECUTION_FAILED",
        error_message="HTTP 502: Bad Gateway for https://api.example.com/v1/check",
        retryable=True,
    )

    assert outcome.disposition is FailureDisposition.UNVERIFIED
    assert outcome.error_code == "TASK_RESULT_UNVERIFIED"
    assert outcome.error_message == "HTTP 502: Bad Gateway for https://api.example.com/v1/check"


async def test_long_error_is_stored_full_and_outbox_is_bounded(async_tool_database) -> None:
    factory = async_sessionmaker(async_tool_database, expire_on_commit=False)
    task, _, _ = await seed_operation(factory, mode=CompletionMode.JOIN)
    raw = "api_key=super-secret\n" + ("traceback-line\n" * 700)

    async with factory() as session, session.begin():
        assert await write_terminal(
            session,
            task,
            TerminalChange(
                status=TerminalStatus.FAILED,
                error_code="SKILL_EXECUTION_FAILED",
                error_message=raw,
            ),
            datetime.now(UTC),
        )

    stored, outbox, _ = await _task_and_outbox(factory, task.id)
    assert stored is not None and stored.error_message is not None
    assert "api_key=***" in stored.error_message
    assert len(stored.error_message) > MAX_TOOL_ERROR_MESSAGE_CHARS
    assert outbox is not None
    projected = outbox.payload_json["error_message"]
    assert len(projected) == MAX_TOOL_ERROR_MESSAGE_CHARS
    assert projected.startswith("api_key=***")
    assert "super-secret" not in projected


async def test_big_result_with_projection_keeps_full_task_result(async_tool_database) -> None:
    factory = async_sessionmaker(async_tool_database, expire_on_commit=False)
    task, _, _ = await seed_operation(factory, mode=CompletionMode.JOIN)
    full_result = {"payload": "x" * (300 * 1024)}
    projection = {
        "artifact": {
            "artifact_id": str(uuid4()),
            "size": len(full_result["payload"]),
            "checksum": "sha256:" + "a" * 64,
            "preview": "payload: xxxx…",
        }
    }

    async with factory() as session, session.begin():
        assert await write_terminal(
            session,
            task,
            TerminalChange(
                status=TerminalStatus.COMPLETED,
                result=full_result,
                outbox_result=projection,
            ),
            datetime.now(UTC),
        )

    stored, outbox, count = await _task_and_outbox(factory, task.id)
    assert stored is not None and stored.status == "COMPLETED"
    assert stored.result_json == full_result
    assert count == 1
    assert outbox is not None
    assert outbox.payload_json["result"] == projection


async def test_big_result_without_projection_does_not_rollback_terminal(async_tool_database) -> None:
    factory = async_sessionmaker(async_tool_database, expire_on_commit=False)
    task, _, _ = await seed_operation(factory, mode=CompletionMode.JOIN)

    async with factory() as session, session.begin():
        assert await write_terminal(
            session,
            task,
            TerminalChange(
                status=TerminalStatus.COMPLETED,
                result={"payload": "x" * (300 * 1024)},
            ),
            datetime.now(UTC),
        )

    stored, outbox, count = await _task_and_outbox(factory, task.id)
    assert stored is not None and stored.status == "COMPLETED"
    assert count == 0
    assert outbox is None


class _ArtifactWriter:
    def __init__(self) -> None:
        self.purposes: list[str] = []

    async def write(self, **kwargs):
        self.purposes.append(str(kwargs["purpose"]))
        return TaskArtifactWriteResponse(
            artifact_id=uuid4(),
            version="1",
            checksum=str(kwargs["checksum"]),
        )


async def test_worker_externalizes_big_result_before_terminal(async_tool_database) -> None:
    factory = async_sessionmaker(async_tool_database, expire_on_commit=False)
    task, _, _ = await seed_operation(factory, mode=CompletionMode.JOIN)
    writer = _ArtifactWriter()
    loop = WorkerLoop(factory, artifacts=writer)  # type: ignore[arg-type]

    projection = await loop._bounded_outbox_result(task, {"payload": "x" * (300 * 1024)})

    assert writer.purposes == ["TOOL_RESULT"]
    assert projection is not None
    assert projection["artifact"]["artifact_id"]  # type: ignore[index]
    assert projection["artifact"]["size"] > 300 * 1024  # type: ignore[index]
