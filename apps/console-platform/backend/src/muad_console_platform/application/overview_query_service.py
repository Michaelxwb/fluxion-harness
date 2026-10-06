"""概览聚合服务：单请求内组装 4 个 KPI 与两组列表（设计 §3.3 冻结契约）。

只读：不写任何表、不建快照、不依赖 Redis；某模块无数据时对应 KPI=0/列表为空，
不把整个概览判错（E-01）。时间出参复用 Console 统一格式（RULE-time-001）。

`get_metrics` 提供指标图数据（任务趋势/状态分布）：日历日按**平台默认时区**（locale
设置）分桶，与页面展示时区一致；趋势窗口内无数据的日子补 0，保证前端拿到连续序列。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.repositories.overview_query_repository import (
    NextScheduleRow,
    OverviewQueryRepository,
    RecentTaskRow,
)
from .audit_query_service import format_console_time
from .platform_settings_service import PlatformSettingsService

RECENT_TASK_LIMIT = 5
NEXT_SCHEDULE_LIMIT = 5
METRIC_TZ_FALLBACK = "UTC"


def today_in_tz(tz: str) -> date:
    """平台默认时区下的「今天」（仅用于趋势窗口的日期序列）。"""
    return datetime.now(ZoneInfo(tz)).date()


class OverviewQueryService:
    def __init__(self, session: AsyncSession, *, tenant_id: str) -> None:
        self._session = session
        self._repository = OverviewQueryRepository(session)
        self._tenant_id = tenant_id

    async def get_overview(self) -> dict[str, Any]:
        """≤3 条聚合 SQL：KPI 一条、两组列表各一条（名称在同 SQL 内 JOIN 补齐）。"""
        kpis = await self._repository.kpis(self._tenant_id)
        tasks = await self._repository.recent_tasks(self._tenant_id, limit=RECENT_TASK_LIMIT)
        schedules = await self._repository.next_schedules(
            self._tenant_id, limit=NEXT_SCHEDULE_LIMIT
        )
        return {
            "kpis": {
                "enabled_agents": kpis.enabled_agents,
                "enabled_skills": kpis.enabled_skills,
                "active_tasks": kpis.active_tasks,
                "active_schedules": kpis.active_schedules,
            },
            "recent_tasks": [self._task_payload(row) for row in tasks],
            "next_schedules": [self._schedule_payload(row) for row in schedules],
        }

    async def get_metrics(self, *, days: int) -> dict[str, Any]:
        """指标图聚合（平台设置读取 + 2 条 SQL）：趋势按日补零为连续序列。"""
        tz = await self._display_timezone()
        trend_rows = await self._repository.task_trend(self._tenant_id, days=days, tz=tz)
        by_day = {row.day: row for row in trend_rows}
        today = today_in_tz(tz)
        trend = []
        for offset in range(days - 1, -1, -1):
            day = (today - timedelta(days=offset)).isoformat()
            row = by_day.get(day)
            trend.append(
                {
                    "date": day,
                    "total": row.total if row else 0,
                    "failed": row.failed if row else 0,
                }
            )
        status_rows = await self._repository.task_status_counts(self._tenant_id)
        return {
            "days": days,
            "timezone": tz,
            "task_trend": trend,
            "task_status": {row.status: row.total for row in status_rows},
        }

    async def _display_timezone(self) -> str:
        """平台默认时区（locale 设置）；非法值不炸指标——降级 UTC。"""
        snapshot = await PlatformSettingsService(self._session).read_current(self._tenant_id)
        candidate = snapshot.settings.locale.default_timezone
        try:
            ZoneInfo(candidate)
        except Exception:
            return METRIC_TZ_FALLBACK
        return candidate

    @staticmethod
    def _task_payload(row: RecentTaskRow) -> dict[str, Any]:
        return {
            "task_id": str(row.task_id),
            "intent_key": row.intent_key,
            "agent_id": str(row.agent_id),
            "agent_name": row.agent_name,
            "actor_user_id": str(row.actor_user_id),
            "actor_user_name": row.actor_user_name,
            "status": row.status,
            "trigger_type": row.trigger_type,
            "delivery_status": row.delivery_status,
            "started_at": format_console_time(row.started_at),
            "finished_at": format_console_time(row.finished_at),
            "deadline_at": format_console_time(row.deadline_at),
            "create_time": format_console_time(row.create_time),
        }

    @staticmethod
    def _schedule_payload(row: NextScheduleRow) -> dict[str, Any]:
        return {
            "schedule_id": str(row.schedule_id),
            "name": row.name,
            "agent_id": str(row.agent_id),
            "agent_name": row.agent_name,
            "actor_user_id": str(row.actor_user_id),
            "actor_user_name": row.actor_user_name,
            "intent_key": row.intent_key,
            "status": row.status,
            "next_fire_at": format_console_time(row.next_fire_at),
            "last_fire_at": format_console_time(row.last_fire_at),
            "timezone": row.timezone,
        }
