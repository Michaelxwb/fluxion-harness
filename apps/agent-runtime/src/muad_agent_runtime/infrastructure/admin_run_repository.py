"""Admin Run 列表/详情的参数化 SQL 读写（API-03/API-04）。

名称由权威表补齐：`run_record.agent_id → control.agent_definition.name`、
`run_record.user_id → control.platform_user.display_name`（运行/审计表不冗余名称，
同一 SQL 内 JOIN，不做逐行关联查询）。SELECT 只列契约字段——Snapshot 的
`agent_json/model_json`、`run_record.input_text/error_message` 等含密钥或原始 Prompt
的列一律不进投影。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# 列表投影：与设计 §3.4 API-03 items 字段一一对应
_LIST_COLUMNS = """
SELECT r.id AS run_id,
       r.conversation_id,
       r.agent_id,
       ad.name AS agent_name,
       r.user_id,
       pu.display_name AS user_name,
       r.status,
       r.trace_id,
       r.start_time,
       r.end_time,
       r.error_code
"""

_RUN_COLUMNS = """
SELECT r.id AS run_id,
       r.conversation_id,
       r.agent_id,
       ad.name AS agent_name,
       r.user_id,
       pu.display_name AS user_name,
       r.status,
       r.trace_id,
       r.cancel_requested,
       r.error_code,
       r.start_time,
       r.end_time
"""

_RUN_FROM = """
  FROM runtime.run_record r
  LEFT JOIN control.agent_definition ad ON ad.id = r.agent_id AND ad.is_deleted = false
  LEFT JOIN control.platform_user pu ON pu.id = r.user_id AND pu.is_deleted = false
