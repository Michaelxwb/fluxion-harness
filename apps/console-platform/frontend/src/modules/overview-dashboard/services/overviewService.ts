/**
 * 概览 service 层（设计 §3.4/§3.5）。
 *
 * 全部请求经共享 apiClient（自动 X-Locale/X-Request-Id/CSRF 头）；后端 snake_case 与前端
 * camelCase 的字段映射只在本层发生，组件不直接消费原始 Envelope。
 *
 * v2（指标化改造）：`getOverview` 只消费 KPI（接口仍返回两组列表，前端已不使用）；
 * `getOverviewMetrics` 供指标图取数（任务趋势 + 状态分布）。
 */

import { api, type ApiResponse } from '../../../api/client';
import type { OverviewData, OverviewKpis, OverviewMetrics } from '../types';

/** KPI 出参（`overview_query_service.get_overview` 的 `kpis`）。 */
interface RawKpis {
  enabled_agents: number;
  enabled_skills: number;
  active_tasks: number;
  active_schedules: number;
}

interface RawOverview {
  kpis: RawKpis;
}

/** `task_trend[]` 出参（按平台默认时区的日历日分桶，无数据日由后端补零）。 */
interface RawTrendPoint {
  date: string;
  total: number;
  failed: number;
}

/** `GET /overview/metrics` 出参。 */
interface RawMetrics {
  days: number;
  timezone: string;
  task_trend: RawTrendPoint[];
  task_status: Record<string, number>;
}

async function unwrap<T>(response: { data: ApiResponse<T> }): Promise<T> {
  if (response.data.data === null) throw response.data;
  return response.data.data;
}

export function toKpis(raw: RawKpis): OverviewKpis {
  return {
    enabledAgents: raw.enabled_agents,
    enabledSkills: raw.enabled_skills,
    activeTasks: raw.active_tasks,
    activeSchedules: raw.active_schedules
  };
}

export function toMetrics(raw: RawMetrics): OverviewMetrics {
  return {
    days: raw.days,
    timezone: raw.timezone,
    taskTrend: raw.task_trend.map((point) => ({
      date: point.date,
      total: point.total,
      failed: point.failed
    })),
    taskStatus: { ...raw.task_status }
  };
}

/** 一次聚合取全 4 个 KPI（不按实体循环拉取；列表出参前端已不消费）。 */
export async function getOverview(): Promise<OverviewData> {
  const raw = await unwrap(await api.get<ApiResponse<RawOverview>>('/overview'));
  return { kpis: toKpis(raw.kpis) };
}

/** 指标图聚合：默认近 7 天任务趋势 + 全量任务状态分布（后端按平台默认时区分桶）。 */
export async function getOverviewMetrics(days = 7): Promise<OverviewMetrics> {
  const raw = await unwrap(
    await api.get<ApiResponse<RawMetrics>>('/overview/metrics', { params: { days } })
  );
  return toMetrics(raw);
}
