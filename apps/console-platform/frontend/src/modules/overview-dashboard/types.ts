/**
 * 概览模块类型契约（设计 §3.4 组件接口契约）。
 *
 * 只表达结构：文案一律由组件用 i18n key 承载；后端 snake_case → 前端 camelCase 的映射
 * 只发生在 `services/overviewService.ts`，组件不直接消费原始 Envelope。
 *
 * v2（指标化改造）：页面为纯指标页——KPI + 两块图表；列表与运行关系说明卡已按对齐结论移除，
 * 明细一律去后台任务 / 定时任务 / 运行审计页看。`/overview` 聚合接口仍返回两组列表，前端不再消费。
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

export interface OverviewData {
  kpis: OverviewKpis;
}

export interface KpiCardsProps {
  kpis: OverviewKpis;
  loading: boolean;
}

/** 单日任务趋势点（后端按平台默认时区的日历日聚合，无数据日补零）。 */
export interface TaskTrendPoint {
  /** YYYY-MM-DD */
  date: string;
  total: number;
  failed: number;
}

export interface OverviewMetrics {
  /** 实际窗口天数（= 请求的 days） */
  days: number;
  /** 分桶所用 IANA 时区（平台默认时区，非法时降级 UTC） */
  timezone: string;
  taskTrend: TaskTrendPoint[];
  /** 全量状态计数（键为任务状态枚举，可能含未知新增值） */
  taskStatus: Record<string, number>;
}

export interface OverviewMetricsQuery {
  days?: number;
}
