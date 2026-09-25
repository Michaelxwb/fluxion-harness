"""四张审计表的查询期聚合投影（设计 §3.3「审计聚合投影」）。

不建宽表、不做物化视图：查询时把 `control.config_audit_log` 与
`runtime.{tool_call_audit,egress_audit,model_invocation_audit}` UNION ALL 到同一字段集，
Actor/Agent 名称在同一 SQL 内 JOIN 补齐（一次往返，不做逐行关联查询）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# 投影字段集：与设计 §3.3「审计聚合投影」的统一字段一一对应。
PROJECTED_COLUMNS = (
    "audit_id",
    "audit_type",
    "resource_type",
    "resource_id",
    "actor_user_id",
    "actor_name",
    "agent_id",
    "agent_name",
    "action",
    "result_status",
    "trace_id",
    "target",
    "occurred_at",
    "started_at",
    "finished_at",
    "latency_ms",
)

# 每列位置与 PROJECTED_COLUMNS 严格一一对应：只有第一条 SELECT 起名，其余按位置对齐。
# TOOL/EGRESS/MODEL 三表不冗余 trace_id：先按 task_id 取 task_event，否则回落 run_record。
_PROJECTION_SQL = """
SELECT
    c.id AS audit_id,
    'CONFIG' AS audit_type,
    c.resource_type AS resource_type,
    c.resource_id::text AS resource_id,
    c.actor_user_id AS actor_user_id,
    acct.display_name AS actor_name,
    NULL::uuid AS agent_id,
    NULL::text AS agent_name,
    c.action AS action,
    'SUCCESS' AS result_status,
    c.trace_id AS trace_id,
    c.resource_type || '/' || c.resource_id::text AS target,
    c.create_time AS occurred_at,
    c.create_time AS started_at,
    NULL::timestamptz AS finished_at,
    NULL::bigint AS latency_ms
  FROM control.config_audit_log c
  LEFT JOIN control.console_account acct ON acct.id = c.actor_user_id
 WHERE c.tenant_id = :tenant_id
   AND c.is_deleted = false

UNION ALL

SELECT
    t.id,
    'TOOL',
    'TOOL',
    COALESCE(t.run_id, t.task_id)::text,
    COALESCE(r.user_id, tx.actor_user_id),
    pu.display_name,
    COALESCE(r.agent_id, tx.agent_id),
    ad.name,
    t.tool_name,
    CASE
        WHEN t.status IN ('OK', 'SUCCEEDED', 'SUCCESS') THEN 'SUCCESS'
        WHEN t.status IN ('ERROR', 'FAILED') THEN 'FAILED'
        ELSE t.status
    END,
    COALESCE(r.trace_id, ev.trace_id),
    t.tool_name,
    t.create_time,
    t.start_time,
    t.end_time,
    t.latency_ms
  FROM runtime.tool_call_audit t
  LEFT JOIN runtime.run_record r ON r.id = t.run_id
  LEFT JOIN task.task_execution tx ON tx.id = t.task_id
  LEFT JOIN LATERAL (
      SELECT e.trace_id
        FROM task.task_event e
       WHERE e.task_id = t.task_id
         AND e.trace_id IS NOT NULL
       ORDER BY e.seq
       LIMIT 1
  ) ev ON true
  LEFT JOIN control.platform_user pu ON pu.id = COALESCE(r.user_id, tx.actor_user_id)
  LEFT JOIN control.agent_definition ad ON ad.id = COALESCE(r.agent_id, tx.agent_id)
 WHERE t.tenant_id = :tenant_id
   AND t.is_deleted = false

UNION ALL

SELECT
    g.id,
    'EGRESS',
    g.target_type,
    COALESCE(g.platform_id::text, g.target),
    g.user_id,
    pu.display_name,
    COALESCE(r.agent_id, tx.agent_id),
    ad.name,
    COALESCE(NULLIF(g.operation, ''), g.target),
    CASE
        WHEN g.result_status IN ('OK', 'SUCCEEDED', 'SUCCESS') THEN 'SUCCESS'
        WHEN g.result_status IN ('ERROR', 'FAILED') THEN 'FAILED'
        ELSE g.result_status
    END,
    COALESCE(r.trace_id, ev.trace_id),
    g.target,
    g.create_time,
    g.create_time,
    NULL::timestamptz,
    g.latency_ms
  FROM runtime.egress_audit g
  LEFT JOIN runtime.run_record r ON r.id = g.run_id
  LEFT JOIN task.task_execution tx ON tx.id = g.task_id
  LEFT JOIN LATERAL (
      SELECT e.trace_id
        FROM task.task_event e
       WHERE e.task_id = g.task_id
         AND e.trace_id IS NOT NULL
       ORDER BY e.seq
       LIMIT 1
  ) ev ON true
  LEFT JOIN control.platform_user pu ON pu.id = g.user_id
  LEFT JOIN control.agent_definition ad ON ad.id = COALESCE(r.agent_id, tx.agent_id)
 WHERE g.tenant_id = :tenant_id
   AND g.is_deleted = false

