export type OverviewScope = 'runs' | 'tasks';
export interface OverviewResources {
  enabledAgents: number; enabledSkills: number; enabledMcps: number;
  enabledModels: number; users: number; activeSchedules: number;
}
export interface OverviewKpis {
  runCount: number; runSuccessRate: number | null; averageRunDurationMs: number | null;
  activeUsers: number; modelRequestCount: number;
  inputTokens: number | null; outputTokens: number | null; totalTokens: number | null;
  tokenReportedRequestCount: number; tokenCoverage: number | null;
}
export interface OverviewActivity {
  activeRuns: number; queuedTasks: number; runningTasks: number; waitingTasks: number;
  waitingInputRuns: number; waitingToolRuns: number; overdueTasks: number;
}
export interface OverviewAttention {
  failedRuns: number; failedTasks: number; failedDeliveries: number;
}
export interface OverviewTrendPoint { date: string; total: number; failed: number; }
export interface OverviewTokenPoint {
  date: string; inputTokens: number | null; outputTokens: number | null;
  modelRequestCount: number; tokenReportedRequestCount: number;
}
export interface OverviewAgentRanking { agentId: string; agentName: string | null; runCount: number; }
export interface OverviewData {
  days: number; timezone: string; generatedAt: string;
  resources: OverviewResources; kpis: OverviewKpis;
  activity: OverviewActivity; attention: OverviewAttention;
  trend: { runs: OverviewTrendPoint[]; tasks: OverviewTrendPoint[]; tokens: OverviewTokenPoint[]; };
  status: { runs: Record<string, number>; tasks: Record<string, number>; };
  agentRanking: OverviewAgentRanking[];
}
