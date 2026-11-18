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
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from muad_agent_core.agent import (
    RunnerCancelled,
    RunnerDeadlineExceeded,
    RunnerEmptyReply,
    RunnerError,
    RunnerModelError,
    RunnerVerificationExhausted,
    SuspensionKind,
)
from muad_agent_core.model import ModelMessage, ModelRole
from muad_agent_core.skill import SkillExecutionStatus
from muad_api.error_codes import ErrorCode
from muad_contracts import WaitKind
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..application.agentic.checkpoints import AgenticLeaseLost, ExecutionIdentity, checkpoint_state
from ..application.agentic.errors import AgenticError
from ..application.agentic.model import ModelPoolError
from ..application.agentic.run import (
    TASK_BUDGET_EXCEEDED,
    TASK_NO_PROGRESS,
    AgenticDependencies,
    AgenticOutcome,
    ResumeState,
    execute_agentic,
    load_resume_state,
)
from ..application.agentic.snapshot import load_frozen_execution
from ..application.agentic.suspension import AgenticSuspension, pending_payload
from ..application.human_interactions import human_answer_content, load_human_answer
from ..infrastructure.console_credentials import CredentialResolutionError
from ..infrastructure.models.task import TaskExecution
from ..infrastructure.task_operation_client import TaskOperationClient, TaskOperationError
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
#: `last_resume_reason` 的 Human 取值（claim 写入；与 answer 事务同一口径）。
HUMAN_RESUME_REASON = "HUMAN"
#: `last_resume_reason` 的外部等待取值（claim 写入）。
EXTERNAL_RESUME_REASON = "EXTERNAL"
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

    @property
    def operations(self) -> TaskOperationClient | None:
        """Task-owned 结果收件箱客户端：等待前的就绪检查与周期补扫共用同一个装配实例。"""
        return self._deps.operations

    async def execute(self, task: TaskExecution) -> dict[str, Any]:
        frozen = load_frozen_execution(task)
        identity = _identity(task)
        try:
            # 恢复的唯一依据是 checkpoint：`load_resume_state` 在没有 checkpoint 时返回 None。
            # 首跑、外部续接、Human 续接、崩后重试都走同一条读历史的路，不靠 resume_reason 猜。
            resume = await load_resume_state(
                self._deps.sessions,
                task,
                operations=self._deps.operations,
                session_context=self._deps.session_context,
            )
            # Human 续接的答案不是持久事件：恢复后按 resolved_at 注入成本轮的一条 USER 消息。
            if resume is not None and task.last_resume_reason == HUMAN_RESUME_REASON:
                resume = await _with_human_answer(self._deps.sessions, task, resume)
            if resume is not None and resume.pending:
                # 崩后重建：还有调用没终态，历史里那些 tool 消息只是提交回执。补写等待，
                # 不跑模型（否则会得到"声明了 tool_call 却没有结果"的非法回合）。
                return _pending_envelope(resume)
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
        except TaskOperationError as exc:
            # 同一调用身份换了参数（`TOOL_OPERATION_INPUT_MISMATCH`）是确定性冲突：重试不会变好。
            raise TaskExecutionError(
                exc.code, str(exc), retryable=exc.code != ErrorCode.TOOL_OPERATION_INPUT_MISMATCH.value
            ) from exc
        except (RunnerModelError, RunnerVerificationExhausted, RunnerEmptyReply, RunnerError) as exc:
            raise TaskExecutionError(AGENTIC_EXECUTION_FAILED, str(exc)) from exc
        return _envelope(outcome, task)


async def _with_human_answer(
    sessions: async_sessionmaker[AsyncSession], task: TaskExecution, resume: ResumeState
) -> ResumeState:
    """Human 续接：把这一次的回答按用户消息接在 checkpoint 历史之后。

    没有可注入的回答（回答之后没有新 checkpoint、或已被消费）就保持原恢复状态并显式告警：
    恢复仍从 checkpoint 继续，不臆造回答内容。
    """
    answer = await load_human_answer(sessions, task)
    if answer is None:
        logger.warning("human_resume_without_answer", extra={"execution_id": str(task.id)})
        return resume
    return replace(
        resume,
        messages=(
            *resume.messages,
            ModelMessage(role=ModelRole.USER, content=human_answer_content(answer)),
        ),
    )


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
        detail = ""
        if outcome.last_failure:
            detail = (
                f" (last failure: {outcome.last_failure.get('tool')} / "
                f"{outcome.last_failure.get('code')}: {outcome.last_failure.get('message')})"
            )
        raise TaskExecutionError(
            TASK_NO_PROGRESS, f"agentic execution made no progress{detail}", retryable=False
        )
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
            **(
                {"result_status": outcome.result_status}
                if outcome.result_status is not None
                else {}
            ),
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
        return _async_wait_envelope(
            text=_intermediate_text(outcome),
            operations=cast(
                list[dict[str, Any]], pending_payload(request.pending_tool_calls)["operations"]
            ),
            checkpoint=checkpoint,
        )
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


def _pending_envelope(resume: ResumeState) -> dict[str, Any]:
    """崩后重建：还原出来的调用还没终态时补写等待（等待状态仍只由 WorkerLoop 写）。"""
    checkpoint = replace(resume.checkpoint, pending_tool_calls=resume.pending)
    return _async_wait_envelope(
        text="",
        operations=cast(list[dict[str, Any]], pending_payload(resume.pending)["operations"]),
        checkpoint=checkpoint_state(checkpoint),
    )


def _async_wait_envelope(
    *, text: str, operations: list[dict[str, Any]], checkpoint: Mapping[str, Any]
) -> dict[str, Any]:
    """工具级挂起的执行信封：一份形状，Kernel 收口与崩后重建共用。"""
    not_before = (datetime.now(UTC) + timedelta(seconds=ASYNC_WAIT_SEC)).isoformat()
    return {
        "status": str(SkillExecutionStatus.SUCCEEDED),
        "result": {
            "text": text,
            "wait": {
                "wait_kind": WaitKind.ASYNC_OPERATION.value,
                "operations": operations,
                "not_before": not_before,
            },
        },
        "stdout": "",
        "stderr": "",
        "duration_ms": 0,
        "exit_code": 0,
        AGENTIC_KEY: {
            AGENTIC_KIND_KEY: "TOOL_OPERATION",
            AGENTIC_CHECKPOINT_KEY: dict(checkpoint),
        },
    }


def _intermediate_text(outcome: AgenticOutcome) -> str:
    text = outcome.final_text.strip()
    return text


__all__ = ["AGENTIC_EXECUTION_FAILED", "AgenticTaskExecutor"]