UNION ALL

SELECT
    m.id,
    'MODEL',
    'MODEL',
    COALESCE(m.run_id, m.task_id)::text,
    COALESCE(r.user_id, tx.actor_user_id),
    pu.display_name,
    COALESCE(r.agent_id, tx.agent_id),
    ad.name,
    m.provider || '/' || m.model,
    CASE
        WHEN m.status IN ('OK', 'SUCCEEDED', 'SUCCESS') THEN 'SUCCESS'
        WHEN m.status IN ('ERROR', 'FAILED') THEN 'FAILED'
        ELSE m.status
    END,
    COALESCE(r.trace_id, ev.trace_id),
    m.provider || '/' || m.model,
    m.create_time,
    m.create_time,
    NULL::timestamptz,
    m.latency_ms
  FROM runtime.model_invocation_audit m
  LEFT JOIN runtime.run_record r ON r.id = m.run_id
  LEFT JOIN task.task_execution tx ON tx.id = m.task_id
  LEFT JOIN LATERAL (
      SELECT e.trace_id
        FROM task.task_event e
       WHERE e.task_id = m.task_id
         AND e.trace_id IS NOT NULL
       ORDER BY e.seq
       LIMIT 1
  ) ev ON true
  LEFT JOIN control.platform_user pu ON pu.id = COALESCE(r.user_id, tx.actor_user_id)
  LEFT JOIN control.agent_definition ad ON ad.id = COALESCE(r.agent_id, tx.agent_id)
 WHERE m.tenant_id = :tenant_id
   AND m.is_deleted = false
