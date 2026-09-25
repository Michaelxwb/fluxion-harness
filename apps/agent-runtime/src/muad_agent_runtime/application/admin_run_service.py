"""Admin Run 列表/详情服务（API-03/API-04）。

只做投影整形与缺失降级：Snapshot/Timeline/Audit/Artifact 全部按 `run_id` 批量取回后整形，
不逐行关联；缺失的 Snapshot/Artifact 渲染为 null/空数组，不整体失败。
响应字段与设计 §3.3「Admin Run 详情响应」严格一致，不含 Secret/原始 Prompt。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.admin_run_repository import AdminRunFilters, AdminRunRepository

_SNAPSHOT_FIELDS = (
    "snapshot_id",
    "schema_version",
    "agent_revision",
    "model_revision",
    "prompt_template_version",
    "content_hash",
)


def _isoformat(value: datetime | None) -> str | None:
    """内部端点时间出参为 RFC3339（Console 的展示格式只作用于 Console 输出）。"""
    return value.isoformat() if value is not None else None


def _text_or_none(value: Any) -> str | None:
    return None if value is None else str(value)


def _run_summary(row: Mapping[str, Any]) -> dict[str, Any]:
    """API-03 item：11 个摘要字段，名称取自权威表 JOIN。"""
    return {
        "run_id": _text_or_none(row["run_id"]),
        "conversation_id": _text_or_none(row["conversation_id"]),
        "agent_id": _text_or_none(row["agent_id"]),
        "agent_name": row["agent_name"],
        "user_id": _text_or_none(row["user_id"]),
        "user_name": row["user_name"],
        "status": row["status"],
        "trace_id": row["trace_id"],
        "start_time": _isoformat(row["start_time"]),
        "end_time": _isoformat(row["end_time"]),
        "error_code": row["error_code"],
    }


def _run_detail(row: Mapping[str, Any]) -> dict[str, Any]:
    detail = _run_summary(row)
    detail["cancel_requested"] = bool(row["cancel_requested"])
    return detail


def _snapshot_section(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    section = {field: row[field] for field in _SNAPSHOT_FIELDS}
    section["snapshot_id"] = _text_or_none(row["snapshot_id"])
    return section


def _timeline_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "seq": row["seq"],
        "event_type": row["event_type"],
        "payload": row["payload_json"],
        "create_time": _isoformat(row["create_time"]),
    }


def _tool_audit_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "audit_id": _text_or_none(row["audit_id"]),
        "tool_name": row["tool_name"],
        "tool_kind": row["tool_kind"],
        "status": row["status"],
        "latency_ms": row["latency_ms"],
        "error_code": row["error_code"],
    }


def _egress_audit_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "audit_id": _text_or_none(row["audit_id"]),
        "target_type": row["target_type"],
        "target": row["target"],
        "operation": row["operation"],
        "policy_decision": row["policy_decision"],
        "result_status": row["result_status"],
        "latency_ms": row["latency_ms"],
        "error_code": row["error_code"],
    }


def _model_invocation_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "audit_id": _text_or_none(row["audit_id"]),
        "provider": row["provider"],
        "model": row["model"],
        "attempt": row["attempt"],
        "status": row["status"],
        "input_tokens": row["input_tokens"],
        "output_tokens": row["output_tokens"],
        "latency_ms": row["latency_ms"],
        "error_code": row["error_code"],
    }


def _artifact_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "artifact_id": _text_or_none(row["artifact_id"]),
        "artifact_type": row["artifact_type"],
        "media_type": row["media_type"],
        "size": row["size"],
        "checksum": row["checksum"],
        "preview": row["preview_text"],
        "create_time": _isoformat(row["create_time"]),
    }


def _rows(
    rows: Sequence[Mapping[str, Any]],
    shaper: Callable[[Mapping[str, Any]], dict[str, Any]],
) -> list[dict[str, Any]]:
    return [shaper(row) for row in rows]


class AdminRunService:
    def __init__(self, session: AsyncSession) -> None:
        self._repository = AdminRunRepository(session)

    async def list_runs(
        self,
        tenant_id: str,
        filters: AdminRunFilters,
        *,
        page: int,
        page_size: int,
    ) -> tuple[list[dict[str, Any]], int]:
        total = await self._repository.count_runs(tenant_id, filters)
        rows = await self._repository.list_runs(
            tenant_id, filters, page=page, page_size=page_size
        )
        return [_run_summary(row) for row in rows], total

    async def get_run_detail(self, tenant_id: str, run_id: uuid.UUID) -> dict[str, Any]:
        run = await self._repository.get_run(tenant_id, run_id)
        if run is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        return {"run": _run_detail(run), **await self._sections(tenant_id, run_id)}

    async def _sections(self, tenant_id: str, run_id: uuid.UUID) -> dict[str, Any]:
        return {
            "snapshot": _snapshot_section(
                await self._repository.get_snapshot(tenant_id, run_id)
            ),
            "timeline": _rows(await self._repository.list_timeline(tenant_id, run_id), _timeline_row),
            "tool_audits": _rows(
                await self._repository.list_tool_audits(tenant_id, run_id), _tool_audit_row
            ),
            "egress_audits": _rows(
                await self._repository.list_egress_audits(tenant_id, run_id), _egress_audit_row
            ),
            "model_invocations": _rows(
                await self._repository.list_model_invocations(tenant_id, run_id),
                _model_invocation_row,
            ),
            "artifacts": _rows(
                await self._repository.list_artifacts(tenant_id, run_id), _artifact_row
            ),
        }
