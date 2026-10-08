"""Run 详情（Console 面向的只读视图）。

数据来自 `runtime.run_record` + `runtime.canonical_event`，经 `RunQueryRepository`
做只读聚合投影——**不转调 Runtime**：Runtime 没有对外读接口，而 `harness-data.md`
明确允许 Console 对 `runtime.*` 做只读聚合投影（审计与概览已是两例）。

**投影内容与 `input_text` / 事件 payload / `submission_json` 无关**，理由见仓库模块的 docstring。
API-05/06：等待字段（含 `waiting_reason`，口径在 `muad_contracts.tool_waiting_reason`）与
关联 operations 分页；operations 的 Task 状态来自 phase JOIN，`SUBMIT` 失败不伪造 Task 失败。
"""

from __future__ import annotations

import uuid
from typing import Any

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import RunStatus, tool_waiting_reason
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.repositories.run_query_repository import (
    RunOperationRow,
    RunQueryRepository,
)
from .audit_query_service import format_console_time


def _operation_view(row: RunOperationRow) -> dict[str, Any]:
    """operations 子资源字段白名单：无 input/result/hash/credential。"""
    return {
        "operation_id": str(row.operation_id),
        "source_tool_call_id": row.source_tool_call_id,
        "completion_mode": row.completion_mode,
        "status": row.status,
        "error_phase": row.error_phase,
        "error_code": row.error_code,
        "task_id": str(row.task_id) if row.task_id is not None else None,
        "task_status": row.task_status,
        "submitted_at": format_console_time(row.submitted_at),
        "completed_at": format_console_time(row.completed_at),
    }


class RunQueryService:
    def __init__(self, session: AsyncSession) -> None:
        self._repository = RunQueryRepository(session)

    async def get_run(self, tenant_id: str, run_id: uuid.UUID) -> dict[str, Any]:
        row = await self._repository.detail(tenant_id, run_id)
        if row is None:
            # 跨租户与不存在同码：不泄漏「这个 id 在别的租户里存在」。
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        events, truncated = await self._repository.outline(tenant_id, run_id)
        reason = tool_waiting_reason(
            RunStatus(row.status),
            resume_ready=bool(row.continuation_ready),
            pending_submission_count=row.pending_submission_count,
            pending_join_count=row.pending_join_count,
        )
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
            "deadline_at": format_console_time(row.deadline_at),
            "waiting_since": format_console_time(row.waiting_since),
            "waiting_reason": reason.value if reason is not None else None,
            "pending_join_count": row.pending_join_count,
            "pending_submission_count": row.pending_submission_count,
            "continuation_count": (
                row.continuation_count if row.continuation_count is not None else 0
            ),
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

    async def list_operations(
        self, tenant_id: str, run_id: uuid.UUID, *, page: int, page_size: int
    ) -> tuple[list[dict[str, Any]], int]:
        """关联 operations 一页 + total；Run 不存在或跨租户同码 404（与详情一致）。"""
        if not await self._repository.run_exists(tenant_id, run_id):
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        rows, total = await self._repository.operations(
            tenant_id, run_id, limit=page_size, offset=(page - 1) * page_size
        )
        return [_operation_view(row) for row in rows], total
