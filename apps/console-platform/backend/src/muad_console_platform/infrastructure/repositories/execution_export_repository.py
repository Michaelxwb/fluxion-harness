"""Read-only collection for one execution-chain export.

Every read happens inside one ``REPEATABLE READ, READ ONLY`` PostgreSQL transaction so the
Run/Task, events, audits and child tasks all describe the same instant. Core sections
(Run/Task + their own events) are required; optional sections degrade to an explicit
``section_status=error`` + warning instead of aborting the whole export.

Owner tables stay owned by Runtime/Worker; this module is a Console-side read-only
projection (same pattern as ``audit_query_repository`` / ``run_query_repository``).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from ...application.execution_export_settings import ExecutionExportSettings


@dataclass(frozen=True)
class ExportSection:
    """One source table read: rows are raw stored JSON (credential masking happens later)."""

    status: str
    rows: tuple[dict[str, Any], ...] = ()
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class ExecutionExportBundle:
    kind: str
    record: dict[str, Any]
    sections: dict[str, ExportSection]
    database_snapshot: dict[str, str]
    warnings: tuple[dict[str, str], ...] = ()
    truncation: dict[str, int | bool] = field(default_factory=dict)


_RUN_SQL = text(
    """
    SELECT to_jsonb(r) AS record
      FROM runtime.run_record r
     WHERE r.tenant_id = :tenant_id
       AND r.id = :run_id
       AND r.is_deleted = false
    """
)

_TASK_SQL = text(
    """
    SELECT to_jsonb(t) AS record
      FROM task.task_execution t
     WHERE t.tenant_id = :tenant_id
       AND t.id = :task_id
       AND t.is_deleted = false
    """
)

_SNAPSHOT_SQL = text(
    """
    SELECT current_timestamp::text AS snapshot_time,
           current_setting('transaction_read_only') AS transaction_read_only,
           current_setting('transaction_isolation') AS transaction_isolation
    """
)

#: Run 的补充段与核心段；键即 Owner 表名，渲染层按表名映射为报告字段。
_RUN_SECTION_SQL: dict[str, str] = {
    "runtime.runtime_snapshot": """
        SELECT to_jsonb(r) AS record FROM runtime.runtime_snapshot r
         WHERE r.tenant_id = :tenant_id AND r.run_id = :run_id AND r.is_deleted = false
         ORDER BY r.create_time, r.id
    """,
    "runtime.canonical_event": """
        SELECT to_jsonb(e) AS record FROM runtime.canonical_event e
         WHERE e.tenant_id = :tenant_id AND e.run_id = :run_id AND e.is_deleted = false
           AND e.event_type <> 'ASSISTANT_DELTA'
         ORDER BY e.seq, e.id
         LIMIT :row_limit
    """,
    "runtime.tool_call_audit": """
        SELECT to_jsonb(a) AS record FROM runtime.tool_call_audit a
         WHERE a.tenant_id = :tenant_id AND a.run_id = :run_id AND a.is_deleted = false
         ORDER BY a.create_time, a.id
    """,
    "runtime.model_invocation_audit": """
        SELECT to_jsonb(a) AS record FROM runtime.model_invocation_audit a
         WHERE a.tenant_id = :tenant_id AND a.run_id = :run_id AND a.is_deleted = false
         ORDER BY a.create_time, a.id
    """,
    "runtime.egress_audit": """
        SELECT to_jsonb(a) AS record FROM runtime.egress_audit a
         WHERE a.tenant_id = :tenant_id AND a.run_id = :run_id AND a.is_deleted = false
         ORDER BY a.create_time, a.id
    """,
    "runtime.tool_operation": """
        SELECT to_jsonb(o) AS record FROM runtime.tool_operation o
         WHERE o.tenant_id = :tenant_id AND o.run_id = :run_id AND o.is_deleted = false
         ORDER BY o.create_time, o.id
    """,
    "runtime.run_continuation": """
        SELECT to_jsonb(c) AS record FROM runtime.run_continuation c
         WHERE c.tenant_id = :tenant_id AND c.run_id = :run_id AND c.is_deleted = false
    """,
    "runtime.tool_result_inbox": """
        SELECT to_jsonb(i) AS record FROM runtime.tool_result_inbox i
         WHERE i.tenant_id = :tenant_id AND i.run_id = :run_id AND i.is_deleted = false
         ORDER BY i.receipt_seq, i.id
    """,
    "runtime.run_submission": """
        SELECT to_jsonb(s) AS record FROM runtime.run_submission s
         WHERE s.tenant_id = :tenant_id AND s.run_id = :run_id AND s.is_deleted = false
         ORDER BY s.create_time, s.id
    """,
    "runtime.run_interrupt": """
        SELECT to_jsonb(i) AS record FROM runtime.run_interrupt i
         WHERE i.tenant_id = :tenant_id AND i.run_id = :run_id AND i.is_deleted = false
         ORDER BY i.create_time, i.id
    """,
}

_TASK_TREE_SQL = """
    WITH RECURSIVE related(id) AS (
        SELECT t.id
          FROM task.task_execution t
         WHERE t.tenant_id = :tenant_id
           AND t.is_deleted = false
           AND (t.source_run_id = :run_id OR t.id = ANY(CAST(:operation_task_ids AS uuid[])))
        UNION
        SELECT t.id
          FROM task.task_execution t
          JOIN related p ON t.parent_id = p.id
         WHERE t.tenant_id = :tenant_id
           AND t.is_deleted = false
    )
    SELECT to_jsonb(t) AS record
      FROM task.task_execution t
      JOIN related r ON r.id = t.id
     ORDER BY t.create_time, t.id
     LIMIT :row_limit
