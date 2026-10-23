import type { SkillListItem } from '../../apps/console-platform/frontend/src/modules/skill-management/services/skills';
import type { McpServerListItem } from '../../apps/console-platform/frontend/src/modules/mcp-management/services/mcpServers';
import type { ModelItem } from '../../apps/console-platform/frontend/src/modules/model-management/services/models';
import type { PlatformItem } from '../../apps/console-platform/frontend/src/modules/project-platform/services/platforms';
import type { UserListItem } from '../../apps/console-platform/frontend/src/modules/user-identity/services/users';
import type { TaskListItem } from '../../apps/console-platform/frontend/src/modules/task-schedule/services/tasks';
import type { ScheduleListItem } from '../../apps/console-platform/frontend/src/modules/task-schedule/services/schedules';

const time = '2026-09-30T00:00:00Z';
const id = 'f069722a-1234-4567-8901-234567890123';
const longName = 'Layout name with enough text to overflow a narrow column '.repeat(2);
const longKey = 'layout-key-that-would-otherwise-stretch-the-whole-table';
const skill: SkillListItem = {
  id, key: longKey, name: longName, description: '', platform_label: null, user_scope: 'SELECTED',
  enabled: true, current_artifact_id: 'version', current_version: '1.0.0', execution_mode: 'SCRIPT',
  agent_count: 12345, user_count: 12345, update_time: time
};
const mcp: McpServerListItem = {
  mcp_id: id, key: longKey, name: longName, transport: 'STREAMABLE_HTTP',
  endpoint: 'https://example.com/' + 'long-path/'.repeat(15), user_scope: 'ALL', enabled: true,
  connection_status: 'AVAILABLE', tool_count: 12345, using_agent_count: 12345,
  selected_user_count: 12345, last_discovered_at: time, update_time: time
};
const model: ModelItem = {
  id, key: longKey, name: longName, protocol: 'OPENAI', model_id: longKey,
  base_url: mcp.endpoint, api_key_configured: true, params: {}, revision: 123,
  enabled: true, last_test_status: 'AVAILABLE', last_test_at: time, create_time: time, update_time: time
};
const user: UserListItem = {
  id, user_code: longKey, display_name: longName, status: 'ACTIVE', agent_grant_count: 12345,
  credential_count: 12345, identity_count: 12345, memory_count: 12345, create_time: time, update_time: time
};
const platform: PlatformItem = {
  platform_id: id, key: longKey, name: longName, resolver_type: 'SERVICE_DISCOVERY',
  resolver_config: {}, adapter_key: longKey, adapter_config: {}, adapter_schema_version: '1',
  credential_mode: 'USER_THEN_SHARED', enabled: true, configured_user_credential_count: 12345,
  has_shared_credential: false, update_time: time
};
const task: TaskListItem = {
  task_id: id, tenant_id: 'tenant', agent_id: id, actor_user_id: id, skill_id: id,
  agent_name: longName, actor_name: longName, skill_name: longName, skill_key: longKey,
  schedule_id: null, source_run_id: null, intent_key: 'intent', status: 'FAILED', trigger_type: 'SCHEDULED',
  task_type: 'BATCH', input: {}, result: null, error_code: 'ERROR', error_message: longName,
  attempt: 1, max_attempts: 3, cancel_requested: false, delivery_mode: 'FINAL_ONLY', delivery_status: 'FAILED',
  delivery_attempts: 1, delivered_at: null, deadline_at: time, not_before: time, create_time: time,
  update_time: time, started_at: time, finished_at: time, child_total: 12345, child_finished: 12345
};
const schedule: ScheduleListItem = {
  schedule_id: id, tenant_id: 'tenant', name: longName, agent_id: id, actor_user_id: id, skill_id: id,
  agent_name: longName, actor_name: longName, skill_name: longName, skill_key: longKey,
  intent_key: 'intent', input_template: {}, schedule_type: 'CRON', cron_expr: '0 0 1 * *',
  timezone: 'America/Argentina/Buenos_Aires', run_at: null, status: 'ACTIVE', next_fire_at: time,
  last_fire_at: time, revision: 1, completed_at: null, last_error_code: null, last_error_message: null,
  last_skipped_at: null, create_time: time, update_time: time
};
const audit = {
  audit_id: id, audit_type: 'CONFIG', resource_type: 'AGENT_SKILL_BINDING', resource_id: id, actor_user_id: id,
  actor_name: longName, agent_id: id, agent_name: longName, action: longKey, result_status: 'FAILED',
  trace_id: '8344af30-94ba-4ec3-bdc9-1db10a98e8c1', target: longName, occurred_at: time,
  latency_ms: 123, run_id: null, task_id: null
};

export const listLayoutCases = [
  { path: 'skills', api: 'skills', data: [skill], columns: 9, dense: false, actions: true },
  { path: 'mcp', api: 'mcp-servers', data: [mcp, { ...mcp, mcp_id: 'unavailable-mcp', key: 'unavailable-mcp',
    enabled: false, user_scope: 'SELECTED', connection_status: 'DISCOVERY_FAILED' } satisfies McpServerListItem],
    columns: 11, dense: true, actions: true },
  { path: 'models', api: 'models', data: [model], columns: 10, dense: false, actions: true },
  { path: 'users', api: 'users', data: [user], columns: 9, dense: false, actions: true },
  { path: 'platforms', api: 'project-platforms', data: [platform], columns: 10, dense: true, actions: true },
  { path: 'tasks', api: 'tasks', data: [task, { ...task, task_id: 'running-task', status: 'RUNNING',
    finished_at: null, error_code: null, error_message: null } satisfies TaskListItem], columns: 11, dense: true, actions: true },
  { path: 'schedules', api: 'schedules', data: [schedule, { ...schedule, schedule_id: 'once-schedule',
    schedule_type: 'ONCE', cron_expr: null, run_at: time, status: 'COMPLETED' } satisfies ScheduleListItem],
    columns: 12, dense: true, actions: true },
  { path: 'audits', api: 'audits', data: [audit,
    { ...audit, audit_id: 'second-audit', audit_type: 'MODEL', run_id: id },
    { ...audit, audit_id: 'missing-trace', trace_id: null }], columns: 8, dense: false, actions: false }
] as const;
