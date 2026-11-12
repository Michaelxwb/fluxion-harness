# ruff: noqa: E501
"""One read-only SQL snapshot: aggregate before joining to avoid multiplying facts."""

OVERVIEW_SQL = """
WITH all_runs AS MATERIALIZED (
  SELECT id, tenant_id, agent_id, user_id, status, create_time, start_time, end_time
  FROM runtime.run_record
  WHERE tenant_id = :tenant_id AND NOT is_deleted AND create_time <= :now
), period_runs AS (
  SELECT * FROM all_runs WHERE create_time >= :start
), all_tasks AS MATERIALIZED (
  SELECT status, delivery_mode, delivery_status, create_time, finished_at, deadline_at
  FROM task.task_execution
  WHERE tenant_id = :tenant_id AND NOT is_deleted
    AND parent_id IS NULL AND create_time <= :now
), period_tasks AS (
  SELECT * FROM all_tasks WHERE create_time >= :start
), model_requests AS MATERIALIZED (
  SELECT a.create_time, a.input_tokens, a.output_tokens
  FROM runtime.model_invocation_audit a
  JOIN all_runs r ON r.id = a.run_id AND r.tenant_id = a.tenant_id
  WHERE a.tenant_id = :tenant_id AND NOT a.is_deleted
    AND a.create_time >= :start AND a.create_time <= :now
), calendar AS (
  SELECT day::date AS day FROM generate_series(
    (CAST(:start AS timestamptz) AT TIME ZONE :tz)::date,
    (CAST(:now AS timestamptz) AT TIME ZONE :tz)::date,
    interval '1 day'
  ) AS day
), egress_requests AS MATERIALIZED (
  SELECT CASE WHEN method IS NOT NULL AND left(operation, length(method) + 2) = method || ' /'
    THEN target || substring(operation FROM length(method) + 2) ELSE target END AS url,
    method, platform_id, result_status, policy_decision, latency_ms
  FROM runtime.egress_audit
  WHERE tenant_id = :tenant_id AND NOT is_deleted
    AND target_type = 'HTTP' AND phase = 'completed'
    AND create_time >= :start AND create_time <= :now
), egress_top AS (
  SELECT url, method, platform_id, count(*) AS request_count,
    count(*) FILTER (WHERE policy_decision IN ('DENY','BLOCKED') OR result_status = 'BLOCKED') AS blocked_count
  FROM egress_requests GROUP BY url, method, platform_id
  ORDER BY request_count DESC, url, method, platform_id LIMIT 10
), egress_platforms AS (
  SELECT e.*, matched.platform_name FROM egress_top e
  LEFT JOIN LATERAL (
    SELECT CASE WHEN count(*) = 1 THEN min(name) END AS platform_name FROM (
      SELECT p.name, dense_rank() OVER (ORDER BY
        (p.id = e.platform_id) DESC NULLS LAST,
        length(rtrim(p.resolver_config_json->>'base_url', '/')) DESC NULLS LAST) AS priority
      FROM control.project_platform p
      WHERE p.tenant_id = :tenant_id AND NOT p.is_deleted AND (
        p.id = e.platform_id OR (e.platform_id IS NULL AND (
          e.url = rtrim(p.resolver_config_json->>'base_url', '/') OR
          left(e.url, length(rtrim(p.resolver_config_json->>'base_url', '/')) + 1) =
            rtrim(p.resolver_config_json->>'base_url', '/') || '/')))
    ) candidates WHERE priority = 1
  ) matched ON true
), run_stats AS (
  SELECT count(*) AS total, count(DISTINCT user_id) AS active_users,
    count(*) FILTER (WHERE status = 'FAILED') AS failed,
    100.0 * count(*) FILTER (WHERE status = 'COMPLETED') /
      nullif(count(*) FILTER (WHERE status IN ('COMPLETED','FAILED')), 0) AS success_rate,
    avg(extract(epoch FROM (end_time - start_time)) * 1000) FILTER (
      WHERE status IN ('COMPLETED','FAILED','CANCELLED')
        AND start_time IS NOT NULL AND end_time >= start_time AND end_time <= :now
    ) AS average_duration
  FROM period_runs
), model_stats AS (
  SELECT count(*) AS total, sum(input_tokens) AS inputs, sum(output_tokens) AS outputs,
    count(*) FILTER (WHERE input_tokens IS NOT NULL AND output_tokens IS NOT NULL) AS reported
  FROM model_requests
), run_created AS (
  SELECT (create_time AT TIME ZONE :tz)::date AS day, count(*) AS total
  FROM period_runs GROUP BY day
), run_failed AS (
  SELECT (end_time AT TIME ZONE :tz)::date AS day, count(*) AS total
  FROM all_runs WHERE status = 'FAILED' AND end_time >= :start AND end_time <= :now
  GROUP BY day
), task_created AS (
  SELECT (create_time AT TIME ZONE :tz)::date AS day, count(*) AS total
  FROM period_tasks GROUP BY day
), task_failed AS (
  SELECT (finished_at AT TIME ZONE :tz)::date AS day, count(*) AS total
  FROM all_tasks WHERE status = 'FAILED' AND finished_at >= :start AND finished_at <= :now
  GROUP BY day
), token_days AS (
  SELECT (create_time AT TIME ZONE :tz)::date AS day, count(*) AS total,
    sum(input_tokens) AS inputs, sum(output_tokens) AS outputs,
    count(*) FILTER (WHERE input_tokens IS NOT NULL AND output_tokens IS NOT NULL) AS reported
  FROM model_requests GROUP BY day
), rank_counts AS (
  SELECT agent_id, count(*) AS run_count FROM period_runs GROUP BY agent_id
  ORDER BY run_count DESC, agent_id LIMIT 5
), rank_names AS (
  SELECT c.agent_id, a.name AS agent_name, c.run_count
  FROM rank_counts c LEFT JOIN control.agent_definition a
    ON a.id = c.agent_id AND a.tenant_id = :tenant_id AND NOT a.is_deleted
)
SELECT jsonb_build_object(
  'resources', jsonb_build_object(
    'enabled_agents', (SELECT count(*) FROM control.agent_definition
      WHERE tenant_id = :tenant_id AND enabled AND NOT is_deleted),
    'enabled_skills', (SELECT count(*) FROM control.skill
      WHERE tenant_id = :tenant_id AND enabled AND NOT is_deleted),
    'enabled_mcps', (SELECT count(*) FROM control.mcp_server
      WHERE tenant_id = :tenant_id AND enabled AND NOT is_deleted),
    'enabled_models', (SELECT count(*) FROM control.model_definition
      WHERE tenant_id = :tenant_id AND enabled AND NOT is_deleted),
    'users', (SELECT count(*) FROM control.platform_user
      WHERE tenant_id = :tenant_id AND NOT is_deleted),
    'active_schedules', (SELECT count(*) FROM task.task_schedule
      WHERE tenant_id = :tenant_id AND status = 'ACTIVE' AND NOT is_deleted)
  ),
  'kpis', (SELECT jsonb_build_object(
    'run_count', r.total, 'run_success_rate', r.success_rate,
    'average_run_duration_ms', r.average_duration, 'active_users', r.active_users,
    'model_request_count', m.total,
    'input_tokens', CASE WHEN m.total = 0 THEN 0 ELSE m.inputs END,
    'output_tokens', CASE WHEN m.total = 0 THEN 0 ELSE m.outputs END,
    'total_tokens', CASE WHEN m.total = 0 THEN 0
      WHEN m.inputs IS NULL AND m.outputs IS NULL THEN NULL
      ELSE coalesce(m.inputs, 0) + coalesce(m.outputs, 0) END,
    'token_reported_request_count', m.reported,
    'token_coverage', 100.0 * m.reported / nullif(m.total, 0)
  ) FROM run_stats r CROSS JOIN model_stats m),
  'activity', jsonb_build_object(
    'active_runs', (SELECT count(*) FROM all_runs
      WHERE status IN ('CREATED','RUNNING','WAITING_TOOL','WAITING_INPUT')),
    'waiting_input_runs', (SELECT count(*) FROM all_runs WHERE status = 'WAITING_INPUT'),
    'waiting_tool_runs', (SELECT count(*) FROM all_runs WHERE status = 'WAITING_TOOL'),
    'queued_tasks', (SELECT count(*) FROM all_tasks WHERE status = 'QUEUED'),
    'running_tasks', (SELECT count(*) FROM all_tasks WHERE status = 'RUNNING'),
    'waiting_tasks', (SELECT count(*) FROM all_tasks WHERE status = 'WAITING'),
    'overdue_tasks', (SELECT count(*) FROM all_tasks
      WHERE status IN ('QUEUED','RUNNING','WAITING') AND deadline_at <= :now)
  ),
  'attention', jsonb_build_object(
    'failed_runs', (SELECT failed FROM run_stats),
    'failed_tasks', (SELECT count(*) FROM period_tasks WHERE status = 'FAILED'),
    'failed_deliveries', (SELECT count(*) FROM period_tasks
      WHERE delivery_mode = 'FINAL_ONLY' AND delivery_status = 'FAILED')
  ),
  'trend', jsonb_build_object(
    'runs', (SELECT jsonb_agg(jsonb_build_object('date', c.day::text,
      'total', coalesce(n.total, 0), 'failed', coalesce(f.total, 0)) ORDER BY c.day)
      FROM calendar c LEFT JOIN run_created n USING(day) LEFT JOIN run_failed f USING(day)),
    'tasks', (SELECT jsonb_agg(jsonb_build_object('date', c.day::text,
      'total', coalesce(n.total, 0), 'failed', coalesce(f.total, 0)) ORDER BY c.day)
      FROM calendar c LEFT JOIN task_created n USING(day) LEFT JOIN task_failed f USING(day)),
    'tokens', (SELECT jsonb_agg(jsonb_build_object('date', c.day::text,
      'input_tokens', CASE WHEN d.total IS NULL THEN 0 ELSE d.inputs END,
      'output_tokens', CASE WHEN d.total IS NULL THEN 0 ELSE d.outputs END,
      'model_request_count', coalesce(d.total, 0),
      'token_reported_request_count', coalesce(d.reported, 0)) ORDER BY c.day)
      FROM calendar c LEFT JOIN token_days d USING(day))
  ),
  'status', jsonb_build_object(
    'runs', coalesce((SELECT jsonb_object_agg(status, total) FROM
      (SELECT status, count(*) AS total FROM period_runs GROUP BY status) s), '{}'::jsonb),
    'tasks', coalesce((SELECT jsonb_object_agg(status, total) FROM
      (SELECT status, count(*) AS total FROM period_tasks GROUP BY status) s), '{}'::jsonb)
  ),
  'agent_ranking', coalesce((SELECT jsonb_agg(jsonb_build_object(
    'agent_id', agent_id, 'agent_name', agent_name, 'run_count', run_count)
    ORDER BY run_count DESC, agent_id) FROM rank_names), '[]'::jsonb),
  'egress', jsonb_build_object(
    'request_count', (SELECT count(*) FROM egress_requests),
    'sent_count', (SELECT count(*) FROM egress_requests WHERE policy_decision IN ('ALLOW','ALLOWED')),
    'completed_count', (SELECT count(*) FROM egress_requests WHERE result_status IN ('OK','COMPLETED','SUCCESS')),
    'failed_count', (SELECT count(*) FROM egress_requests WHERE result_status IN ('FAILED','ERROR','TIMEOUT')),
    'blocked_count', (SELECT count(*) FROM egress_requests WHERE policy_decision IN ('DENY','BLOCKED') OR result_status = 'BLOCKED'),
    'average_latency_ms', (SELECT avg(latency_ms) FROM egress_requests WHERE latency_ms IS NOT NULL),
    'top_targets', coalesce((SELECT jsonb_agg(jsonb_build_object(
      'url', url, 'method', method, 'platform_name', platform_name,
      'request_count', request_count, 'blocked_count', blocked_count)
      ORDER BY request_count DESC, url, method) FROM egress_platforms), '[]'::jsonb)
  )
)
"""
