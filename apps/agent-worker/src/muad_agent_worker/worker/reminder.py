"""REMINDER 执行器：把创建时冻结的固定文本直接变成结果（design §2.3.2 / §3.2.2）。

它没有 Model、Skill、Tool 依赖——只从本次执行的 `input_json` 取固定文本并生成执行信封。
结果由 Worker 的终态事务先落 `task_execution.result_json`，投递是之后的独立边界。
"""

from __future__ import annotations

from typing import Any

from muad_agent_core.skill import SkillExecutionStatus

from ..infrastructure.models.task import TaskExecution
from .executor import TaskExecutionError

REMINDER_TEXT_KEY = "reminder_text"
REMINDER_RESULT_TEXT_KEY = "text"


def reminder_text(task: TaskExecution) -> str:
    """固定文本来自 Fire 冻结的本次输入；缺失/空白是确定性失败，不重新理解自然语言。"""
    payload = task.input_json if isinstance(task.input_json, dict) else {}
    text = payload.get(REMINDER_TEXT_KEY)
    if not isinstance(text, str) or not text.strip():
        raise TaskExecutionError(
            "REMINDER_TEXT_MISSING",
            "reminder execution input has no fixed reminder_text",
            retryable=False,
        )
    return text


class ReminderExecutor:
    """固定文本提醒：零模型、零 Skill、零工具调用。"""

    async def execute(self, task: TaskExecution) -> dict[str, Any]:
        text = reminder_text(task)
        return {
            "status": str(SkillExecutionStatus.SUCCEEDED),
            "result": {REMINDER_RESULT_TEXT_KEY: text},
            "stdout": "",
            "stderr": "",
            "duration_ms": 0,
            "exit_code": 0,
        }
