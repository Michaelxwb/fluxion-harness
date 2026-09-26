"""概览聚合的只读查询（设计 §3.2.1「查询流（不建表、不建视图）」）。

全部指标在请求时由少量聚合 SQL 计算：不新增表、不建物化视图、不写任何表，也不缓存为
业务快照（Redis 不可用时功能仍可用）。Actor/Agent 名称在同一 SQL 内 LEFT JOIN 批量补齐
（一次往返，不做逐行关联查询，避免 N+1）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# KPI 与列表口径与设计 §3.2.1 的等价式逐项对应。
_KPI_SQL = """
SELECT
  (SELECT count(*) FROM control.agent_definition
    WHERE tenant_id = :tenant_id AND enabled AND NOT is_deleted) AS enabled_agents,
  (SELECT count(*) FROM control.skill
    WHERE tenant_id = :tenant_id AND enabled AND NOT is_deleted) AS enabled_skills,
  (SELECT count(*) FROM task.task_execution
    WHERE tenant_id = :tenant_id AND NOT is_deleted
      AND status IN ('QUEUED', 'RUNNING', 'WAITING')) AS active_tasks,
  (SELECT count(*) FROM task.task_schedule
    WHERE tenant_id = :tenant_id AND NOT is_deleted
      AND status = 'ACTIVE') AS active_schedules
"""

_RECENT_TASKS_SQL = """
SELECT t.id AS task_id,
       t.intent_key,
       t.agent_id,
       a.name AS agent_name,
       t.actor_user_id,
       pu.display_name AS actor_user_name,
       t.status,
       t.trigger_type,
       t.delivery_status,
       t.started_at,
       t.finished_at,
       t.deadline_at,
       t.create_time
  FROM task.task_execution t
  LEFT JOIN control.agent_definition a ON a.id = t.agent_id
  LEFT JOIN control.platform_user pu ON pu.id = t.actor_user_id
 WHERE t.tenant_id = :tenant_id AND NOT t.is_deleted
 ORDER BY t.create_time DESC
 LIMIT :limit
"""

_NEXT_SCHEDULES_SQL = """
SELECT s.id AS schedule_id,
       s.name,
       s.agent_id,
       a.name AS agent_name,
       s.actor_user_id,
       pu.display_name AS actor_user_name,
       s.intent_key,
       s.status,
       s.next_fire_at,
       s.last_fire_at,
       s.timezone
  FROM task.task_schedule s
  LEFT JOIN control.agent_definition a ON a.id = s.agent_id
  LEFT JOIN control.platform_user pu ON pu.id = s.actor_user_id
 WHERE s.tenant_id = :tenant_id AND NOT s.is_deleted
   AND s.status = 'ACTIVE'
   AND s.next_fire_at IS NOT NULL
 ORDER BY s.next_fire_at ASC
 LIMIT :limit
"""


@dataclass(frozen=True)
class OverviewKpis:
    enabled_agents: int
    enabled_skills: int
    active_tasks: int
    active_schedules: int


@dataclass(frozen=True)
class RecentTaskRow:
    task_id: uuid.UUID
    intent_key: str
    agent_id: uuid.UUID
    agent_name: str | None
    actor_user_id: uuid.UUID
    actor_user_name: str | None
    status: str
    trigger_type: str
    delivery_status: str
    started_at: datetime | None
    finished_at: datetime | None
    deadline_at: datetime | None
    create_time: datetime


@dataclass(frozen=True)
class NextScheduleRow:
    schedule_id: uuid.UUID
    name: str
    agent_id: uuid.UUID
    agent_name: str | None
    actor_user_id: uuid.UUID
    actor_user_name: str | None
    intent_key: str
    status: str
    next_fire_at: datetime
    last_fire_at: datetime | None
    timezone: str


class OverviewQueryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def kpis(self, tenant_id: str) -> OverviewKpis:
        row = (await self._session.execute(text(_KPI_SQL), {"tenant_id": tenant_id})).one()
        return OverviewKpis(*[int(value or 0) for value in row])

    async def recent_tasks(self, tenant_id: str, *, limit: int) -> list[RecentTaskRow]:
        rows = await self._session.execute(
            text(_RECENT_TASKS_SQL), {"tenant_id": tenant_id, "limit": limit}
        )
        return [RecentTaskRow(**self._row(mapping)) for mapping in rows.mappings()]

    async def next_schedules(self, tenant_id: str, *, limit: int) -> list[NextScheduleRow]:
        rows = await self._session.execute(
            text(_NEXT_SCHEDULES_SQL), {"tenant_id": tenant_id, "limit": limit}
        )
        return [NextScheduleRow(**self._row(mapping)) for mapping in rows.mappings()]

    @staticmethod
    def _row(mapping: Any) -> dict[str, Any]:
        return dict(mapping)
