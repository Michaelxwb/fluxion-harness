/**
 * 运行审计模块类型契约（设计 §3.4/§3.5）。
 *
 * 字段一律 camelCase：后端 snake_case ↔ camelCase 的映射只发生在
 * `services/auditService.ts`，组件不直接消费原始 Envelope。
 */

/** 后端列表分页上限（`page_size <= 100`），service 层按此收敛。 */
export const AUDIT_PAGE_SIZE_MAX = 100;

/** 列表默认页大小；页面初始筛选持有（设计 §3.4）。 */
export const AUDIT_PAGE_SIZE_DEFAULT = 15;

/** 列表粒度视图：操作（一次业务操作一行）/ 明细（一次调用一行）。 */
export type AuditView = 'operation' | 'detail';

/** 审计类型：四类来源表事件 + `RUN` 操作行（RUN 只出现在操作视图）。 */
export type AuditType = 'RUN' | 'CONFIG' | 'TOOL' | 'EGRESS' | 'MODEL';

/** 详情只接受四类来源表：`RUN` 由运行详情承载。 */
export type AuditDetailType = Exclude<AuditType, 'RUN'>;

/** 操作行的内部步骤计数（后端当页聚合）。 */
export interface AuditStepSummary {
  model: number;
  tool: number;
  egress: number;
  retries: number;
}

/** 操作行的失败摘要：仅在 Run 结果为失败时出现。 */
export interface AuditFailureSummary {
  kind: 'MODEL' | 'TOOL' | 'EGRESS';
  name: string | null;
  code: string | null;
}

export interface AuditListQuery {
  keyword?: string;
  auditType?: AuditType;
  /** 由页面按 `auditType` 派生并显式传给后端（导出请求不带该字段）。 */
  view?: AuditView;
  resourceType?: string;
  resourceId?: string;
  actorUserId?: string;
  /** Agent 筛选（API-01/API-05 的 `agent_id`）：只命中运行类记录，config 类投影为空。 */
  agentId?: string;
  action?: string;
  resultStatus?: string;
  traceId?: string;
  startTime?: string;
  endTime?: string;
  page: number; // >= 1
  pageSize: number; // <= 100，默认 20
}

export interface AuditListItem {
  auditId: string;
  auditType: AuditType;
  resourceType: string;
  resourceId: string;
  actorUserId: string;
  actorName?: string;
  agentId?: string;
  agentName?: string;
  action: string;
  resultStatus: string; // SUCCESS | FAILED | <领域错误码>
  traceId?: string;
  target: string;
  occurredAt: string; // YYYY-MM-DD HH:mm:ss
  latencyMs?: number;
  /** 关联对象（列表列）：运行类审计行的裸外键，CONFIG 行为空。 */
  runId?: string;
  taskId?: string;
  /** RUN 行的用户提问摘要（只用于列表展示，不进导出）。 */
  summary?: string | null;
  /** RUN 行的内部步骤计数；聚合降级时为 null（不显示 0）。 */
  steps?: AuditStepSummary | null;
  /** RUN 行的失败摘要；Run 成功时为 null。 */
  failure?: AuditFailureSummary | null;
}

/** 详情可读关联：仅在后端确认关联真实可读时出现（不可读见 `relatedMissing`）。 */
export interface AuditRelations {
  runId?: string;
  taskId?: string;
}

export interface AuditDetail extends Omit<AuditListItem, 'auditType'> {
  /** 详情只可能来自四类来源表（`RUN` 走运行详情）。 */
  auditType: AuditDetailType;
  related: AuditRelations;
  relatedMissing: boolean;
  /** 投影行未映射列（startedAt/finishedAt…）与来源表独有字段（CONFIG 的 before/after、TOOL 的 argsPreview、EGRESS 的 policyDecision…）。 */
  extras: Record<string, unknown>;
}

export interface AuditExportCreateRequest {
  exportFormat: 'CSV' | 'JSON';
  filters: Omit<AuditListQuery, 'page' | 'pageSize'>;
}

export interface AuditExportJob {
  exportId: string;
  status: 'PENDING' | 'RUNNING' | 'SUCCEEDED' | 'FAILED';
  rowCount?: number;
  errorCode?: string;
  createTime: string; // YYYY-MM-DD HH:mm:ss
  updateTime: string;
}

export interface AuditPage {
  items: AuditListItem[];
  page: number;
  pageSize: number;
  total: number;
}
