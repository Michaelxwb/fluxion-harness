"""Console 经验管理只读投影（SELECT-only over runtime schema）。"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

EXPERIENCE_CATEGORIES = "('PROCEDURE','FAILURE_PATTERN','VERIFICATION')"
CAP = 100_000

_ITEM_COLUMNS = """
    m.user_id::text AS user_id,
    m.memory_key,
    m.version,
    m.category,
    m.activation_state,
    m.authority,
    m.task_signature,
    m.trigger_text,
    m.content_json AS content,
    m.author,
    m.enabled,
    m.expires_at,
    m.update_time,
    m.evidence_json
"""

_LATEST_REPORT = """
    LEFT JOIN LATERAL (
        SELECT r.version, r.decision_json, r.metrics_json, r.report_json,
               r.base_config_hash, r.experience_user_id, r.create_time
          FROM runtime.eval_report r
         WHERE r.tenant_id = m.tenant_id
           AND r.task_family = m.task_signature
           AND r.experience_key = m.memory_key
           AND r.experience_user_id = m.user_id
           AND r.is_deleted = false
         ORDER BY r.version DESC
         LIMIT 1
    ) latest ON true
"""

_UNKNOWN_REPORT = """
    EXISTS (
        SELECT 1 FROM runtime.eval_report r
         WHERE r.tenant_id = m.tenant_id
           AND r.task_family = m.task_signature
           AND r.experience_key = m.memory_key
           AND r.experience_user_id IS NULL
           AND r.is_deleted = false
    ) AS has_unknown_report
