"""持久排队阶段与两入口状态投影（design §3.2 架构与执行边界 / §3.4 接口设计；B-236）。

Runtime 是阶段事实的唯一 Owner：

- Run 行上的排队投影（`queue_stage`/`queue_reason`/`queued_at`/`queue_deadline_at`）是**持久事实**；
  `run.stage` canonical 事件与行投影由**同一投影函数**产生（同一词表、同一时间口径），
  Console（API-15 `active_run.execution_status`）与 Gateway（API-07 SSE）两个入口因此一致；
- 阶段文案不是助手正文，也不进入下一轮模型历史（`RUN_STAGE` 不在 `HISTORY_EVENT_TYPES`）；
- 只投影已持久化的时间事实：排队期限是受理/等待时冻结的 `queue_deadline_at`，不计算预计时间、
  不输出名次/位置等臆造字段（契约 `WebchatExecutionStatus` 只有 stage/reason/queued_at/
  deadline_at/started_at）。
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from muad_contracts import RunStatus
from muad_contracts.webchat import ExecutionStage, WebchatExecutionStatus

from ..infrastructure.db import SessionFactoryProvider
from ..infrastructure.models.runtime import RunRecord
from .run_events import EventWriter

STAGE_WAITING_RUNTIME: ExecutionStage = "WAITING_RUNTIME"
STAGE_WAITING_MODEL: ExecutionStage = "WAITING_MODEL"
STAGE_RUNNING: ExecutionStage = "RUNNING"
STAGE_WAITING_TOOL: ExecutionStage = "WAITING_TOOL"
STAGE_WAITING_INPUT: ExecutionStage = "WAITING_INPUT"
STAGE_COMPLETED: ExecutionStage = "COMPLETED"
STAGE_FAILED: ExecutionStage = "FAILED"
STAGE_CANCELLED: ExecutionStage = "CANCELLED"
#: 需要 queued_at/deadline_at 的排队阶段（与契约 `WebchatExecutionStatus._queue_deadline` 同源）。
QUEUE_STAGES: tuple[ExecutionStage, ...] = (STAGE_WAITING_RUNTIME, STAGE_WAITING_MODEL)
#: Run 状态 → 阶段：排队投影为空时的映射；排队投影非空时以 Run 行上的持久投影为准。
STAGE_BY_STATUS: dict[RunStatus, ExecutionStage] = {
    RunStatus.CREATED: STAGE_WAITING_RUNTIME,
    RunStatus.RUNNING: STAGE_RUNNING,
    RunStatus.WAITING_TOOL: STAGE_WAITING_TOOL,
    RunStatus.WAITING_INPUT: STAGE_WAITING_INPUT,
    RunStatus.COMPLETED: STAGE_COMPLETED,
    RunStatus.FAILED: STAGE_FAILED,
    RunStatus.CANCELLED: STAGE_CANCELLED,
}
#: `runtime.run_record.queue_reason` 列宽（varchar(64)）：超长/非稳定码形状的原因不进公开投影。
QUEUE_REASON_MAX_LENGTH = 64


def normalized_queue_reason(raw: object) -> str | None:
    """排队原因规范化：只接受稳定错误码形状（`[A-Z][A-Z0-9_]*`，≤64 字符）。

    原因消费 `MODEL_CAPACITY_UNAVAILABLE` 的 `data.reason`（`NO_CAPACITY`/`WORKLOAD_SHARE`）：
    它是公开状态词表的一部分，不是上游原文——非码形状的输入（可能夹带内部细节）不投影。
    """
    if not isinstance(raw, str) or not 0 < len(raw) <= QUEUE_REASON_MAX_LENGTH:
        return None
    if not raw.isascii() or not raw[0].isupper():
        return None
    if not all(char.isupper() or char.isdigit() or char == "_" for char in raw):
        return None
    return raw


def project_execution_status(run: RunRecord) -> WebchatExecutionStatus:
    """Run 行的持久事实 → 对外阶段投影（两个入口共用的唯一投影函数）。

    排队阶段（WAITING_RUNTIME/WAITING_MODEL）带冻结的 `queued_at`/`deadline_at`；其余阶段
    带 Run 的 `start_time`（受理/开始时间），不带任何排队窗口字段。
    """
    stage = _waiting_stage(run) or STAGE_BY_STATUS[RunStatus(run.status)]
    if stage in QUEUE_STAGES:
        queued_at, deadline_at = _queue_window(run)
        return WebchatExecutionStatus(
            stage=stage,
            reason=normalized_queue_reason(run.queue_reason),
            queued_at=queued_at,
            deadline_at=deadline_at,
        )
    return WebchatExecutionStatus(stage=stage, started_at=run.start_time)


def _waiting_stage(run: RunRecord) -> ExecutionStage | None:
    """排队投影列 → 排队阶段（表级 CHECK 只允许两值）；未知值显式失败，不静默当运行中。"""
    if run.queue_stage is None:
        return None
    if run.queue_stage not in QUEUE_STAGES:
        raise ValueError(f"run {run.id} has an unknown queue stage")
    return run.queue_stage


def _queue_window(run: RunRecord) -> tuple[datetime, datetime]:
    """排队窗口：优先取受理/等待时冻结的排队列；缺失（迁移前旧行）回落受理与绝对期限。

    两者都是**已持久化的事实**，不做任何估算；连事实都不足时显式失败，不编造预计时间。
    """
    queued_at = run.queued_at or run.start_time
    deadline_at = run.queue_deadline_at or run.deadline_at
    if queued_at is None or deadline_at is None or deadline_at <= queued_at:
        raise ValueError(f"run {run.id} has no valid queue window to project")
    return queued_at, deadline_at


async def persist_model_wait(
    session_factory: SessionFactoryProvider,
    *,
    tenant_id: str,
    run_id: uuid.UUID,
    lease_owner: str,
    reason: str | None,
    queued_at: datetime,
    deadline_at: datetime,
) -> bool:
    """把「等待模型资源」落到 Run 行与 `run.stage` 事件（**同一事务**）。

    CAS 栅栏：只有仍由本执行片段持有（`RUNNING` + `lease_owner` 匹配 + 未请求取消）的 Run 才可
    改写；相同阶段+原因的重放是幂等的（不重复发事件）。返回是否真的写入。
    """
    normalized = normalized_queue_reason(reason)
    if deadline_at <= queued_at:
        raise ValueError("model wait deadline must follow the wait start")
    async with session_factory()() as session, session.begin():
        row = (
            await session.execute(
                sa.update(RunRecord)
                .where(
                    RunRecord.id == run_id,
                    RunRecord.tenant_id == tenant_id,
                    RunRecord.is_deleted.is_(False),
                    RunRecord.status == RunStatus.RUNNING,
                    RunRecord.lease_owner == lease_owner,
                    RunRecord.cancel_requested.is_(False),
                    sa.or_(
                        RunRecord.queue_stage.is_distinct_from(STAGE_WAITING_MODEL),
                        RunRecord.queue_reason.is_distinct_from(normalized),
                    ),
                )
                .values(
                    queue_stage=STAGE_WAITING_MODEL,
                    queue_reason=normalized,
                    queued_at=queued_at,
                    queue_deadline_at=deadline_at,
                    update_time=sa.func.now(),
                )
                .returning(RunRecord.id, RunRecord.conversation_id)
            )
        ).one_or_none()
        if row is None:
            return False
        await EventWriter(session).append_run_stage(
            tenant_id=tenant_id,
            conversation_id=row.conversation_id,
            run_id=run_id,
            status=WebchatExecutionStatus(
                stage=STAGE_WAITING_MODEL,
                reason=normalized,
                queued_at=queued_at,
                deadline_at=deadline_at,
            ),
        )
        return True


async def persist_model_running(
    session_factory: SessionFactoryProvider,
    *,
    tenant_id: str,
    run_id: uuid.UUID,
    lease_owner: str,
) -> bool:
    """模型额度恢复：清排队投影并写 `RUNNING` 阶段事件（同一事务；只对等待中的行生效）。"""
    async with session_factory()() as session, session.begin():
        row = (
            await session.execute(
                sa.update(RunRecord)
                .where(
                    RunRecord.id == run_id,
                    RunRecord.tenant_id == tenant_id,
                    RunRecord.is_deleted.is_(False),
                    RunRecord.status == RunStatus.RUNNING,
                    RunRecord.lease_owner == lease_owner,
                    RunRecord.cancel_requested.is_(False),
                    RunRecord.queue_stage == STAGE_WAITING_MODEL,
                )
                .values(queue_stage=None, queue_reason=None, update_time=sa.func.now())
                .returning(RunRecord.id, RunRecord.conversation_id, RunRecord.start_time)
            )
        ).one_or_none()
        if row is None:
            return False
        await EventWriter(session).append_run_stage(
            tenant_id=tenant_id,
            conversation_id=row.conversation_id,
            run_id=run_id,
            status=WebchatExecutionStatus(stage=STAGE_RUNNING, started_at=row.start_time),
        )
        return True
