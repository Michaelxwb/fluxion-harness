"""Run（`runtime.run_record`）的只读聚合投影（承载 §11.6「跨页定位」里的 Run 关联）。

跨 Owner Schema 只读聚合，遵 `harness-data.md`：① 只读、不写任何表；② 每条查询带
`tenant_id` 与 `is_deleted = false`；③ 名称类字段在同一 SQL 内 JOIN 补齐，不做逐行关联。
既有两例是 `audit_query_repository` 与 `overview_query_repository`，本文件是第三例。

**只投影结构，不投影内容**：`run_record.input_text`、`canonical_event.payload_json` 与
`tool_operation.submission_json` 都不取。Console 至今**没有暴露过任何对话原文**（审计给的是
工具名、参数哈希、模型、耗时），展示原文是新的隐私姿态，需产品单独定——**先不加易、后撤难**。

API-05/06 新增的等待信息与关联操作同样只投影**状态/计数/时间**：operations 子资源以一次
LEFT JOIN 批量补 Task 状态（不逐行回查），分页与 count 共用同一条件片段。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from muad_contracts import CompletionMode, OperationStatus, RunStatus
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

#: 不进「轮廓」的事件类型：**落库后的业务名**（`canonical_event.event_type`，不是 SSE 名——
#: 生产侧由 `STREAM_BUSINESS_TYPES` 映射，见
#: `apps/agent-runtime/src/muad_agent_runtime/application/run_service.py`）。
#: 只剔 token 级增量（一次运行可达数千行，占满上限后轮廓就只剩它）。
#: 模型调用边界**保留**：与 `TOOL_CALL_STARTED`/`TOOL_CALL` 对称，是审计要看的执行事实。
STREAMING_EVENT_TYPES = ("ASSISTANT_DELTA",)

#: 轮廓上限。超出只回前 N 条并置 `truncated`，让页面能如实说明「还有更多」，而不是假装完整。
TIMELINE_LIMIT = 200

#: 记录 Run 进入 WAITING_TOOL 的 canonical 事件（等待起点）；每次等待代都会追加一条。
WAITING_TOOL_EVENT_TYPE = "RUN_WAITING_TOOL"
#: `run_interrupt` 的等待态（WAITING_INPUT 的等待起点）。
INTERRUPT_WAITING_STATUS = "WAITING"

#: 「未终态 JOIN」判定：与 Runtime `tool_result_receipts.OP_TERMINAL` 同口径（含 MATERIALIZED，
#: 它仍在等待模型消费）。终态 operation 不再构成等待依赖。
TERMINAL_OPERATION_STATUSES = (
    OperationStatus.COMPLETED,
    OperationStatus.FAILED,
    OperationStatus.CANCELLED,
    OperationStatus.LATE,
)

_DETAIL_SQL = """
SELECT
    r.id              AS run_id,
    r.tenant_id       AS tenant_id,
    r.conversation_id AS conversation_id,
    r.status          AS status,
    r.agent_id        AS agent_id,
    ad.name           AS agent_name,
    r.user_id         AS actor_user_id,
    pu.display_name   AS actor_name,
    r.trace_id        AS trace_id,
    r.start_time      AS start_time,
    r.end_time        AS end_time,
    r.deadline_at     AS deadline_at,
    r.error_code      AS error_code,
    r.error_message   AS error_message,
    r.create_time     AS create_time,
    r.update_time     AS update_time,
    (
        SELECT count(*)
          FROM runtime.tool_operation o
         WHERE o.tenant_id = r.tenant_id
           AND o.run_id = r.id
           AND o.is_deleted = false
           AND o.completion_mode = :join_mode
           AND o.status <> ALL(:terminal_operation_statuses)
    ) AS pending_join_count,
    (
        SELECT count(*)
          FROM runtime.tool_operation o
         WHERE o.tenant_id = r.tenant_id
           AND o.run_id = r.id
           AND o.is_deleted = false
           AND o.status = :submit_pending
    ) AS pending_submission_count,
    c.wait_generation AS continuation_count,
    c.ready           AS continuation_ready,
    CASE
        WHEN r.status = :waiting_tool THEN (
            SELECT max(e.create_time)
              FROM runtime.canonical_event e
             WHERE e.tenant_id = r.tenant_id
               AND e.run_id = r.id
               AND e.is_deleted = false
               AND e.event_type = :waiting_tool_event
        )
        WHEN r.status = :waiting_input THEN (
            SELECT max(i.create_time)
              FROM runtime.run_interrupt i
             WHERE i.tenant_id = r.tenant_id
               AND i.run_id = r.id
               AND i.is_deleted = false
               AND i.status = :interrupt_waiting
        )
        ELSE NULL
    END AS waiting_since
  FROM runtime.run_record r
  LEFT JOIN control.agent_definition ad ON ad.id = r.agent_id
  LEFT JOIN control.platform_user pu ON pu.id = r.user_id
  LEFT JOIN runtime.run_continuation c
    ON c.run_id = r.id
   AND c.tenant_id = r.tenant_id
   AND c.is_deleted = false
 WHERE r.tenant_id = :tenant_id
   AND r.id = :run_id
   AND r.is_deleted = false
