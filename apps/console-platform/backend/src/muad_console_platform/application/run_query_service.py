"""Run 详情（Console 面向的只读视图）。

数据来自 `runtime.run_record` + `runtime.canonical_event`，经 `RunQueryRepository`
做只读聚合投影——**不转调 Runtime**：Runtime 没有对外读接口，而 `harness-data.md`
明确允许 Console 对 `runtime.*` 做只读投影（审计与概览已是两例）。

**投影内容与 `input_text` / 事件 payload 无关**，理由见仓库模块的模块 docstring。
"""

from __future__ import annotations

import uuid
from typing import Any

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.repositories.run_query_repository import RunQueryRepository
from .audit_query_service import format_console_time


class RunQueryService:
    def __init__(self, session: AsyncSession) -> None:
        self._repository = RunQueryRepository(session)

    async def get_run(self, tenant_id: str, run_id: uuid.UUID) -> dict[str, Any]:
        row = await self._repository.detail(tenant_id, run_id)
        if row is None:
            # 跨租户与不存在同码：不泄漏「这个 id 在别的租户里存在」。
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        events, truncated = await self._repository.outline(tenant_id, run_id)
        return {
            "run_id": str(row.run_id),
            "conversation_id": str(row.conversation_id),
            "status": row.status,
            "agent_id": str(row.agent_id),
            "agent_name": row.agent_name,
            "actor_user_id": str(row.actor_user_id),
            "actor_name": row.actor_name,
            "trace_id": row.trace_id,
            "start_time": format_console_time(row.start_time),
            "end_time": format_console_time(row.end_time),
            "error_code": row.error_code,
            "error_message": row.error_message,
            "create_time": format_console_time(row.create_time),
            "update_time": format_console_time(row.update_time),
            "timeline": [
                {
                    "seq": event.seq,
                    "event_type": event.event_type,
                    "stream_type": event.stream_type,
                    "has_artifact": event.has_artifact,
                    "create_time": format_console_time(event.create_time),
                }
                for event in events
            ],
            "timeline_truncated": truncated,
        }