"""

# start_time 可空（CREATED）：DESC 时 NULL 排最后，再按 id 兜底保证分页稳定
_ORDER_BY = "\n ORDER BY r.start_time DESC NULLS LAST, r.id DESC"

_SNAPSHOT_SQL = text(
    """
    SELECT id AS snapshot_id, schema_version, agent_revision, model_revision,
           prompt_template_version, content_hash
      FROM runtime.runtime_snapshot
     WHERE tenant_id = :tenant_id AND run_id = :run_id AND is_deleted = false
     LIMIT 1
    """
)

_TIMELINE_SQL = text(
    """
    SELECT seq, event_type, payload_json, create_time
      FROM runtime.canonical_event
     WHERE tenant_id = :tenant_id AND run_id = :run_id AND is_deleted = false
     ORDER BY seq
    """
)

_TOOL_AUDIT_SQL = text(
    """
    SELECT id AS audit_id, tool_name, tool_kind, status, latency_ms, error_code
      FROM runtime.tool_call_audit
     WHERE tenant_id = :tenant_id AND run_id = :run_id AND is_deleted = false
     ORDER BY create_time, id
    """
)

_EGRESS_AUDIT_SQL = text(
    """
    SELECT id AS audit_id, target_type, target, operation, policy_decision,
           result_status, latency_ms, error_code
      FROM runtime.egress_audit
     WHERE tenant_id = :tenant_id AND run_id = :run_id AND is_deleted = false
     ORDER BY create_time, id
    """
)

_MODEL_AUDIT_SQL = text(
    """
    SELECT id AS audit_id, provider, model, attempt, status,
           input_tokens, output_tokens, latency_ms, error_code
      FROM runtime.model_invocation_audit
     WHERE tenant_id = :tenant_id AND run_id = :run_id AND is_deleted = false
     ORDER BY attempt, create_time, id
    """
)

_ARTIFACT_SQL = text(
    """
    SELECT id AS artifact_id, artifact_type, media_type, size, checksum,
           preview_text, create_time
      FROM runtime.artifact
     WHERE tenant_id = :tenant_id AND run_id = :run_id AND is_deleted = false
     ORDER BY create_time, id
    """
)


@dataclass(frozen=True)
class AdminRunFilters:
    """API-03 的可选筛选条件；`skill_id` 无归属列，经 Snapshot 的 Skill Catalog 关联。"""

    agent_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    skill_id: uuid.UUID | None = None
    status: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None


def _scope(
    tenant_id: str,
    filters: AdminRunFilters,
    *,
    run_id: uuid.UUID | None = None,
) -> tuple[str, dict[str, Any]]:
    """租户 + 可选索引列过滤的 WHERE 片段（全部命名参数，无入参拼接）。"""
    clauses = ["r.tenant_id = :tenant_id", "r.is_deleted = false"]
    params: dict[str, Any] = {"tenant_id": tenant_id}
    optional: tuple[tuple[str, str, Any], ...] = (
        ("r.agent_id = :agent_id", "agent_id", filters.agent_id),
        ("r.user_id = :user_id", "user_id", filters.user_id),
        ("r.status = :status", "status", filters.status),
        ("r.start_time >= :start_time", "start_time", filters.start_time),
        ("r.start_time <= :end_time", "end_time", filters.end_time),
        ("r.id = :run_id", "run_id", run_id),
    )
    for clause, name, value in optional:
        if value is not None:
            clauses.append(clause)
            params[name] = value
    if filters.skill_id is not None:
        clauses.append(
            "EXISTS (SELECT 1 FROM runtime.runtime_snapshot s"
            " JOIN LATERAL jsonb_array_elements(s.skill_catalog_json) sk ON true"
            " WHERE s.run_id = r.id AND s.is_deleted = false"
            " AND sk->>'skill_id' = :skill_id)"
        )
        params["skill_id"] = str(filters.skill_id)
    return "\n   AND ".join(clauses), params


async def _mappings(
    session: AsyncSession, sql: Any, params: dict[str, Any]
) -> list[dict[str, Any]]:
    rows = await session.execute(sql, params)
    return [dict(row) for row in rows.mappings()]


class AdminRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_runs(
        self,
        tenant_id: str,
        filters: AdminRunFilters,
        *,
        page: int,
        page_size: int,
    ) -> list[dict[str, Any]]:
        scope, params = _scope(tenant_id, filters)
        sql = text(
            f"{_LIST_COLUMNS}{_RUN_FROM} WHERE {scope}{_ORDER_BY} LIMIT :limit OFFSET :offset"
        )
        return await _mappings(
            self._session,
            sql,
            {**params, "limit": page_size, "offset": (page - 1) * page_size},
        )

    async def count_runs(self, tenant_id: str, filters: AdminRunFilters) -> int:
        scope, params = _scope(tenant_id, filters)
        total = await self._session.scalar(
            text(f"SELECT count(*){_RUN_FROM} WHERE {scope}"), params
        )
        return int(total or 0)

    async def get_run(self, tenant_id: str, run_id: uuid.UUID) -> dict[str, Any] | None:
        scope, params = _scope(tenant_id, AdminRunFilters(), run_id=run_id)
        rows = await _mappings(
            self._session, text(f"{_RUN_COLUMNS}{_RUN_FROM} WHERE {scope}"), params
        )
        return rows[0] if rows else None

    async def get_snapshot(self, tenant_id: str, run_id: uuid.UUID) -> dict[str, Any] | None:
        rows = await _mappings(
            self._session, _SNAPSHOT_SQL, {"tenant_id": tenant_id, "run_id": run_id}
        )
        return rows[0] if rows else None

    async def _by_run(
        self, sql: Any, tenant_id: str, run_id: uuid.UUID
    ) -> list[dict[str, Any]]:
        return await _mappings(self._session, sql, {"tenant_id": tenant_id, "run_id": run_id})

    async def list_timeline(self, tenant_id: str, run_id: uuid.UUID) -> list[dict[str, Any]]:
        return await self._by_run(_TIMELINE_SQL, tenant_id, run_id)

    async def list_tool_audits(self, tenant_id: str, run_id: uuid.UUID) -> list[dict[str, Any]]:
        return await self._by_run(_TOOL_AUDIT_SQL, tenant_id, run_id)

    async def list_egress_audits(self, tenant_id: str, run_id: uuid.UUID) -> list[dict[str, Any]]:
        return await self._by_run(_EGRESS_AUDIT_SQL, tenant_id, run_id)

    async def list_model_invocations(
        self, tenant_id: str, run_id: uuid.UUID
    ) -> list[dict[str, Any]]:
        return await self._by_run(_MODEL_AUDIT_SQL, tenant_id, run_id)

    async def list_artifacts(self, tenant_id: str, run_id: uuid.UUID) -> list[dict[str, Any]]:
        return await self._by_run(_ARTIFACT_SQL, tenant_id, run_id)
