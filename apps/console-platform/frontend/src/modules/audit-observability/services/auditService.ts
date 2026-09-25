/**
 * 运行审计 service 层（设计 §3.4/§3.5）。
 *
 * 全部请求经共享 apiClient（自动 X-Locale/X-Request-Id/CSRF 头）；后端 snake_case 与前端
 * camelCase 的字段映射只在本层发生，组件不直接消费原始 Envelope。
 */

import { api, type ApiResponse } from '../../../api/client';
import {
  AUDIT_PAGE_SIZE_MAX,
  type AuditDetail,
  type AuditExportCreateRequest,
  type AuditExportJob,
  type AuditListItem,
  type AuditListQuery,
  type AuditPage,
  type AuditRelations
} from '../types';

/** 后端分页封套（`muad_api.response.paginate`）。 */
interface RawPage<T> {
  items: T[];
  page: number;
  page_size: number;
  total: number;
}

/** 后端审计行出参（`audit_query_repository.PROJECTED_COLUMNS`）。 */
interface RawAuditItem {
  audit_id: string;
  audit_type: AuditListItem['auditType'];
  resource_type: string;
  resource_id: string;
  actor_user_id: string;
  actor_name: string | null;
  agent_id: string | null;
  agent_name: string | null;
  action: string;
  result_status: string;
  trace_id: string | null;
  target: string;
  occurred_at: string;
  latency_ms: number | null;
}

/** 详情 = 投影行 + 来源表独有字段 + 关联可读性（E-01）。 */
interface RawAuditDetail extends RawAuditItem {
  related: Partial<Record<'run_id' | 'task_id', string>>;
  related_missing: boolean;
  [extra: string]: unknown;
}

/** 列表查询出参：后端 snake_case 命名；值为 undefined 的筛选默认不进查询串。 */
interface RawListQuery {
  audit_type?: string;
  resource_type?: string;
  resource_id?: string;
  actor_user_id?: string;
  action?: string;
  result_status?: string;
  trace_id?: string;
  start_time?: string;
  end_time?: string;
  page: number;
  page_size: number;
}

/** 导出创建请求体：后端 `AuditExportCreateRequest` DTO 为 flat 且 `extra="forbid"`。 */
interface RawExportCreateBody {
  export_format: string;
  audit_type?: string;
  resource_type?: string;
  resource_id?: string;
  actor_user_id?: string;
  action?: string;
  result_status?: string;
  trace_id?: string;
  start_time?: string;
  end_time?: string;
}

/** 创建出参：后端只回 `{export_id, status, create_time}`。 */
interface RawExportCreated {
  export_id: string;
  status: AuditExportJob['status'];
  create_time: string;
}

/** 状态出参（API-06）。 */
interface RawExportStatus {
  export_id: string;
  status: AuditExportJob['status'];
  row_count: number | null;
  error_code: string | null;
  create_time: string;
  update_time: string;
}

/**
 * 投影行已被 `toAuditItem` 消费的字段（含后端为兼容旧审计页签保留的别名）。
 *
 * 其余字段一律进 `AuditDetail.extras`（camelCase），未知字段不会静默丢失。
 */
const MAPPED_RAW_KEYS = new Set([
  'audit_id',
  'audit_type',
  'resource_type',
  'resource_id',
  'actor_user_id',
  'actor_name',
  'agent_id',
  'agent_name',
  'action',
  'result_status',
  'trace_id',
  'target',
  'occurred_at',
  'latency_ms',
  'id',
  'actor_display_name',
  'create_time',
  'related',
  'related_missing'
]);

async function unwrap<T>(response: { data: ApiResponse<T> }): Promise<T> {
  if (response.data.data === null) throw response.data;
  return response.data.data;
}

function optional<T>(value: T | null | undefined): T | undefined {
  return value === null || value === undefined ? undefined : value;
}

function camelCase(key: string): string {
  return key.replace(/_([a-z0-9])/g, (_match, char: string) => char.toUpperCase());
}

function toAuditItem(raw: RawAuditItem): AuditListItem {
  return {
    auditId: raw.audit_id,
    auditType: raw.audit_type,
    resourceType: raw.resource_type,
    resourceId: raw.resource_id,
    actorUserId: raw.actor_user_id,
    actorName: optional(raw.actor_name),
    agentId: optional(raw.agent_id),
    agentName: optional(raw.agent_name),
    action: raw.action,
    resultStatus: raw.result_status,
    traceId: optional(raw.trace_id),
    target: raw.target,
    occurredAt: raw.occurred_at,
    latencyMs: optional(raw.latency_ms)
  };
}

