"""AGENTIC 执行器：把 Kernel 的中立结论翻成 Worker 的执行信封。

分工是刻意的：

- `application/agentic/**` 只实现中立端口（模型/工具/记忆/事件/checkpoint/挂起/压缩/产物）；
- 本模块是**宿主边界**：从 TaskExecution 行取 lease/epoch/attempt，调 Kernel，然后把结果翻成
  `interpret_execution` 认识的返回形状，或翻成 `TaskExecutionError` 的失败码。

等待状态不在这里写：Async/Human 挂起只把 checkpoint 与问题放进信封，`WorkerLoop._wait` 在停下
心跳之后一次事务写 checkpoint + `WAITING_*` + 事件 + 释放租约（design §3.5.8）。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from muad_agent_core.agent import (
    RunnerCancelled,
    RunnerDeadlineExceeded,
    RunnerEmptyReply,
    RunnerError,
    RunnerModelError,
    RunnerVerificationExhausted,
    SuspensionKind,
)
from muad_agent_core.skill import SkillExecutionStatus
from muad_contracts import WaitKind

from ..application.agentic.checkpoints import AgenticLeaseLost, ExecutionIdentity, checkpoint_state
from ..application.agentic.errors import AgenticError
from ..application.agentic.model import ModelPoolError
from ..application.agentic.run import (
    TASK_BUDGET_EXCEEDED,
    TASK_NO_PROGRESS,
    AgenticDependencies,
    AgenticOutcome,
    execute_agentic,
    load_resume_state,
)
from ..application.agentic.snapshot import load_frozen_execution
from ..application.agentic.suspension import AgenticSuspension, pending_payload
from ..infrastructure.console_credentials import CredentialResolutionError
from ..infrastructure.models.task import TaskExecution
from .execution_outcomes import (
    AGENTIC_CHECKPOINT_KEY,
    AGENTIC_DEADLINE_KEY,
    AGENTIC_INPUT_SCHEMA_KEY,
    AGENTIC_KEY,
    AGENTIC_KIND_KEY,
    AGENTIC_QUESTION_KEY,
)
from .executor import TaskExecutionError

logger = logging.getLogger("muad_agent_worker.worker.agentic_executor")

AGENTIC_EXECUTION_FAILED = "AGENTIC_EXECUTION_FAILED"
TASK_DEADLINE_EXCEEDED = "TASK_DEADLINE_EXCEEDED"
ASYNC_WAIT_SEC = 60
HUMAN_INPUT_KIND = "HUMAN_INPUT"

SkillRunnerFactory = Callable[[TaskExecution, AgenticSuspension], Any]


class AgenticTaskExecutor:
    """生产装配的 AGENTIC 执行入口（与 REMINDER/SKILL 同一个 `TaskExecutorProtocol`）。"""

    def __init__(
        self,
        *,
        deps: AgenticDependencies,
        skill_runner: SkillRunnerFactory,
    ) -> None:
        self._deps = deps
        self._skill_runner = skill_runner

    async def execute(self, task: TaskExecution) -> dict[str, Any]:
        frozen = load_frozen_execution(task)
        identity = _identity(task)
        resume = None
        if task.last_resume_reason in ("EXTERNAL", "HUMAN"):
            resume = await load_resume_state(self._deps.sessions, task, operations=self._deps.operations)
        try:
            outcome = await execute_agentic(
                self._deps,
                task,
                frozen,
                identity=identity,
                skill_runner_factory=lambda suspension: self._skill_runner(task, suspension),
                resume=resume,
            )
        except RunnerCancelled as exc:
            return _cancelled(exc)
        except RunnerDeadlineExceeded as exc:
            raise TaskExecutionError(TASK_DEADLINE_EXCEEDED, str(exc), retryable=False) from exc
        except (ModelPoolError, CredentialResolutionError) as exc:
            code = getattr(exc, "code", "MODEL_UNAVAILABLE")
            raise TaskExecutionError(code, str(exc), retryable=False) from exc
        except AgenticError as exc:
            raise TaskExecutionError(exc.code, exc.message, retryable=exc.retryable) from exc
        except (RunnerModelError, RunnerVerificationExhausted, RunnerEmptyReply, RunnerError) as exc:
            raise TaskExecutionError(AGENTIC_EXECUTION_FAILED, str(exc)) from exc
        return _envelope(outcome, task)


def _identity(task: TaskExecution) -> ExecutionIdentity:
    if not task.lease_owner:
        raise AgenticLeaseLost("execution has no lease owner")
    return ExecutionIdentity(
        tenant_id=task.tenant_id,
        execution_id=task.id,
        owner=task.lease_owner,
        epoch=task.lease_epoch,
    )


def _cancelled(exc: Exception) -> dict[str, Any]:
    """停止本地执行：终态由 WorkerLoop 的 CAS 裁决（失约时它会拒绝写）。"""
    logger.info("agentic_execution_cancelled", extra={"reason": str(exc)})
    return {
        "status": str(SkillExecutionStatus.CANCELLED),
        "stdout": "",
        "stderr": "",
        "duration_ms": 0,
        "exit_code": 0,
    }


def _envelope(outcome: AgenticOutcome, task: TaskExecution) -> dict[str, Any]:
    boundary = outcome.boundary
    if boundary is not None:
        return _suspension_envelope(outcome, task)
    if outcome.status.value in ("WAITING_INPUT", "WAITING_TOOL"):
        raise TaskExecutionError(
            AGENTIC_EXECUTION_FAILED, "kernel suspended without a persisted boundary", retryable=False
        )
    if outcome.status.value == "BUDGET_EXCEEDED":
        raise TaskExecutionError(TASK_BUDGET_EXCEEDED, "agentic budget exhausted", retryable=False)
    if outcome.status.value == "NO_PROGRESS":
        raise TaskExecutionError(TASK_NO_PROGRESS, "agentic execution made no progress", retryable=False)
    return {
        "status": str(SkillExecutionStatus.SUCCEEDED),
        "result": {
            "text": outcome.final_text,
            "turns": outcome.turns,
            "tool_calls": outcome.tool_calls,
            "usage": {
                "input_tokens": outcome.usage.input_tokens,
                "output_tokens": outcome.usage.output_tokens,
            },
        },
        "stdout": "",
        "stderr": "",
        "duration_ms": 0,
        "exit_code": 0,
    }


def _suspension_envelope(outcome: AgenticOutcome, task: TaskExecution) -> dict[str, Any]:
    boundary = outcome.boundary
    assert boundary is not None
    request = boundary.request
    checkpoint = checkpoint_state(boundary.progress)
    if request.kind is SuspensionKind.TOOL_OPERATION:
        not_before = (datetime.now(UTC) + timedelta(seconds=ASYNC_WAIT_SEC)).isoformat()
        return {
            "status": str(SkillExecutionStatus.SUCCEEDED),
            "result": {
                "text": _intermediate_text(outcome),
                "wait": {
                    "wait_kind": WaitKind.ASYNC_OPERATION.value,
                    "operations": pending_payload(request.pending_tool_calls)["operations"],
                    "not_before": not_before,
                },
            },
            "stdout": "",
            "stderr": "",
            "duration_ms": 0,
            "exit_code": 0,
            AGENTIC_KEY: {
                AGENTIC_KIND_KEY: "TOOL_OPERATION",
                AGENTIC_CHECKPOINT_KEY: checkpoint,
            },
        }
    deadline = request.deadline_at or task.deadline_at
    return {
        "status": str(SkillExecutionStatus.SUCCEEDED),
        "result": {"text": request.prompt},
        "stdout": "",
        "stderr": "",
        "duration_ms": 0,
        "exit_code": 0,
        AGENTIC_KEY: {
            AGENTIC_KIND_KEY: HUMAN_INPUT_KIND,
            AGENTIC_CHECKPOINT_KEY: checkpoint,
            AGENTIC_QUESTION_KEY: request.text_prompt or request.prompt,
            AGENTIC_INPUT_SCHEMA_KEY: dict(request.input_schema),
            AGENTIC_DEADLINE_KEY: deadline.isoformat(),
        },
    }


def _intermediate_text(outcome: AgenticOutcome) -> str:
    text = outcome.final_text.strip()
    return text


__all__ = ["AGENTIC_EXECUTION_FAILED", "AgenticTaskExecutor"]
