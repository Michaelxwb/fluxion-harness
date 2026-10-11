import { api, type ApiResponse } from '../../../api/client';
import type { OverviewData, OverviewKpis, OverviewResources, OverviewActivity } from '../types';

interface RawKpis {
  run_count: number; run_success_rate: number | null; average_run_duration_ms: number | null;
  active_users: number; model_request_count: number; input_tokens: number | null;
  output_tokens: number | null; total_tokens: number | null;
  token_reported_request_count: number; token_coverage: number | null;
}
interface RawResources {
  enabled_agents: number; enabled_skills: number; enabled_mcps: number;
  enabled_models: number; users: number; active_schedules: number;
}
interface RawActivity {
  active_runs: number; queued_tasks: number; running_tasks: number; waiting_tasks: number;
  waiting_input_runs: number; waiting_tool_runs: number; overdue_tasks: number;
}
interface RawOverview {
  days: number; timezone: string; generated_at: string;
  resources: RawResources; kpis: RawKpis; activity: RawActivity;
  attention: { failed_runs: number; failed_tasks: number; failed_deliveries: number; };
  trend: { runs: OverviewData['trend']['runs']; tasks: OverviewData['trend']['tasks'];
    tokens: { date: string; input_tokens: number | null; output_tokens: number | null;
      model_request_count: number; token_reported_request_count: number; }[]; };
  status: OverviewData['status'];
  agent_ranking: { agent_id: string; agent_name: string | null; run_count: number; }[];
}
function mapKpis(raw: RawKpis): OverviewKpis {
  return { runCount: raw.run_count, runSuccessRate: raw.run_success_rate,
    averageRunDurationMs: raw.average_run_duration_ms, activeUsers: raw.active_users,
    modelRequestCount: raw.model_request_count, inputTokens: raw.input_tokens,
    outputTokens: raw.output_tokens, totalTokens: raw.total_tokens,
    tokenReportedRequestCount: raw.token_reported_request_count, tokenCoverage: raw.token_coverage };
}
function mapResources(raw: RawResources): OverviewResources {
  return { enabledAgents: raw.enabled_agents, enabledSkills: raw.enabled_skills,
    enabledMcps: raw.enabled_mcps, enabledModels: raw.enabled_models,
    users: raw.users, activeSchedules: raw.active_schedules };
}
function mapActivity(raw: RawActivity): OverviewActivity {
  return { activeRuns: raw.active_runs, queuedTasks: raw.queued_tasks,
    runningTasks: raw.running_tasks, waitingTasks: raw.waiting_tasks,
    waitingInputRuns: raw.waiting_input_runs, waitingToolRuns: raw.waiting_tool_runs,
    overdueTasks: raw.overdue_tasks };
}
function mapOverview(raw: RawOverview): OverviewData {
  return { days: raw.days, timezone: raw.timezone, generatedAt: raw.generated_at,
    resources: mapResources(raw.resources), kpis: mapKpis(raw.kpis), activity: mapActivity(raw.activity),
    attention: { failedRuns: raw.attention.failed_runs, failedTasks: raw.attention.failed_tasks,
      failedDeliveries: raw.attention.failed_deliveries },
    trend: { runs: raw.trend.runs, tasks: raw.trend.tasks,
      tokens: raw.trend.tokens.map((point) => ({ date: point.date, inputTokens: point.input_tokens,
        outputTokens: point.output_tokens, modelRequestCount: point.model_request_count,
        tokenReportedRequestCount: point.token_reported_request_count })) },
    status: raw.status, agentRanking: raw.agent_ranking.map((row) => ({
      agentId: row.agent_id, agentName: row.agent_name, runCount: row.run_count })) };
}
export async function getOverview(days = 7): Promise<OverviewData> {
  const response = await api.get<ApiResponse<RawOverview>>('/overview', { params: { days } });
  if (response.data.data === null) throw response.data;
  return mapOverview(response.data.data);
}