"""

_TIMELINE_SQL = """
SELECT
    seq,
    event_type,
    stream_type,
    create_time,
    artifact_id IS NOT NULL AS has_artifact
  FROM runtime.canonical_event
 WHERE tenant_id = :tenant_id
   AND run_id = :run_id
   AND is_deleted = false
   AND event_type <> ALL(:streaming_event_types)
 ORDER BY seq
 LIMIT :limit
"""

#: operations 列表与 count 的**共用**条件片段（RULE：分页与 total 不得漂移）。
_OPERATION_SCOPE = """
    o.tenant_id = :tenant_id
    AND o.run_id = :run_id
    AND o.is_deleted = false
"""

_OPERATIONS_SQL = f"""
SELECT
    o.id                  AS operation_id,
    o.source_tool_call_id AS source_tool_call_id,
    o.completion_mode     AS completion_mode,
    o.status              AS status,
    o.error_phase         AS error_phase,
    o.error_code          AS error_code,
    o.task_id             AS task_id,
    t.status              AS task_status,
    o.submitted_at        AS submitted_at,
    o.completed_at        AS completed_at
  FROM runtime.tool_operation o
  LEFT JOIN task.task_execution t
    ON t.id = o.task_id
   AND t.tenant_id = o.tenant_id
   AND t.is_deleted = false
 WHERE {_OPERATION_SCOPE}
 ORDER BY o.create_time, o.id
 LIMIT :limit OFFSET :offset
"""

_OPERATIONS_COUNT_SQL = f"""
SELECT count(*)
  FROM runtime.tool_operation o
 WHERE {_OPERATION_SCOPE}
"""

_RUN_EXISTS_SQL = """
SELECT 1
  FROM runtime.run_record
 WHERE tenant_id = :tenant_id
   AND id = :run_id
   AND is_deleted = false
 LIMIT 1
"""


@dataclass(frozen=True)
class RunDetailRow:
    run_id: uuid.UUID
    tenant_id: str
    conversation_id: uuid.UUID
    status: str
    agent_id: uuid.UUID
    agent_name: str | None
    actor_user_id: uuid.UUID
    actor_name: str | None
    trace_id: str
    start_time: datetime | None
    end_time: datetime | None
    deadline_at: datetime | None
    error_code: str | None
    error_message: str | None
    create_time: datetime
    update_time: datetime
    pending_join_count: int
    pending_submission_count: int
    continuation_count: int | None
    continuation_ready: bool | None
    waiting_since: datetime | None


@dataclass(frozen=True)
class RunEventRow:
    seq: int
    event_type: str
    stream_type: str | None
    has_artifact: bool
    create_time: datetime


@dataclass(frozen=True)
class RunOperationRow:
    operation_id: uuid.UUID
    source_tool_call_id: str
    completion_mode: str
    status: str
    error_phase: str | None
    error_code: str | None
    task_id: uuid.UUID | None
    task_status: str | None
    submitted_at: datetime | None
    completed_at: datetime | None


class RunQueryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def detail(self, tenant_id: str, run_id: uuid.UUID) -> RunDetailRow | None:
        row = (
            await self._session.execute(
                text(_DETAIL_SQL),
                {
                    "tenant_id": tenant_id,
                    "run_id": run_id,
                    "join_mode": CompletionMode.JOIN.value,
                    "terminal_operation_statuses": [
                        status.value for status in TERMINAL_OPERATION_STATUSES
                    ],
                    "submit_pending": OperationStatus.SUBMIT_PENDING.value,
                    "waiting_tool": RunStatus.WAITING_TOOL.value,
                    "waiting_input": RunStatus.WAITING_INPUT.value,
                    "waiting_tool_event": WAITING_TOOL_EVENT_TYPE,
                    "interrupt_waiting": INTERRUPT_WAITING_STATUS,
                },
            )
        ).mappings().one_or_none()
        return RunDetailRow(**row) if row is not None else None

    async def run_exists(self, tenant_id: str, run_id: uuid.UUID) -> bool:
        found = await self._session.scalar(
            text(_RUN_EXISTS_SQL), {"tenant_id": tenant_id, "run_id": run_id}
        )
        return found is not None

    async def outline(self, tenant_id: str, run_id: uuid.UUID) -> tuple[list[RunEventRow], bool]:
        """返回 `(事件轮廓, 是否被截断)`；多取一条只为判断「还有更多」，不外传。"""
        rows = (
            await self._session.execute(
                text(_TIMELINE_SQL),
                {
                    "tenant_id": tenant_id,
                    "run_id": run_id,
                    "streaming_event_types": list(STREAMING_EVENT_TYPES),
                    "limit": TIMELINE_LIMIT + 1,
                },
            )
        ).mappings().all()
        truncated = len(rows) > TIMELINE_LIMIT
        return [RunEventRow(**row) for row in rows[:TIMELINE_LIMIT]], truncated

    async def operations(
        self, tenant_id: str, run_id: uuid.UUID, *, limit: int, offset: int
    ) -> tuple[list[RunOperationRow], int]:
        """一页操作 + 同条件的 total；Task 状态由同一条 SQL 的 LEFT JOIN 补齐。"""
        params = {"tenant_id": tenant_id, "run_id": run_id}
        rows = (
            await self._session.execute(
                text(_OPERATIONS_SQL), {**params, "limit": limit, "offset": offset}
            )
        ).mappings().all()
        total = await self._session.scalar(text(_OPERATIONS_COUNT_SQL), params)
        return [RunOperationRow(**row) for row in rows], int(total or 0)
