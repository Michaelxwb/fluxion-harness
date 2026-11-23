"""Async admission stream types keep DETACH and JOIN distinct."""

from __future__ import annotations

from muad_agent_runtime.application.async_tools.control_dispatcher import (
    admission_stream_type,
)
from muad_contracts import CompletionMode


def test_detach_uses_the_task_accepted_event_for_im_fixed_receipts() -> None:
    assert (
        admission_stream_type(late=False, completion_mode=CompletionMode.DETACH)
        == "task.accepted"
    )


def test_join_keeps_streaming_as_tool_submitted() -> None:
    assert (
        admission_stream_type(late=False, completion_mode=CompletionMode.JOIN)
        == "tool.submitted"
    )


def test_late_admission_keeps_the_late_result_type() -> None:
    assert (
        admission_stream_type(late=True, completion_mode=CompletionMode.DETACH)
        == "tool.result.late"
    )
