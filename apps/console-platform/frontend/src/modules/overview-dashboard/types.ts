/**
 * 概览与运营入口类型契约（设计 §3.4 组件接口契约）。
 *
 * 只表达结构：文案一律由组件用 i18n key 承载；后端 snake_case → 前端 camelCase 的映射
 * 只发生在 `services/overviewService.ts`，组件不直接消费原始 Envelope。
 *
 * 与设计的精度差异（如实登记）：设计把 `agentName`/时间字段写成非空 `string`，但后端是
 * LEFT JOIN + 可空时间列，实际会返回 `null`；本契约按**实际出参**标注可空，避免下游误判。
 */

export interface OverviewKpis {
  /** 启用 Agent */
  enabledAgents: number;
  /** 启用 Skill */
  enabledSkills: number;
  /** 后台执行中（非终态）Task */
  activeTasks: number;
  /** 启用定时任务 */
  activeSchedules: number;
}

export type TaskTriggerType = 'IMMEDIATE' | 'SCHEDULED';
export type TaskDeliveryStatus = 'PENDING' | 'SENT' | 'FAILED' | 'NONE';

export interface RecentTaskItem {
  taskId: string;
  intentKey: string;
  agentId: string;
  agentName: string | null;
  actorUserId: string;
  actorUserName: string | null;
  status: string;
  triggerType: TaskTriggerType;
  deliveryStatus: TaskDeliveryStatus;
  /** YYYY-MM-DD HH:mm:ss */
  startedAt: string | null;
  finishedAt: string | null;
  deadlineAt: string | null;
  createTime: string;
}

export interface NextScheduleItem {
  scheduleId: string;
  name: string;
  agentId: string;
  agentName: string | null;
  actorUserId: string;
  actorUserName: string | null;
  intentKey: string;
  /** 列表仅含 ACTIVE */
  status: string;
  /** YYYY-MM-DD HH:mm:ss */
  nextFireAt: string;
  lastFireAt: string | null;
  timezone: string;
}

export interface OverviewData {
  kpis: OverviewKpis;
  recentTasks: RecentTaskItem[];
  nextSchedules: NextScheduleItem[];
}

export interface KpiCardsProps {
  kpis: OverviewKpis;
  loading: boolean;
}

export interface RecentTaskListProps {
  items: RecentTaskItem[];
  loading: boolean;
  onOpenTask(taskId: string): void;
  onViewAll(): void;
}

export interface NextScheduleListProps {
  items: NextScheduleItem[];
  loading: boolean;
  onOpenSchedule(scheduleId: string): void;
  onViewAll(): void;
}

/** 纯静态说明卡：无 props。 */
export type RuntimeRelationCardProps = Record<string, never>;
