"""审计聚合查询服务（API-01）。

四表 UNION ALL 的投影由 `AuditQueryRepository` 在查询时完成（不建宽表）；
本层只做参数与枚举校验、时间出参格式化，以及把投影行整形为 API 字段。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.repositories.audit_query_repository import (
    PROJECTED_COLUMNS,
    AuditQueryFilters,
    AuditQueryRepository,
)

# RULE-05：Console 时间出参统一 YYYY-MM-DD HH:mm:ss
CONSOLE_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

AUDIT_TYPES = frozenset({"CONFIG", "TOOL", "EGRESS", "MODEL"})
# 归一后的执行结果域（docs/07 §10.11）：SUCCESS / FAILED / 领域错误码
RESULT_STATUSES = frozenset({"SUCCESS", "FAILED", "DENIED"})

_TIME_FIELDS = ("occurred_at", "started_at", "finished_at")


def format_console_time(value: datetime | None) -> str | None:
    """timestamptz → Console 展示格式；出参依赖 DB 的绝对时刻，不做字符串截断。"""
    if value is None:
        return None
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value
    return aware.astimezone().strftime(CONSOLE_TIME_FORMAT)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class AuditQueryService:
    def __init__(self, session: AsyncSession, registered_codes: frozenset[str]) -> None:
        self._repository = AuditQueryRepository(session)
        self._allowed_result_statuses = RESULT_STATUSES | registered_codes

    async def list_audits(
        self,
        tenant_id: str,
        filters: AuditQueryFilters,
        *,
        page: int,
        page_size: int,
    ) -> tuple[list[dict[str, Any]], int]:
        self._validate(filters)
        total = await self._repository.count(tenant_id, filters)
        rows = await self._repository.page(tenant_id, filters, page, page_size)
        return [self._project(row) for row in rows], total

    def _validate(self, filters: AuditQueryFilters) -> None:
        if filters.audit_type is not None and filters.audit_type not in AUDIT_TYPES:
            raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
        if (
            filters.result_status is not None
            and filters.result_status not in self._allowed_result_statuses
        ):
            raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
        if filters.start_time is not None and filters.end_time is not None:
            if _as_utc(filters.start_time) > _as_utc(filters.end_time):
                raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)

    @staticmethod
    def _project(row: dict[str, Any]) -> dict[str, Any]:
        item = {key: row[key] for key in PROJECTED_COLUMNS}
        for field in _TIME_FIELDS:
            item[field] = format_console_time(row[field])
        for field in ("audit_id", "resource_id", "actor_user_id"):
            item[field] = str(row[field])
        item["agent_id"] = None if row["agent_id"] is None else str(row["agent_id"])
        # 旧版字段别名：Console agent-management 审计页签与既有回归用例仍在消费
        item["id"] = item["audit_id"]
        item["actor_display_name"] = item["actor_name"]
        item["create_time"] = item["occurred_at"]
        return item
