"""One event's tenant-scoped, read-only joins; never infer call pairs by time."""

import uuid

from pydantic import BaseModel, JsonValue
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class RunEventDetailRow(BaseModel):
    seq: int
    event_type: str
    event_data: dict[str, JsonValue]
    tool: dict[str, JsonValue] | None
    model: dict[str, JsonValue] | None
    model_snapshot: dict[str, JsonValue] | None
    model_summary: dict[str, JsonValue] | None
    run_status: str
    operation: dict[str, JsonValue] | None
    artifact: dict[str, JsonValue] | None


_EVENT_SQL = text("""
SELECT e.seq, e.event_type,
       CASE WHEN jsonb_typeof(e.payload_json) = 'object' THEN e.payload_json
            ELSE '{}'::jsonb END AS event_data,
       tool.detail AS tool, model.detail AS model, r.status AS run_status,
       CASE WHEN s.id IS NOT NULL THEN jsonb_build_object(
           'model', s.model_json->>'model_id', 'protocol', s.model_json->>'protocol',
           'model_revision', s.model_revision) END AS model_snapshot,
       model_summary.detail AS model_summary,
       operation.detail AS operation, artifact.detail AS artifact
  FROM runtime.canonical_event e
  JOIN runtime.run_record r ON r.id = e.run_id AND r.tenant_id = e.tenant_id
       AND r.is_deleted = false
  LEFT JOIN runtime.runtime_snapshot s ON s.id = r.snapshot_id AND s.run_id = r.id
       AND s.tenant_id = r.tenant_id AND NOT s.is_deleted
       AND e.event_type IN ('MODEL_CALL_STARTED', 'MODEL_CALL_COMPLETED')
  LEFT JOIN LATERAL (
    SELECT a.artifact_id, jsonb_build_object(
        'tool_name', a.tool_name, 'tool_call_id', a.tool_call_id,
        'status', a.status, 'duration_ms', a.latency_ms,
        'error_code', a.error_code, 'arguments', a.args_preview_json) AS detail
      FROM runtime.tool_call_audit a
     WHERE a.tenant_id = e.tenant_id AND a.run_id = e.run_id AND NOT a.is_deleted
       AND a.tool_call_id = e.payload_json->>'tool_call_id'
     ORDER BY a.create_time DESC, a.id LIMIT 1
  ) tool ON true
  LEFT JOIN LATERAL (
    SELECT jsonb_build_object(
        'provider', a.provider, 'model', a.model, 'attempt', a.attempt,
        'retry_reason', a.retry_reason, 'status', a.status,
        'input_tokens', a.input_tokens, 'output_tokens', a.output_tokens,
        'duration_ms', a.latency_ms, 'error_code', a.error_code) AS detail
      FROM runtime.model_invocation_audit a
     WHERE a.tenant_id = e.tenant_id AND a.run_id = e.run_id AND NOT a.is_deleted
       AND a.id::text = e.payload_json->>'model_audit_id'
  ) model ON true
  LEFT JOIN LATERAL (
    SELECT jsonb_build_object(
        'call_count', count(*), 'success_count', count(*) FILTER (WHERE a.status = 'OK'),
        'retry_count', count(*) FILTER (WHERE a.status = 'RETRY'),
        'error_count', count(*) FILTER (WHERE a.status = 'ERROR'),
        'input_tokens', sum(a.input_tokens), 'output_tokens', sum(a.output_tokens),
        'duration_ms', sum(a.latency_ms)) AS detail
      FROM runtime.model_invocation_audit a
     WHERE a.tenant_id = e.tenant_id AND a.run_id = e.run_id AND NOT a.is_deleted
       AND e.event_type IN ('MODEL_CALL_STARTED', 'MODEL_CALL_COMPLETED')
       AND model.detail IS NULL
    HAVING count(*) > 0
  ) model_summary ON true
  LEFT JOIN LATERAL (
    SELECT t.id AS task_id, jsonb_build_object(
        'operation_id', o.id, 'task_id', o.task_id, 'status', o.status,
        'completion_mode', o.completion_mode, 'error_code', o.error_code,
        'task_status', t.status) AS detail
      FROM runtime.tool_operation o
      LEFT JOIN task.task_execution t ON t.id = o.task_id AND t.tenant_id = o.tenant_id
           AND t.source_run_id = e.run_id AND NOT t.is_deleted
     WHERE o.tenant_id = e.tenant_id AND o.run_id = e.run_id AND NOT o.is_deleted
       AND (o.id::text = e.payload_json->>'operation_id'
            OR (e.payload_json->>'operation_id' IS NULL
                AND o.source_tool_call_id = e.payload_json->>'tool_call_id'))
     ORDER BY o.create_time DESC, o.id LIMIT 1
  ) operation ON true
  LEFT JOIN LATERAL (
    SELECT jsonb_build_object('id', a.id, 'filename', a.metadata_json->>'filename',
        'media_type', a.media_type, 'size', a.size, 'preview', a.preview_text) AS detail
      FROM runtime.artifact a
     WHERE a.tenant_id = e.tenant_id AND NOT a.is_deleted
       AND (a.run_id = e.run_id OR a.task_id = operation.task_id)
       AND a.id::text = COALESCE(e.artifact_id::text, tool.artifact_id::text,
                                e.payload_json->>'artifact_id')
  ) artifact ON true
 WHERE e.tenant_id = :tenant_id AND e.run_id = :run_id AND e.seq = :seq
   AND NOT e.is_deleted AND e.event_type <> 'ASSISTANT_DELTA'
""")


class RunEventQueryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def detail(self, tenant_id: str, run_id: uuid.UUID, seq: int) -> RunEventDetailRow | None:
        result = await self._session.execute(
            _EVENT_SQL, {"tenant_id": tenant_id, "run_id": run_id, "seq": seq}
        )
        row = result.mappings().one_or_none()
        return RunEventDetailRow.model_validate(row) if row is not None else None
