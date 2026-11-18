"""TASK-001 contract bounds for raw stderr and Task artifact purpose."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from muad_contracts import (
    MAX_TOOL_ERROR_MESSAGE_CHARS,
    RunToolOperationOwner,
    TaskArtifactWriteRequest,
    TerminalStatus,
    ToolResultRequest,
)
from pydantic import ValidationError


def _result_request(**overrides):
    payload = {
        "event_id": uuid4(),
        "operation_id": uuid4(),
        "task_id": uuid4(),
        "task_event_seq": 1,
        "owner": RunToolOperationOwner(run_id=uuid4()),
        "actor_user_id": uuid4(),
        "task_snapshot_hash": "sha256:" + "a" * 64,
        "terminal_status": TerminalStatus.FAILED,
        "completed_at": datetime.now(UTC),
        "error_code": "SKILL_EXECUTION_FAILED",
    }
    payload.update(overrides)
    return ToolResultRequest(**payload)


def test_error_message_accepts_protocol_bound_and_rejects_over_bound() -> None:
    _result_request(error_message="x" * MAX_TOOL_ERROR_MESSAGE_CHARS)
    with pytest.raises(ValidationError):
        _result_request(error_message="x" * (MAX_TOOL_ERROR_MESSAGE_CHARS + 1))


def test_task_artifact_purpose_defaults_to_output_and_accepts_tool_result() -> None:
    request = TaskArtifactWriteRequest(
        task_execution_id=uuid4(),
        filename="tool-result.json",
        media_type="application/json",
        size=1,
        checksum="sha256:" + "b" * 64,
        base_version=0,
    )
    assert request.purpose == "OUTPUT"
    assert (
        TaskArtifactWriteRequest(
            task_execution_id=uuid4(),
            purpose="TOOL_RESULT",
            filename="tool-result.json",
            media_type="application/json",
            size=1,
            checksum="sha256:" + "b" * 64,
            base_version=0,
        ).purpose
        == "TOOL_RESULT"
    )