"""

_TASK_EVENT_SQL = """
    SELECT to_jsonb(e) AS record FROM task.task_event e
     WHERE e.tenant_id = :tenant_id AND e.task_id = ANY(CAST(:task_ids AS uuid[])) AND e.is_deleted = false
     ORDER BY e.task_id, e.seq, e.id
     LIMIT :row_limit
"""

_CONTROL_OUTBOX_SQL = """
    SELECT to_jsonb(o) AS record FROM runtime.tool_control_outbox o
     WHERE o.tenant_id = :tenant_id
       AND o.operation_id = ANY(CAST(:operation_ids AS uuid[]))
       AND o.is_deleted = false
     ORDER BY o.not_before, o.id
"""

_ARTIFACT_SQL = """
    SELECT to_jsonb(a) AS record FROM runtime.artifact a
     WHERE a.tenant_id = :tenant_id AND a.is_deleted = false
       AND (a.run_id = :run_id OR a.task_id = ANY(CAST(:task_ids AS uuid[])))
     ORDER BY a.create_time, a.id
"""

_CONVERSATION_SQL = """
    SELECT to_jsonb(c) AS record FROM runtime.conversation c
     WHERE c.tenant_id = :tenant_id AND c.id = :conversation_id AND c.is_deleted = false
"""

_TASK_CHILD_SQL = """
    WITH RECURSIVE related(id) AS (
        SELECT t.id FROM task.task_execution t
         WHERE t.tenant_id = :tenant_id AND t.id = :task_id AND t.is_deleted = false
        UNION
        SELECT t.id FROM task.task_execution t
          JOIN related p ON t.parent_id = p.id
         WHERE t.tenant_id = :tenant_id AND t.is_deleted = false
    )
    SELECT to_jsonb(t) AS record FROM task.task_execution t
      JOIN related r ON r.id = t.id
     ORDER BY t.create_time, t.id
     LIMIT :row_limit
"""

_TASK_SOURCE_RUN_SQL = """
    SELECT to_jsonb(r) AS record FROM runtime.run_record r
     WHERE r.tenant_id = :tenant_id AND r.id = :run_id AND r.is_deleted = false
"""

_TASK_SOURCE_EVENT_SQL = """
    SELECT to_jsonb(e) AS record FROM runtime.canonical_event e
     WHERE e.tenant_id = :tenant_id AND e.run_id = :run_id AND e.is_deleted = false
       AND e.event_type <> 'ASSISTANT_DELTA'
       AND (
           e.payload_json->>'task_id' = :task_id
           OR e.payload_json->>'tool_call_id' = :tool_call_id
           OR e.payload_json->>'operation_id' = :operation_id
       )
     ORDER BY e.seq, e.id
     LIMIT :row_limit
