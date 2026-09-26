/**
 * 概览与运营入口 service 层（设计 §3.4/§3.5）。
 *
 * 全部请求经共享 apiClient（自动 X-Locale/X-Request-Id/CSRF 头）；后端 snake_case 与前端
 * camelCase 的字段映射只在本层发生，组件不直接消费原始 Envelope。
 */

import { api, type ApiResponse } from '../../../api/client';
import type {
  NextScheduleItem,
  OverviewData,
  OverviewKpis,
  RecentTaskItem
} from '../types';

/** KPI 出参（`overview_query_service.get_overview` 的 `kpis`）。 */
interface RawKpis {
  enabled_agents: number;
  enabled_skills: number;
  active_tasks: number;
  active_schedules: number;
}

/** `recent_tasks[]` 出参。 */
interface RawTask {
  task_id: string;
  intent_key: string;
  agent_id: string;
  agent_name: string | null;
  actor_user_id: string;
  actor_user_name: string | null;
  status: string;
  trigger_type: RecentTaskItem['triggerType'];
  delivery_status: RecentTaskItem['deliveryStatus'];
  started_at: string | null;
  finished_at: string | null;
  deadline_at: string | null;
  create_time: string;
}

/** `next_schedules[]` 出参。 */
interface RawSchedule {
  schedule_id: string;
  name: string;
  agent_id: string;
  agent_name: string | null;
  actor_user_id: string;
  actor_user_name: string | null;
  intent_key: string;
  status: string;
  next_fire_at: string;
  last_fire_at: string | null;
  timezone: string;
}

interface RawOverview {
  kpis: RawKpis;
  recent_tasks: RawTask[];
  next_schedules: RawSchedule[];
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

export function toTask(raw: RawTask): RecentTaskItem {
  return {
    taskId: raw.task_id,
    intentKey: raw.intent_key,
    agentId: raw.agent_id,
    agentName: raw.agent_name,
    actorUserId: raw.actor_user_id,
    actorUserName: raw.actor_user_name,
    status: raw.status,
    triggerType: raw.trigger_type,
    deliveryStatus: raw.delivery_status,
    startedAt: raw.started_at,
    finishedAt: raw.finished_at,
    deadlineAt: raw.deadline_at,
    createTime: raw.create_time
  };
}

export function toSchedule(raw: RawSchedule): NextScheduleItem {
  return {
    scheduleId: raw.schedule_id,
    name: raw.name,
    agentId: raw.agent_id,
    agentName: raw.agent_name,
    actorUserId: raw.actor_user_id,
    actorUserName: raw.actor_user_name,
    intentKey: raw.intent_key,
    status: raw.status,
    nextFireAt: raw.next_fire_at,
    lastFireAt: raw.last_fire_at,
    timezone: raw.timezone
  };
}

/** 一次聚合取全 4 个 KPI 与两组列表（不按实体循环拉取）。 */
export async function getOverview(): Promise<OverviewData> {
  const raw = await unwrap(await api.get<ApiResponse<RawOverview>>('/overview'));
  return {
    kpis: toKpis(raw.kpis),
    recentTasks: raw.recent_tasks.map(toTask),
    nextSchedules: raw.next_schedules.map(toSchedule)
  };
}
