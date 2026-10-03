"""Run（`runtime.run_record`）的只读聚合投影（承载 §11.6「跨页定位」里的 Run 关联）。

跨 Owner Schema 只读聚合，遵 `harness-data.md`：① 只读、不写任何表；② 每条查询带
`tenant_id` 与 `is_deleted = false`；③ 名称类字段在同一 SQL 内 JOIN 补齐，不做逐行关联。
既有两例是 `audit_query_repository` 与 `overview_query_repository`，本文件是第三例。

**只投影结构，不投影内容**：`run_record.input_text` 与 `canonical_event.payload_json`
都不取。Console 至今**没有暴露过任何对话原文**（审计给的是工具名、参数哈希、模型、耗时），
展示原文是新的隐私姿态，需产品单独定——**先不加易、后撤难**，所以这一版不给。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

#: 流式增量事件：一次运行里按 token 产生，行数可达数千，是「轮廓」要剔掉的那一类。
#: 生产侧定义见 `apps/agent-runtime/src/muad_agent_runtime/application/executor.py`（`MESSAGE_DELTA_EVENT`）。
STREAMING_EVENT_TYPES = ("message.delta",)

#: 轮廓上限。超出只回前 N 条并置 `truncated`，让页面能如实说明「还有更多」，而不是假装完整。
TIMELINE_LIMIT = 200

_DETAIL_SQL = """
SELECT
    r.id            AS run_id,
    r.tenant_id     AS tenant_id,
    r.conversation_id AS conversation_id,
    r.status        AS status,
    r.agent_id      AS agent_id,
    ad.name         AS agent_name,
    r.user_id       AS actor_user_id,
    pu.display_name AS actor_name,
    r.trace_id      AS trace_id,
    r.start_time    AS start_time,
    r.end_time      AS end_time,
    r.error_code    AS error_code,
    r.error_message AS error_message,
    r.create_time   AS create_time,
    r.update_time   AS update_time
  FROM runtime.run_record r
  LEFT JOIN control.agent_definition ad ON ad.id = r.agent_id
  LEFT JOIN control.platform_user pu ON pu.id = r.user_id
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
    error_code: str | None
    error_message: str | None
    create_time: datetime
    update_time: datetime


@dataclass(frozen=True)
class RunEventRow:
    seq: int
    event_type: str
    stream_type: str | None
    has_artifact: bool
    create_time: datetime


class RunQueryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def detail(self, tenant_id: str, run_id: uuid.UUID) -> RunDetailRow | None:
        row = (
            await self._session.execute(
                text(_DETAIL_SQL), {"tenant_id": tenant_id, "run_id": run_id}
            )
        ).mappings().one_or_none()
        return RunDetailRow(**row) if row is not None else None

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