function toAuditDetail(raw: RawAuditDetail): AuditDetail {
  const extras: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(raw)) {
    if (MAPPED_RAW_KEYS.has(key)) continue;
    extras[camelCase(key)] = value;
  }
  const relations: AuditRelations = {
    runId: optional(raw.related.run_id),
    taskId: optional(raw.related.task_id)
  };
  return { ...toAuditItem(raw), related: relations, relatedMissing: raw.related_missing, extras };
}

function toListParams(query: AuditListQuery): RawListQuery {
  return {
    audit_type: query.auditType,
    resource_type: query.resourceType,
    resource_id: query.resourceId,
    actor_user_id: query.actorUserId,
    action: query.action,
    result_status: query.resultStatus,
    trace_id: query.traceId,
    start_time: query.startTime,
    end_time: query.endTime,
    page: query.page,
    page_size: Math.min(query.pageSize, AUDIT_PAGE_SIZE_MAX)
  };
}

function toExportBody(req: AuditExportCreateRequest): RawExportCreateBody {
  const filters = req.filters;
  return {
    export_format: req.exportFormat,
    audit_type: filters.auditType,
    resource_type: filters.resourceType,
    resource_id: filters.resourceId,
    actor_user_id: filters.actorUserId,
    action: filters.action,
    result_status: filters.resultStatus,
    trace_id: filters.traceId,
    start_time: filters.startTime,
    end_time: filters.endTime
  };
}

export async function listAudits(params: AuditListQuery): Promise<AuditPage> {
  const page = await unwrap(
    await api.get<ApiResponse<RawPage<RawAuditItem>>>('/audits', { params: toListParams(params) })
  );
  return {
    items: page.items.map(toAuditItem),
    page: page.page,
    pageSize: page.page_size,
    total: page.total
  };
}

/** 详情：4 张来源表 UUID 不互通，`audit_type` 必填且不猜表。 */
export async function getAudit(
  auditType: AuditListItem['auditType'],
  id: string
): Promise<AuditDetail> {
  const raw = await unwrap(
    await api.get<ApiResponse<RawAuditDetail>>(`/audits/${id}`, {
      params: { audit_type: auditType }
    })
  );
  return toAuditDetail(raw);
}

/**
 * 创建导出任务（设计 §3.5「导出幂等约定」/ RULE-api-002）。
 *
 * `Idempotency-Key` 由调用方持有：一次用户提交只生成一次（组件用 `newRequestId()`），
 * 提交重试（网络超时/双击）必须复用同一 key，后端据此重放首次结果；只有用户显式发起
 * 新导出才换新 key。本函数不自行生成 key。
 */
export async function createExport(
  req: AuditExportCreateRequest,
  idempotencyKey: string
): Promise<AuditExportJob> {
  const created = await unwrap(
    await api.post<ApiResponse<RawExportCreated>>('/audits/exports', toExportBody(req), {
      headers: { 'Idempotency-Key': idempotencyKey }
    })
  );
  // 创建响应只带 create_time：此刻 update_time 与之相同，后续以 getExport 轮询为准。
  return {
    exportId: created.export_id,
    status: created.status,
    createTime: created.create_time,
    updateTime: created.create_time
  };
}

/** 导出状态（轮询）：`FAILED` 时 `errorCode` 为 catalog 错误码，由调用方映射 i18n key。 */
export async function getExport(exportId: string): Promise<AuditExportJob> {
  const status = await unwrap(
    await api.get<ApiResponse<RawExportStatus>>(`/audits/exports/${exportId}`)
  );
  return {
    exportId: status.export_id,
    status: status.status,
    rowCount: optional(status.row_count),
    errorCode: optional(status.error_code),
    createTime: status.create_time,
    updateTime: status.update_time
  };
}

/**
 * 下载导出产物：后端直出字节流（`Content-Disposition: attachment`），不是封套。
 *
 * 失败响应体是 Blob，取不到封套错误码；错误码由调用方经 `getExport` 轮询到 `FAILED` 的
 * `errorCode` 后映射 catalog → i18n key 呈现。
 */
export async function downloadExport(exportId: string): Promise<Blob> {
  const response = await api.get<Blob>(`/audits/exports/${exportId}/download`, {
    responseType: 'blob'
  });
  return response.data;
}
