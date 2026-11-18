"""TASK_NO_PROGRESS 的消息必须带上最后一次工具失败，不能只有一句 made no progress。"""

from __future__ import annotations

import uuid

import pytest
from muad_agent_core.agent import AgentRunStatus
from muad_agent_core.model import ModelUsage
from muad_agent_worker.application.agentic.run import AgenticOutcome
from muad_agent_worker.worker.agentic_executor import _envelope
from muad_agent_worker.worker.executor import TaskExecutionError


def test_no_progress_message_includes_last_failure() -> None:
    outcome = AgenticOutcome(
        status=AgentRunStatus.NO_PROGRESS,
        final_text="",
        turns=2,
        tool_calls=2,
        usage=ModelUsage(input_tokens=10, output_tokens=5),
        last_failure={
            "tool": "skill::f314c19f::execute",
            "code": "SKILL_EXECUTION_FAILED",
            "message": "run.py: error: the following arguments are required: --company-id",
        },
    )

    with pytest.raises(TaskExecutionError) as caught:
        _envelope(outcome, object())  # NO_PROGRESS 分支不使用 task

    assert caught.value.code == "TASK_NO_PROGRESS"
    assert "SKILL_EXECUTION_FAILED" in str(caught.value)
    assert "--company-id" in str(caught.value)
