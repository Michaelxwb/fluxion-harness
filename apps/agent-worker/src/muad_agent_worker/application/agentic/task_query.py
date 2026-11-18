"""Scheduled-run task-query tools: bounded, owner-scoped read-only facts.

The scheduled agent may need to inspect earlier runs ("汇总昨天的定时任务执行"). The
query surface is deliberately small and safe: tenant + creator + agent scope, no raw
prompt/tool arguments, only the task's public summary fields.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ...infrastructure.models.task import TaskExecution

MAX_LIST_TASKS = 20
SUMMARY_KEYS = ("summary", "text", "message", "answer")


def task_summary(row: TaskExecution) -> dict[str, Any]:
    """One task row → safe bounded summary; raw prompts/results never leave the worker."""
    result = row.result_json if isinstance(row.result_json, dict) else {}
    summary = next(
        (
            value.strip()
            for key in SUMMARY_KEYS
            if isinstance((value := result.get(key)), str) and value.strip()
        ),
        None,
    )
    return {
        "task_id": str(row.id),
        "task_type": row.task_type,
        "trigger_type": row.trigger_type,
        "status": row.status,
        "schedule_id": str(row.schedule_id) if row.schedule_id else None,
        "scheduled_at": row.scheduled_at.isoformat() if row.scheduled_at else None,
        "create_time": row.create_time.isoformat() if row.create_time else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "error_code": row.error_code,
        "error_message": row.error_message,
        "summary": summary[:1000] if summary else None,
    }


class DbTaskQuery:
    """Read-only task queries bound to the scheduled execution's trusted identity."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        tenant_id: str,
        actor_user_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        self._sessions = sessions
        self._tenant_id = tenant_id
        self._actor_user_id = actor_user_id
        self._agent_id = agent_id

    async def list_tasks(self, *, limit: int) -> list[dict[str, Any]]:
        async with self._sessions() as session:
            rows = await session.scalars(
                select(TaskExecution)
                .where(
                    TaskExecution.tenant_id == self._tenant_id,
                    TaskExecution.actor_user_id == self._actor_user_id,
                    TaskExecution.agent_id == self._agent_id,
                    TaskExecution.is_deleted.is_(False),
                )
                .order_by(TaskExecution.create_time.desc(), TaskExecution.id)
                .limit(limit)
            )
            return [task_summary(row) for row in rows]

    async def get_task(self, task_id: uuid.UUID) -> dict[str, Any] | None:
        async with self._sessions() as session:
            row = await session.scalar(
                select(TaskExecution).where(
                    TaskExecution.id == task_id,
                    TaskExecution.tenant_id == self._tenant_id,
                    TaskExecution.actor_user_id == self._actor_user_id,
                    TaskExecution.agent_id == self._agent_id,
                    TaskExecution.is_deleted.is_(False),
                )
            )
            return task_summary(row) if row is not None else None


__all__ = ["DbTaskQuery", "MAX_LIST_TASKS", "task_summary"]
