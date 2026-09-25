/**
 * 运行审计模块类型契约（设计 §3.4/§3.5）。
 *
 * 字段一律 camelCase：后端 snake_case ↔ camelCase 的映射只发生在
 * `services/auditService.ts`，组件不直接消费原始 Envelope。
 */

/** 后端列表分页上限（`page_size <= 100`），service 层按此收敛。 */
export const AUDIT_PAGE_SIZE_MAX = 100;

/** 列表默认页大小；页面初始筛选持有（设计 §3.4）。 */
export const AUDIT_PAGE_SIZE_DEFAULT = 20;

export interface AuditListQuery {
  auditType?: 'CONFIG' | 'TOOL' | 'EGRESS' | 'MODEL';
  resourceType?: string;
  resourceId?: string;
  actorUserId?: string;
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
  auditType: 'CONFIG' | 'TOOL' | 'EGRESS' | 'MODEL';
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
}

/** 详情可读关联：仅在后端确认关联真实可读时出现（不可读见 `relatedMissing`）。 */
export interface AuditRelations {
  runId?: string;
  taskId?: string;
}

export interface AuditDetail extends AuditListItem {
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