"""


# 详情按 `audit_type` 定位来源表：4 张表的 UUID 不互通，来源表必须显式指定，不猜表。
# 每类只取该来源表独有字段 + 原始 run_id/task_id（关联可读性判定用，config 无关联）。
_DETAIL_EXTRAS_SQL = {
    "CONFIG": """
        SELECT NULL::uuid AS run_id, NULL::uuid AS task_id,
               before_json, after_json, source_ip
          FROM control.config_audit_log
         WHERE id = :audit_id
           AND tenant_id = :tenant_id
           AND is_deleted = false
    """,
    "TOOL": """
        SELECT run_id, task_id, tool_call_id, tool_kind, prepared_args_hash,
               args_preview_json, error_code
          FROM runtime.tool_call_audit
         WHERE id = :audit_id
           AND tenant_id = :tenant_id
           AND is_deleted = false
    """,
    "EGRESS": """
        SELECT run_id, task_id, target_type, adapter_key, platform_id, method,
               policy_decision, status_code, error_code
          FROM runtime.egress_audit
         WHERE id = :audit_id
           AND tenant_id = :tenant_id
           AND is_deleted = false
    """,
    "MODEL": """
        SELECT run_id, task_id, provider, model, attempt, retry_reason,
               input_tokens, output_tokens, error_code
          FROM runtime.model_invocation_audit
         WHERE id = :audit_id
           AND tenant_id = :tenant_id
           AND is_deleted = false
    """,
}

# 关联可读性：目标行存在且同租户且未软删。表名取自本字面量，不入参。
_RELATION_SQL = {
    "run_id": """
        SELECT id FROM runtime.run_record
         WHERE id = :relation_id
           AND tenant_id = :tenant_id
           AND is_deleted = false
    """,
    "task_id": """
        SELECT id FROM task.task_execution
         WHERE id = :relation_id
           AND tenant_id = :tenant_id
           AND is_deleted = false
    """,
}

RELATION_KEYS = ("run_id", "task_id")


@dataclass(frozen=True)
class AuditDetailRecord:
    """详情查询结果：统一投影行 + 来源表独有字段 + 可读的关联与 missing 标记。"""

    audit_type: str
    row: dict[str, Any]
    extras: dict[str, Any]
    related: dict[str, str]
    related_missing: bool


@dataclass(frozen=True)
class AuditQueryFilters:
    """API-01 的筛选条件；未设置即不过滤。`keyword` 为旧版兼容筛选。"""

    audit_type: str | None = None
    resource_type: str | None = None
    resource_id: uuid.UUID | None = None
    actor_user_id: uuid.UUID | None = None
    action: str | None = None
    result_status: str | None = None
    trace_id: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    keyword: str | None = None


def _text(value: uuid.UUID | None) -> str | None:
    return None if value is None else str(value)


def _where(filters: AuditQueryFilters) -> tuple[str, dict[str, Any]]:
    """按投影列拼装 WHERE；列名与操作符全部来自本函数字面量，值一律走绑定参数。"""
    clauses: list[str] = []
    params: dict[str, Any] = {}
    exact = {
        "audit_type": filters.audit_type,
        "resource_type": filters.resource_type,
        "resource_id": _text(filters.resource_id),
        "actor_user_id": _text(filters.actor_user_id),
        "action": filters.action,
        "result_status": filters.result_status,
        "trace_id": filters.trace_id,
    }
    for column, value in exact.items():
        if value is not None:
            clauses.append(f"{column} = :{column}")
            params[column] = value
    if filters.start_time is not None:
        clauses.append("occurred_at >= :start_time")
        params["start_time"] = filters.start_time
    if filters.end_time is not None:
        clauses.append("occurred_at <= :end_time")
        params["end_time"] = filters.end_time
    if filters.keyword:
        clauses.append(
            "(action ILIKE :keyword OR resource_type ILIKE :keyword"
            " OR trace_id ILIKE :keyword)"
        )
        params["keyword"] = f"%{filters.keyword}%"
    if not clauses:
        return "", params
    return " WHERE " + " AND ".join(clauses), params


class AuditQueryRepository:
    """只读聚合读取；count 与分页各一次往返，行数不产生额外查询。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def count(self, tenant_id: str, filters: AuditQueryFilters) -> int:
        where, params = _where(filters)
        total = await self._session.scalar(
            text(f"SELECT count(*) FROM ({_PROJECTION_SQL}) u{where}"),
            {"tenant_id": tenant_id, **params},
        )
        return int(total or 0)

    async def page(
        self,
        tenant_id: str,
        filters: AuditQueryFilters,
        page: int,
        page_size: int,
    ) -> list[dict[str, Any]]:
        where, params = _where(filters)
        columns = ", ".join(PROJECTED_COLUMNS)
        rows = await self._session.execute(
            text(
                f"SELECT {columns} FROM ({_PROJECTION_SQL}) u{where} "
                "ORDER BY occurred_at DESC, audit_id DESC "
                "OFFSET :offset LIMIT :limit"
            ),
            {
                "tenant_id": tenant_id,
                **params,
                "offset": (page - 1) * page_size,
                "limit": page_size,
            },
        )
        return [dict(row) for row in rows.mappings()]

    async def all_rows(
        self, tenant_id: str, filters: AuditQueryFilters
    ) -> list[dict[str, Any]]:
        """API-06 导出执行：同一四表 UNION ALL 投影按筛选条件取全量行（行序同列表）。"""
        where, params = _where(filters)
        columns = ", ".join(PROJECTED_COLUMNS)
        rows = await self._session.execute(
            text(
                f"SELECT {columns} FROM ({_PROJECTION_SQL}) u{where} "
                "ORDER BY occurred_at DESC, audit_id DESC"
            ),
            {"tenant_id": tenant_id, **params},
        )
        return [dict(row) for row in rows.mappings()]

    async def detail(
        self, tenant_id: str, audit_id: uuid.UUID, audit_type: str
    ) -> AuditDetailRecord | None:
        """按 `(tenant_id, audit_id, audit_type)` 单表命中；不存在/已归档返回 None。"""
        row = await self._projected_row(tenant_id, audit_id, audit_type)
        if row is None:
            return None
        extras = await self._extras(tenant_id, audit_id, audit_type)
        related, related_missing = await self._relation_state(tenant_id, extras)
        return AuditDetailRecord(
            audit_type=audit_type,
            row=row,
            extras=extras,
            related=related,
            related_missing=related_missing,
        )

    async def _projected_row(
        self, tenant_id: str, audit_id: uuid.UUID, audit_type: str
    ) -> dict[str, Any] | None:
        """复用 API-01 的聚合投影取单行，保证详情与列表字段口径一致。"""
        columns = ", ".join(f"u.{column}" for column in PROJECTED_COLUMNS)
        rows = await self._session.execute(
            text(
                f"SELECT {columns} FROM ({_PROJECTION_SQL}) u "
                "WHERE u.audit_id = :audit_id AND u.audit_type = :audit_type"
            ),
            {"tenant_id": tenant_id, "audit_id": audit_id, "audit_type": audit_type},
        )
        row = rows.mappings().first()
        return None if row is None else dict(row)

    async def _extras(
        self, tenant_id: str, audit_id: uuid.UUID, audit_type: str
    ) -> dict[str, Any]:
        rows = await self._session.execute(
            text(_DETAIL_EXTRAS_SQL[audit_type]),
            {"tenant_id": tenant_id, "audit_id": audit_id},
        )
        row = rows.mappings().first()
        return {} if row is None else dict(row)

    async def _relation_state(
        self, tenant_id: str, extras: dict[str, Any]
    ) -> tuple[dict[str, str], bool]:
        """E-01：关联只在真实可读时进入 `related`；不可读即置空并标记 missing。"""
        related: dict[str, str] = {}
        related_missing = False
        for key in RELATION_KEYS:
            relation_id = extras.get(key)
            if relation_id is None:
                continue
            if await self._relation_readable(tenant_id, key, relation_id):
                related[key] = str(relation_id)
            else:
                related_missing = True
        return related, related_missing

    async def _relation_readable(
        self, tenant_id: str, key: str, relation_id: uuid.UUID
    ) -> bool:
        found = await self._session.scalar(
            text(_RELATION_SQL[key]),
            {"tenant_id": tenant_id, "relation_id": relation_id},
        )
        return found is not None