"""


def _source(family: str) -> str:
    if family.startswith("skill:"):
        return "skill"
    if family.startswith("agent:"):
        return "agent"
    return "unknown"


def _report(row: dict[str, Any]) -> dict[str, Any] | None:
    if row.get("report_version") is None:
        return None
    decision = row.get("decision_json") or {}
    return {
        "version": row["report_version"],
        "status": decision.get("status", "UNKNOWN"),
        "improvement": decision.get("improvement"),
        "experience_user_id": (
            str(row["report_user_id"]) if row.get("report_user_id") else None
        ),
        "owner": "OWNED" if row.get("report_user_id") else "UNKNOWN",
        "base_config_hash": row["base_config_hash"],
        "metrics": row.get("metrics_json") or {},
        "decision": decision,
        "report": row.get("report_json") or {},
        "create_time": row["report_create_time"],
    }


def _item(row: dict[str, Any]) -> dict[str, Any]:
    report = _report(row)
    return {
        "user_id": row["user_id"],
        "memory_key": row["memory_key"],
        "version": row["version"],
        "category": row["category"],
        "activation_state": row["activation_state"],
        "authority": row.get("authority") or "AGENT_INFERRED",
        "task_signature": row["task_signature"],
        "trigger_text": row["trigger_text"],
        "content": row["content"],
        "author": row["author"],
        "enabled": row["enabled"],
        "expires_at": row["expires_at"],
        "update_time": row["update_time"],
        "latest_report": report,
        "report_owner": (
            "OWNED" if report else "UNKNOWN" if row.get("has_unknown_report") else "ABSENT"
        ),
    }


class LearningRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_experiences(
        self,
        tenant_id: str,
        user_id: uuid.UUID,
        *,
        state: str | None,
        family: str | None,
        category: str | None,
        query: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[dict[str, Any]], int, dict[str, int]]:
        clauses = [
            "m.tenant_id = :tenant_id",
            "m.user_id = :user_id",
            "m.is_deleted = false",
            f"m.category IN {EXPERIENCE_CATEGORIES}",
        ]
        params: dict[str, Any] = {"tenant_id": tenant_id, "user_id": user_id}
        if state:
            clauses.append("m.activation_state = :state")
            params["state"] = state
        if family:
            clauses.append("m.task_signature = :family")
            params["family"] = family
        if category:
            clauses.append("m.category = :category")
            params["category"] = category
        if query:
            clauses.append(
                "(m.memory_key ILIKE :q OR m.trigger_text ILIKE :q"
                " OR m.content_json->>'value' ILIKE :q)"
            )
            params["q"] = f"%{query}%"
        where = " AND ".join(clauses)
        total = await self._session.scalar(
            text(f"SELECT count(*) FROM runtime.user_memory m WHERE {where}"),
            params,
        )
        rows = (
            await self._session.execute(
                text(
                    f"""
                    SELECT {_ITEM_COLUMNS}, {_UNKNOWN_REPORT},
                           latest.version AS report_version,
                           latest.decision_json, latest.metrics_json, latest.report_json,
                           latest.base_config_hash, latest.experience_user_id AS report_user_id,
                           latest.create_time AS report_create_time
                      FROM runtime.user_memory m
                      {_LATEST_REPORT}
                     WHERE {where}
                     ORDER BY m.update_time DESC, m.id DESC
                     OFFSET :offset LIMIT :limit
                    """
                ),
                {**params, "offset": (page - 1) * page_size, "limit": page_size},
            )
        ).mappings()
        counts = {
            "PROPOSED": 0,
            "TRIAL": 0,
            "ACTIVE": 0,
            "RETIRED": 0,
            "total": int(total or 0),
        }
        grouped = await self._session.execute(
            text(
                "SELECT activation_state, count(*) AS amount FROM runtime.user_memory"
                " WHERE tenant_id=:tenant_id AND user_id=:user_id AND is_deleted=false"
                f" AND category IN {EXPERIENCE_CATEGORIES} GROUP BY activation_state"
            ),
            {"tenant_id": tenant_id, "user_id": user_id},
        )
        for state_name, amount in grouped:
            counts[state_name] = int(amount)
        return [_item(dict(row)) for row in rows], int(total or 0), counts

    async def get_experience(
        self, tenant_id: str, user_id: uuid.UUID, key: str
    ) -> dict[str, Any] | None:
        row = (
            await self._session.execute(
                text(
                    f"""
                    SELECT {_ITEM_COLUMNS}, {_UNKNOWN_REPORT},
                           latest.version AS report_version,
                           latest.decision_json, latest.metrics_json, latest.report_json,
                           latest.base_config_hash, latest.experience_user_id AS report_user_id,
                           latest.create_time AS report_create_time
                      FROM runtime.user_memory m
                      {_LATEST_REPORT}
                     WHERE m.tenant_id=:tenant_id AND m.user_id=:user_id
                       AND m.memory_key=:key AND m.is_deleted=false
                       AND m.category IN {EXPERIENCE_CATEGORIES}
                    """
                ),
                {"tenant_id": tenant_id, "user_id": user_id, "key": key},
            )
        ).mappings().first()
        if row is None:
            return None
        item = _item(dict(row))
        history = (
            await self._session.execute(
                text(
                    "SELECT version, change_reason, actor_type, actor_id, command_id,"
                    " source_run_id::text AS source_run_id, after_json, create_time"
                    " FROM runtime.user_memory_history"
                    " WHERE tenant_id=:tenant_id AND user_id=:user_id AND memory_key=:key"
                    " ORDER BY version DESC LIMIT 50"
                ),
                {"tenant_id": tenant_id, "user_id": user_id, "key": key},
            )
        ).mappings()
        reports = (
            await self._session.execute(
                text(
                    "SELECT version, decision_json, metrics_json, report_json,"
                    " base_config_hash, experience_user_id, create_time"
                    " FROM runtime.eval_report"
                    " WHERE tenant_id=:tenant_id AND experience_key=:key"
                    "   AND (experience_user_id=:user_id OR experience_user_id IS NULL)"
                    "   AND is_deleted=false ORDER BY version DESC LIMIT 50"
                ),
                {"tenant_id": tenant_id, "user_id": user_id, "key": key},
            )
        ).mappings()
        needle = f'[{{"memory_key":"{key}"}}]'
        injections = (
            await self._session.execute(
                text(
                    "SELECT e.run_id::text AS run_id, e.payload_json, e.create_time"
                    " FROM runtime.canonical_event e"
                    " JOIN runtime.run_record r ON r.id=e.run_id"
                    " WHERE e.tenant_id=:tenant_id AND r.user_id=:user_id"
                    "   AND e.event_type='MEMORY_INJECTED' AND e.is_deleted=false"
                    "   AND e.payload_json->'items' @> CAST(:needle AS jsonb)"
                    " ORDER BY e.create_time DESC LIMIT 50"
                ),
                {"tenant_id": tenant_id, "user_id": user_id, "needle": needle},
            )
        ).mappings()
        audit_rows = (
            await self._session.execute(
                text(
                    "SELECT c.action, c.before_json, c.after_json,"
                    " c.actor_user_id::text AS actor_user_id,"
                    " acct.display_name AS operator_name,"
                    " c.after_json->>'operator' AS operator, c.create_time"
                    " FROM control.config_audit_log c"
                    " LEFT JOIN control.console_account acct ON acct.id = c.actor_user_id"
                    " WHERE c.tenant_id=:tenant_id AND c.resource_type='MEMORY_EXPERIENCE'"
                    "   AND (c.after_json->>'experience_key'=:key"
                    "        AND (c.after_json->>'user_id'=:user_text"
                    "             OR c.after_json->>'user_id' IS NULL))"
                    " ORDER BY c.create_time DESC LIMIT 50"
                ),
                {"tenant_id": tenant_id, "user_id": user_id, "key": key, "user_text": str(user_id)},
            )
        ).mappings()
        item.update(
            evidence=row.get("evidence_json") or {},
            history=[dict(entry) for entry in history],
            reports=[
                {
                    "version": entry["version"],
                    "status": (entry["decision_json"] or {}).get("status", "UNKNOWN"),
                    "improvement": (entry["decision_json"] or {}).get("improvement"),
                    "experience_user_id": (
                        str(entry["experience_user_id"]) if entry["experience_user_id"] else None
                    ),
                    "owner": "OWNED" if entry["experience_user_id"] else "UNKNOWN",
                    "base_config_hash": entry["base_config_hash"],
                    "metrics": entry["metrics_json"] or {},
                    "decision": entry["decision_json"] or {},
                    "report": entry["report_json"] or {},
                    "create_time": entry["create_time"],
                }
                for entry in reports
            ],
            injections=[
                {
                    "run_id": entry["run_id"],
                    "score": next(
                        (
                            item.get("score")
                            for item in (entry["payload_json"].get("items") or [])
                            if item.get("memory_key") == key
                        ),
                        None,
                    ),
                    "channel": next(
                        (
                            item.get("channel")
                            for item in (entry["payload_json"].get("items") or [])
                            if item.get("memory_key") == key
                        ),
                        None,
                    ),
                    "selection_hash": entry["payload_json"].get("selection_hash"),
                    "create_time": entry["create_time"],
                }
                for entry in injections
            ],
            audits=[dict(entry) for entry in audit_rows],
        )
        item.pop("evidence_json", None)
        return item

    async def list_families(
        self,
        tenant_id: str,
        *,
        source: str | None,
        query: str | None,
        window_days: int,
        page: int,
        page_size: int,
    ) -> tuple[list[dict[str, Any]], int, int]:
        params: dict[str, Any] = {"tenant_id": tenant_id, "days": window_days}
        family_filter = ""
        if query:
            family_filter = " AND f.family ILIKE :q"
            params["q"] = f"%{query}%"
        source_filter = ""
        if source == "skill":
            source_filter = " AND f.family LIKE 'skill:%'"
        elif source == "agent":
            source_filter = " AND f.family LIKE 'agent:%'"
        elif source == "unknown":
            source_filter = (
                " AND f.family NOT LIKE 'skill:%' AND f.family NOT LIKE 'agent:%'"
            )
        cte = f"""
            WITH fam AS (
                SELECT DISTINCT family FROM runtime.family_run_stat
                 WHERE tenant_id=:tenant_id AND is_deleted=false
                   AND day >= current_date - CAST(:days AS integer)
                UNION
                SELECT DISTINCT task_signature FROM runtime.user_memory
                 WHERE tenant_id=:tenant_id AND is_deleted=false
                   AND task_signature IS NOT NULL
                   AND category IN {EXPERIENCE_CATEGORIES}
            ), picked AS (
                SELECT f.family FROM fam f WHERE 1=1 {family_filter} {source_filter}
                ORDER BY f.family OFFSET :offset LIMIT :limit
            )
        """
        total = await self._session.scalar(
            text(
                f"""
                WITH fam AS (
                    SELECT DISTINCT family FROM runtime.family_run_stat
                     WHERE tenant_id=:tenant_id AND is_deleted=false
                       AND day >= current_date - CAST(:days AS integer)
                    UNION
                    SELECT DISTINCT task_signature FROM runtime.user_memory
                     WHERE tenant_id=:tenant_id AND is_deleted=false
                       AND task_signature IS NOT NULL
                       AND category IN {EXPERIENCE_CATEGORIES}
                )
                SELECT count(*) FROM fam f WHERE 1=1 {family_filter} {source_filter}
                """
            ),
            params,
        )
        rows = (
            await self._session.execute(
                text(
                    cte
                    + """
                    SELECT p.family,
                           COALESCE(run.run_count, 0) AS run_count,
                           run.last_run_at,
                           COALESCE(exp.experience_count, 0) AS experience_count,
                           COALESCE(exp.proposed, 0) AS proposed,
                           COALESCE(exp.trial, 0) AS trial,
                           COALESCE(exp.active, 0) AS active,
                           COALESCE(exp.retired, 0) AS retired,
                           rep.latest_report_at, rep.passed, rep.total, rep.last_decision
                      FROM picked p
                      LEFT JOIN LATERAL (
                          SELECT sum(run_count)::bigint AS run_count, max(last_run_at) AS last_run_at
                            FROM runtime.family_run_stat s
                           WHERE s.tenant_id=:tenant_id AND s.family=p.family
                             AND s.is_deleted=false AND s.day >= current_date - CAST(:days AS integer)
                      ) run ON true
                      LEFT JOIN LATERAL (
                          SELECT count(*) AS experience_count,
                                 count(*) FILTER (WHERE activation_state='PROPOSED') AS proposed,
                                 count(*) FILTER (WHERE activation_state='TRIAL') AS trial,
                                 count(*) FILTER (WHERE activation_state='ACTIVE') AS active,
                                 count(*) FILTER (WHERE activation_state='RETIRED') AS retired
                            FROM runtime.user_memory m
                           WHERE m.tenant_id=:tenant_id AND m.task_signature=p.family
                             AND m.is_deleted=false AND m.category IN """
                    + EXPERIENCE_CATEGORIES
                    + """
                      ) exp ON true
                      LEFT JOIN LATERAL (
                          SELECT max(create_time) AS latest_report_at,
                                 count(*) FILTER (WHERE decision_json->>'status'='PASSED') AS passed,
                                 count(*) AS total,
                                 (array_agg(
                                     decision_json->>'status' ORDER BY version DESC
                                 ))[1] AS last_decision
                            FROM runtime.eval_report r
                           WHERE r.tenant_id=:tenant_id AND r.task_family=p.family
                             AND r.is_deleted=false
                      ) rep ON true
                     ORDER BY p.family
                    """
                ),
                {**params, "offset": (page - 1) * page_size, "limit": page_size},
            )
        ).mappings()
        items = []
        for row in rows:
            run_count = int(row["run_count"] or 0)
            passed, total_reports = int(row["passed"] or 0), int(row["total"] or 0)
            items.append(
                {
                    "family": row["family"],
                    "source": _source(row["family"]),
                    "run_count": min(run_count, CAP),
                    "run_count_capped": run_count > CAP,
                    "last_run_at": row["last_run_at"],
                    "experience_count": int(row["experience_count"] or 0),
                    "states": {
                        "PROPOSED": int(row["proposed"] or 0),
                        "TRIAL": int(row["trial"] or 0),
                        "ACTIVE": int(row["active"] or 0),
                        "RETIRED": int(row["retired"] or 0),
                        "total": int(row["experience_count"] or 0),
                    },
                    "latest_report_at": row["latest_report_at"],
                    "pass_rate": {"passed": passed, "total": total_reports},
                    "last_decision": row["last_decision"],
                }
            )
        unclassified = await self._session.scalar(
            text(
                "SELECT count(*) FROM runtime.run_record"
                " WHERE tenant_id=:tenant_id AND is_deleted=false AND task_family IS NULL"
            ),
            {"tenant_id": tenant_id},
        )
        return items, int(total or 0), int(unclassified or 0)

    async def get_family(
        self, tenant_id: str, family: str, *, window_days: int
    ) -> dict[str, Any]:
        run = (
            await self._session.execute(
                text(
                    "SELECT COALESCE(sum(run_count),0)::bigint AS run_count, max(last_run_at) AS last_run_at"
                    " FROM runtime.family_run_stat"
                    " WHERE tenant_id=:tenant_id AND family=:family AND is_deleted=false"
                    "   AND day >= current_date - CAST(:days AS integer)"
                ),
                {"tenant_id": tenant_id, "family": family, "days": window_days},
            )
        ).mappings().one()
        recent = (
            await self._session.execute(
                text(
                    "SELECT id::text AS id, status, user_id::text AS user_id, agent_id::text AS agent_id,"
                    " end_time FROM runtime.run_record"
                    " WHERE tenant_id=:tenant_id AND task_family=:family AND is_deleted=false"
                    " ORDER BY end_time DESC NULLS LAST LIMIT 20"
                ),
                {"tenant_id": tenant_id, "family": family},
            )
        ).mappings()
        cases = (
            await self._session.execute(
                text(
                    "SELECT id::text AS id, source_run_id::text AS source_run_id, item_count,"
                    " version, create_time FROM runtime.eval_case"
                    " WHERE tenant_id=:tenant_id AND task_family=:family AND is_deleted=false"
                    " ORDER BY create_time DESC LIMIT 20"
                ),
                {"tenant_id": tenant_id, "family": family},
            )
        ).mappings()
        experiences = (
            await self._session.execute(
                text(
                    "SELECT user_id::text AS user_id, memory_key, version, category,"
                    " activation_state, authority, task_signature, trigger_text,"
                    " content_json AS content, author, enabled, expires_at, update_time"
                    " FROM runtime.user_memory"
                    " WHERE tenant_id=:tenant_id AND task_signature=:family AND is_deleted=false"
                    f" AND category IN {EXPERIENCE_CATEGORIES}"
                    " ORDER BY user_id, update_time DESC LIMIT 200"
                ),
                {"tenant_id": tenant_id, "family": family},
            )
        ).mappings()
        reports = (
            await self._session.execute(
                text(
                    "SELECT version, decision_json, metrics_json, report_json,"
                    " base_config_hash, experience_user_id, create_time"
                    " FROM runtime.eval_report"
                    " WHERE tenant_id=:tenant_id AND task_family=:family AND is_deleted=false"
                    " ORDER BY create_time DESC LIMIT 20"
                ),
                {"tenant_id": tenant_id, "family": family},
            )
        ).mappings()
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in experiences:
            grouped.setdefault(row["user_id"], []).append(dict(row))
        run_count = int(run["run_count"] or 0)
        return {
            "family": family,
            "source": _source(family),
            "window": f"{window_days}d",
            "run_count": min(run_count, CAP),
            "run_count_capped": run_count > CAP,
            "recent_runs": [dict(row) for row in recent],
            "cases": [dict(row) for row in cases],
            "experiences": [
                {"user_id": user_id, "items": items} for user_id, items in grouped.items()
            ],
            "reports": [
                {
                    "version": entry["version"],
                    "status": (entry["decision_json"] or {}).get("status", "UNKNOWN"),
                    "improvement": (entry["decision_json"] or {}).get("improvement"),
                    "experience_user_id": (
                        str(entry["experience_user_id"]) if entry["experience_user_id"] else None
                    ),
                    "owner": "OWNED" if entry["experience_user_id"] else "UNKNOWN",
                    "base_config_hash": entry["base_config_hash"],
                    "metrics": entry["metrics_json"] or {},
                    "decision": entry["decision_json"] or {},
                    "report": entry["report_json"] or {},
                    "create_time": entry["create_time"],
                }
                for entry in reports
            ],
            "paused": bool(
                await self._session.scalar(
                    text(
                        "SELECT paused FROM runtime.learning_family_control"
                        " WHERE tenant_id=:tenant_id AND family=:family"
                        "   AND is_deleted=false LIMIT 1"
                    ),
                    {"tenant_id": tenant_id, "family": family},
                )
            ),
        }


    async def run_diagnostics(self, tenant_id: str, run_id: uuid.UUID) -> dict[str, Any] | None:
        run = (
            await self._session.execute(
                text(
                    "SELECT id::text AS id, task_family, user_id::text AS user_id, snapshot_id"
                    " FROM runtime.run_record"
                    " WHERE tenant_id=:tenant_id AND id=:run_id AND is_deleted=false"
                ),
                {"tenant_id": tenant_id, "run_id": run_id},
            )
        ).mappings().first()
        if run is None:
            return None
        event = (
            await self._session.execute(
                text(
                    "SELECT payload_json, create_time FROM runtime.canonical_event"
                    " WHERE tenant_id=:tenant_id AND run_id=:run_id"
                    "   AND event_type='MEMORY_INJECTED' AND is_deleted=false"
                    " ORDER BY seq DESC LIMIT 1"
                ),
                {"tenant_id": tenant_id, "run_id": run_id},
            )
        ).mappings().first()
        learning: dict[str, Any] = {}
        if run["snapshot_id"] is not None:
            policy = await self._session.scalar(
                text("SELECT policy_json FROM runtime.runtime_snapshot WHERE id=:sid"),
                {"sid": run["snapshot_id"]},
            )
            if isinstance(policy, dict):
                learning = policy.get("learning") or {}
        payload = dict(event["payload_json"]) if event is not None else {}
        diagnostics_value = payload.get("diagnostics")
        diagnostics: dict[str, Any] | None = (
            diagnostics_value if isinstance(diagnostics_value, dict) else None
        )
        recorded = diagnostics is not None
        return {
            "run_id": run["id"],
            "task_family": run["task_family"],
            "user_id": run["user_id"],
            "learning_enabled": (
                diagnostics.get("learning_enabled")
                if diagnostics is not None
                else learning.get("learning_enabled", False)
            ),
            "min_score": (
                diagnostics.get("min_score")
                if diagnostics is not None
                else learning.get("retrieval_min_score")
            ),
            "recorded": recorded,
            "injected": payload.get("items") or [],
            "skipped": diagnostics.get("skipped", []) if diagnostics is not None else [],
            "dropped_by_budget": (
                diagnostics.get("dropped_by_budget", []) if diagnostics is not None else []
            ),
            "selection_hash": payload.get("selection_hash"),
            "event_time": event["create_time"] if event is not None else None,
        }
