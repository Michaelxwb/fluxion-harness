"""AsyncCallDispatcher.ensure: operation is the anchor, child is an idempotent carrier."""

from __future__ import annotations

import uuid

import pytest
from muad_agent_worker.application.agentic.async_dispatch import AsyncCallDispatcher
from muad_contracts import (
    CompletionMode,
    OperationStatus,
    TaskToolOperationOwner,
    TaskToolOperationRecoveryView,
    TaskToolOperationView,
)

pytestmark = pytest.mark.unit


class _Operations:
    def __init__(self) -> None:
        self.operation_id = uuid.uuid4()
        self.view = TaskToolOperationView(
            operation_id=self.operation_id,
            owner=TaskToolOperationOwner(task_execution_id=uuid.uuid4()),
            source_tool_call_id="call-1",
            tool_name="skill::s::run",
            completion_mode=CompletionMode.JOIN,
            status=OperationStatus.SUBMITTED,
            cancel_requested=False,
        )
        self.ensured: list[tuple[str, uuid.UUID, str, dict]] = []

    async def submit(self, **_):
        return self.view

    async def get_recovery(self, **_):
        return TaskToolOperationRecoveryView(
            operation_id=self.operation_id,
            status=self.view.status,
            task_id=self.view.task_id,
            tool_name=self.view.tool_name,
            arguments={"input": {"q": "frozen"}, "argv": []},
            input_hash="sha256:frozen",
        )

    async def get(self, **_):
        return self.view.model_copy(update={"task_id": uuid.uuid4()})


async def test_ensure_fills_missing_child_from_recovery_view() -> None:
    operations = _Operations()

    async def ensure_child(call_id, operation_id, tool_name, arguments):
        operations.ensured.append((call_id, operation_id, tool_name, dict(arguments)))
        return uuid.uuid4()

    state = await AsyncCallDispatcher(operations, ensure_child=ensure_child).ensure(
        tenant_id="t",
        actor_user_id=uuid.uuid4(),
        execution_id=uuid.uuid4(),
        call_id="call-1",
        tool_name="skill::s::run",
        arguments={"input": {"q": "current"}, "argv": []},
    )

    assert operations.ensured == [
        ("call-1", operations.operation_id, "skill::s::run", {"input": {"q": "frozen"}, "argv": []})
    ]
    assert state.task_id is not None
    assert state.ready is False