"""

_TASK_AUDIT_SQL = {
    "runtime.tool_call_audit": """
        SELECT to_jsonb(a) AS record FROM runtime.tool_call_audit a
         WHERE a.tenant_id = :tenant_id AND a.task_id = :task_id AND a.is_deleted = false
         ORDER BY a.create_time, a.id
    """,
    "runtime.model_invocation_audit": """
        SELECT to_jsonb(a) AS record FROM runtime.model_invocation_audit a
         WHERE a.tenant_id = :tenant_id AND a.task_id = :task_id AND a.is_deleted = false
         ORDER BY a.create_time, a.id
    """,
    "runtime.egress_audit": """
        SELECT to_jsonb(a) AS record FROM runtime.egress_audit a
         WHERE a.tenant_id = :tenant_id AND a.task_id = :task_id AND a.is_deleted = false
         ORDER BY a.create_time, a.id
    """,
}

_TASK_OPERATION_SQL = """
    SELECT to_jsonb(o) AS record FROM runtime.tool_operation o
     WHERE o.tenant_id = :tenant_id AND o.is_deleted = false
       AND (o.task_id = :task_id OR o.id::text = :source_operation_id)
     ORDER BY o.create_time, o.id
"""


def _ids(rows: tuple[dict[str, Any], ...] | list[dict[str, Any]], column: str) -> list[uuid.UUID]:
    values: list[uuid.UUID] = []
    for row in rows:
        value = row.get(column)
        if value is None:
            continue
        if isinstance(value, uuid.UUID):
            values.append(value)
        else:
            values.append(uuid.UUID(str(value)))
    return values


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


class ExecutionExportRepository:
    def __init__(self, session: AsyncSession, settings: ExecutionExportSettings | None = None) -> None:
        self._session = session
        self._settings = settings or ExecutionExportSettings.from_shared()

    async def _begin_snapshot(self) -> None:
        """Start the one read-only snapshot shared by every section.

        `get_session` does not begin until the first statement, so this must be the first
        statement in the request. If a caller already ran a query, PostgreSQL would reject
        the SET; the resulting error is surfaced instead of silently reading inconsistent data.
        """
        await self._session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        await self._session.execute(text("SELECT set_config('TimeZone', 'UTC', true)"))

    async def _read(
        self,
        name: str,
        sql: str,
        params: dict[str, Any],
        *,
        required: bool = False,
        row_limit: int | None = None,
    ) -> ExportSection:
        try:
            async with self._session.begin_nested():
                rows = (
                    (await self._session.execute(text(sql), {**params, "row_limit": row_limit or 1_000_000}))
                    .mappings()
                    .all()
                )
        except SQLAlchemyError as exc:
            if required:
                raise
            return ExportSection(status="error", error_code=type(exc).__name__, error_message=str(exc))
        return ExportSection(
            status="ok",
            rows=tuple(dict(row["record"]) for row in rows),
        )

    async def _snapshot(self) -> dict[str, str]:
        row = (await self._session.execute(_SNAPSHOT_SQL)).mappings().one()
        return {key: str(row[key]) for key in row}

    async def run_bundle(self, tenant_id: str, run_id: uuid.UUID) -> ExecutionExportBundle | None:
        await self._begin_snapshot()
        run_row = (
            (await self._session.execute(_RUN_SQL, {"tenant_id": tenant_id, "run_id": run_id}))
            .mappings()
            .one_or_none()
        )
        if run_row is None:
            return None
        record = dict(run_row["record"])
        conversation_id = record.get("conversation_id")
        params = {"tenant_id": tenant_id, "run_id": run_id}

        sections: dict[str, ExportSection] = {}
        warnings: list[dict[str, str]] = []
        truncation: dict[str, int | bool] = {"truncated": False}

        for name, sql in _RUN_SECTION_SQL.items():
            limit = self._settings.max_events + 1 if name == "runtime.canonical_event" else None
            section = await self._read(name, sql, params, row_limit=limit)
            sections[name] = section
            if section.status != "ok":
                warnings.append(
                    {"section": name, "status": section.status, "error_code": section.error_code or ""}
                )

        events = sections.get("runtime.canonical_event")
        if events and events.status == "ok" and len(events.rows) > self._settings.max_events:
            sections["runtime.canonical_event"] = ExportSection(
                status="ok", rows=events.rows[: self._settings.max_events]
            )
            truncation.update(
                truncated=True,
                omitted_events=len(events.rows) - self._settings.max_events,
                max_events=self._settings.max_events,
            )

        operations = sections.get("runtime.tool_operation")
        operation_rows = operations.rows if operations and operations.status == "ok" else ()
        operation_ids = _ids(operation_rows, "id")
        operation_task_ids = _ids(operation_rows, "task_id")

        tasks = await self._read(
            "task.task_execution",
            _TASK_TREE_SQL,
            {
                "tenant_id": tenant_id,
                "run_id": run_id,
                "operation_task_ids": operation_task_ids,
            },
            row_limit=self._settings.max_task_children + 1,
        )
        if tasks.status == "ok" and len(tasks.rows) > self._settings.max_task_children:
            tasks = ExportSection(status="ok", rows=tasks.rows[: self._settings.max_task_children])
            truncation.update(
                truncated=True,
                omitted_task_children=len(tasks.rows) - self._settings.max_task_children,
                max_task_children=self._settings.max_task_children,
            )
        sections["task.task_execution"] = tasks
        if tasks.status != "ok":
            warnings.append(
                {
                    "section": "task.task_execution",
                    "status": tasks.status,
                    "error_code": tasks.error_code or "",
                }
            )

        task_ids = _ids(tasks.rows, "id") if tasks.status == "ok" else []
        task_events = await self._read(
            "task.task_event",
            _TASK_EVENT_SQL,
            {"tenant_id": tenant_id, "task_ids": task_ids},
            row_limit=self._settings.max_task_events + 1,
        )
        if task_events.status == "ok" and len(task_events.rows) > self._settings.max_task_events:
            task_events = ExportSection(status="ok", rows=task_events.rows[: self._settings.max_task_events])
            truncation.update(
                truncated=True,
                omitted_task_events=len(task_events.rows) - self._settings.max_task_events,
                max_task_events=self._settings.max_task_events,
            )
        sections["task.task_event"] = task_events
        if task_events.status != "ok":
            warnings.append(
                {
                    "section": "task.task_event",
                    "status": task_events.status,
                    "error_code": task_events.error_code or "",
                }
            )

        for name, sql in (
            ("runtime.tool_control_outbox", _CONTROL_OUTBOX_SQL),
            ("runtime.artifact", _ARTIFACT_SQL),
        ):
            section = await self._read(
                name,
                sql,
                {
                    "tenant_id": tenant_id,
                    "run_id": run_id,
                    "task_ids": task_ids,
                    "operation_ids": operation_ids,
                },
            )
            sections[name] = section
            if section.status != "ok":
                warnings.append(
                    {"section": name, "status": section.status, "error_code": section.error_code or ""}
                )

        if conversation_id:
            section = await self._read(
                "runtime.conversation",
                _CONVERSATION_SQL,
                {"tenant_id": tenant_id, "conversation_id": conversation_id},
            )
            sections["runtime.conversation"] = section
            if section.status != "ok":
                warnings.append(
                    {
                        "section": "runtime.conversation",
                        "status": section.status,
                        "error_code": section.error_code or "",
                    }
                )

        snapshot = await self._snapshot()
        return ExecutionExportBundle(
            kind="RUN",
            record=record,
            sections=sections,
            database_snapshot=snapshot,
            warnings=tuple(warnings),
            truncation=truncation,
        )

    async def task_bundle(self, tenant_id: str, task_id: uuid.UUID) -> ExecutionExportBundle | None:
        await self._begin_snapshot()
        task_row = (
            (await self._session.execute(_TASK_SQL, {"tenant_id": tenant_id, "task_id": task_id}))
            .mappings()
            .one_or_none()
        )
        if task_row is None:
            return None
        record = dict(task_row["record"])
        params = {"tenant_id": tenant_id}

        sections: dict[str, ExportSection] = {}
        warnings: list[dict[str, str]] = []
        truncation: dict[str, int | bool] = {"truncated": False}

        task_events = await self._read(
            "task.task_event",
            """
            SELECT to_jsonb(e) AS record FROM task.task_event e
             WHERE e.tenant_id = :tenant_id AND e.task_id = :task_id AND e.is_deleted = false
             ORDER BY e.seq, e.id
             LIMIT :row_limit
            """,
            {**params, "task_id": task_id},
            row_limit=self._settings.max_task_events + 1,
        )
        if task_events.status == "ok" and len(task_events.rows) > self._settings.max_task_events:
            task_events = ExportSection(status="ok", rows=task_events.rows[: self._settings.max_task_events])
            truncation.update(
                truncated=True, omitted_task_events=len(task_events.rows) - self._settings.max_task_events
            )
        sections["task.task_event"] = task_events

        children = await self._read(
            "task.task_execution",
            _TASK_CHILD_SQL,
            {**params, "task_id": task_id},
            row_limit=self._settings.max_task_children + 1,
        )
        if children.status == "ok" and len(children.rows) > self._settings.max_task_children:
            children = ExportSection(status="ok", rows=children.rows[: self._settings.max_task_children])
            truncation.update(
                truncated=True, omitted_task_children=len(children.rows) - self._settings.max_task_children
            )
        sections["task.task_execution"] = children

        child_ids = _ids(children.rows, "id") if children.status == "ok" else []
        child_events = await self._read(
            "task.task_event.children",
            _TASK_EVENT_SQL,
            {**params, "task_ids": child_ids},
            row_limit=self._settings.max_task_events + 1,
        )
        if child_events.status == "ok" and len(child_events.rows) > self._settings.max_task_events:
            child_events = ExportSection(
                status="ok", rows=child_events.rows[: self._settings.max_task_events]
            )
            truncation.update(
                truncated=True, omitted_task_events=len(child_events.rows) - self._settings.max_task_events
            )
        sections["task.task_event.children"] = child_events

        source_run_id = record.get("source_run_id")
        if source_run_id:
            source_run = await self._read(
                "runtime.run_record",
                _TASK_SOURCE_RUN_SQL,
                {**params, "run_id": source_run_id},
            )
            sections["runtime.run_record"] = source_run
            for name, sql in _TASK_AUDIT_SQL.items():
                sections[name] = await self._read(name, sql, {**params, "task_id": task_id})
            sections["runtime.tool_operation"] = await self._read(
                "runtime.tool_operation",
                _TASK_OPERATION_SQL,
                {
                    **params,
                    "task_id": task_id,
                    "source_operation_id": record.get("source_operation_id") or "",
                },
            )
            operations = sections["runtime.tool_operation"]
            operation_ids = _ids(operations.rows, "id") if operations.status == "ok" else []
            source_events = await self._read(
                "runtime.canonical_event",
                _TASK_SOURCE_EVENT_SQL,
                {
                    **params,
                    "run_id": source_run_id,
                    "task_id": str(task_id),
                    "tool_call_id": record.get("source_tool_call_id") or "",
                    "operation_id": record.get("source_operation_id") or "",
                },
                row_limit=self._settings.max_events,
            )
            sections["runtime.canonical_event"] = source_events
            sections["runtime.tool_control_outbox"] = await self._read(
                "runtime.tool_control_outbox",
                _CONTROL_OUTBOX_SQL,
                {**params, "operation_ids": operation_ids},
            )
            sections["runtime.artifact"] = await self._read(
                "runtime.artifact",
                """
                SELECT to_jsonb(a) AS record FROM runtime.artifact a
                 WHERE a.tenant_id = :tenant_id AND a.task_id = :task_id AND a.is_deleted = false
                 ORDER BY a.create_time, a.id
                """,
                {**params, "task_id": task_id},
            )
            conversation_id = (sections.get("runtime.run_record") or ExportSection(status="error")).rows
            if conversation_id:
                cid = conversation_id[0].get("conversation_id")
                if cid:
                    sections["runtime.conversation"] = await self._read(
                        "runtime.conversation",
                        _CONVERSATION_SQL,
                        {**params, "conversation_id": cid},
                    )

        for name, section in sections.items():
            if section.status != "ok":
                warnings.append(
                    {"section": name, "status": section.status, "error_code": section.error_code or ""}
                )

        snapshot = await self._snapshot()
        return ExecutionExportBundle(
            kind="TASK",
            record=record,
            sections=sections,
            database_snapshot=snapshot,
            warnings=tuple(warnings),
            truncation=truncation,
        )
