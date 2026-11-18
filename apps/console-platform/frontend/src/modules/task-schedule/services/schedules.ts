import { api, newRequestId, type ApiResponse } from '../../../api/client';
import type { Page } from '../../user-identity/services/users';
import type { TaskStatus } from './tasks';

/**
 * Console 定时任务服务的 typed 契约（scheduled-task-v2 §3.10；TASK-030）。
 *
 * 出参字段一律来自 Console 后端对 Worker 真值的投影（`application/schedule_view.py`）：
 * 配置在 Revision 里（`mode`/`timezone`/`policy`/`budget`），Schedule 行只有身份与生命周期事实
 * （`status`/`end_reason`/`config_version`/`next_fire_at`）。前端不复制状态机，也不猜后端默认值。
 *
 * 生命周期动作只有 `POST /schedules/{id}/actions` 一个入口（`pause|resume|cancel`）：
 * 旧的 `PUT pause|resume` 已移除、没有双路由，这里也不提供对应的服务方法。
 */

export type ScheduleStatus = 'ACTIVE' | 'PAUSED' | 'NEEDS_ATTENTION' | 'ENDED' | 'CANCELLED';
export type ScheduleMode = 'REMINDER' | 'SKILL' | 'AGENTIC';
export type ScheduleEndReason =
  | 'COMPLETED'
  | 'MISSED'
  | 'EXHAUSTED'
  | 'ADMISSION_FAILED'
  | 'USER_CANCELLED';
export type FireStatus =
  | 'CREATED'
  | 'EXECUTION_CREATED'
  | 'SKIPPED'
  | 'FAILED_ADMISSION'
  | 'CANCELLED';
export type FireSkipReason = 'OVERLAP' | 'MISFIRE' | 'QUOTA' | 'PAUSED_BY_UPDATE';
export type DstResolution = 'FOLD_FIRST';
export type DstEventType = 'DST_GAP_SKIPPED' | 'DST_FOLD_SECOND_SKIPPED';
export type ScheduleAction = 'pause' | 'resume' | 'cancel';
/** 取消范围：只停未来，或终止全部（含进行中/等待中）。缺省由用户显式选择。 */
export type CancelScope = 'FUTURE_ONLY' | 'ALL_ACTIVE';
export type DeliveryIntent = 'PUSH' | 'NONE';
export type MemoryMode = 'READ_ONLY';
export type ConcurrencyPolicy = 'SKIP_IF_RUNNING' | 'QUEUE' | 'ALLOW_PARALLEL';
export type DstGapPolicy = 'SKIP';
export type DstFoldPolicy = 'FIRST';

export const SCHEDULE_PAGE_SIZE_MAX = 100;

/** 最近一次 Fire 的摘要（列表 ↔ 详情同一形状，来自 Worker 的批量查询，不是逐行请求）。 */
export interface LastFireSummary {
  scheduled_at: string;
  status: FireStatus;
  execution_status: TaskStatus | null;
  missed_count: number;
  dst_resolution: DstResolution | null;
}

export interface ScheduleListItem {
  schedule_id: string;
  agent_id: string;
  /** Schedule 的 owner（创建者）；列表按它过滤/展示。 */
  actor_user_id: string;
  name: string;
  mode: ScheduleMode;
  status: ScheduleStatus;
  timezone: string;
  config_version: number;
  next_fire_at: string | null;
  last_fire: LastFireSummary | null;
  needs_attention: boolean;
  pending_human: boolean;
  /** 名称补齐（Console control.* 权威源，列表接口注入；缺失为 null，前端回落短 id）。 */
  agent_name?: string | null;
  actor_name?: string | null;
}

export interface AgenticBudget {
  max_steps: number;
  max_tool_calls: number;
  max_tokens: number;
}

export interface DraftContext {
  parameters: Record<string, unknown>;
  output: Record<string, unknown>;
}

export interface SchedulePolicy {
  concurrency_policy: ConcurrencyPolicy;
  max_concurrency: number;
  until: string | null;
  max_occurrences: number | null;
  dst_gap_policy: DstGapPolicy;
  dst_fold_policy: DstFoldPolicy;
}

export type SchedulePayload =
  | { kind: 'REMINDER'; text: string }
  | {
      kind: 'SKILL_PLAN';
      skill_id: string;
      input: Record<string, unknown>;
      argv: string[];
      window: { kind: 'DAY'; offset: number };
    }
  | {
      kind: 'AGENT_TURN';
      prompt: string;
      model_ref: string | null;
      tools_allow: string[];
      budget_ref: string;
    };

export type SessionTarget = 'isolated' | 'current' | `session:${string}` | 'main';

export interface ScheduleDetail extends ScheduleListItem {
  revision_no: number;
  instruction: string;
  normalized_goal: string;
  context: DraftContext;
  payload: SchedulePayload;
  session_target: SessionTarget;
  /** SKILL_PLAN 是 `SKILL:<id>`，AGENT_TURN 是工具策略条目；REMINDER 为空。 */
  dependencies: string[];
  delivery_intent: DeliveryIntent;
  tools_allow: string[];
  /** `PLATFORM` = 平台设置；`NONE` = 该 payload 不消费模型预算。 */
  budget_source: 'PLATFORM' | 'NONE';
  policy: SchedulePolicy;
  budget: AgenticBudget | null;
  memory_mode: MemoryMode | null;
  end_reason: ScheduleEndReason | null;
  attention_reason: string | null;
}

/** 一次 occurrence 的处理结果；DST 事件不在这张表里（见 `DstEvent`）。 */
export interface FireItem {
  fire_id: string;
  scheduled_at: string;
  status: FireStatus;
  skip_reason: FireSkipReason | null;
  missed_count: number;
  dst_resolution: DstResolution | null;
  admission_error_code: string | null;
  execution_id: string | null;
  execution_status: TaskStatus | null;
}

/** 不产生 Fire 的 DST 事实：与真实 Fire 分开返回，前端不得把它渲染成触发记录。 */
export interface DstEvent {
  event_id: string;
  event_type: DstEventType;
  local_date: string;
  local_time: string;
  timezone: string;
  occurred_at: string;
}

export interface HistoryRetention {
  fire_history_days: number;
  execution_history_days: number;
  cleaned_before: string;
  oldest_retained_at: string | null;
}

export interface FireHistoryPage {
  items: FireItem[];
  dst_events: DstEvent[];
  page: number;
  page_size: number;
  total: number;
  retention: HistoryRetention | null;
}

export interface ScheduleListParams {
  actor_user_id?: string;
  agent_id?: string;
  status?: ScheduleStatus;
  page?: number;
  page_size?: number;
}

export interface FireHistoryParams {
  page?: number;
  page_size?: number;
}

export interface ScheduleActionInput {
  action: ScheduleAction;
  /** 只属于 `cancel`；其余动作带 scope 会被后端以校验错误拒绝。 */
  scope?: CancelScope;
  reason?: string;
}

/** 恢复时 Admission 判定的结果：`repair` 是可读的修复指引，由后端给出。 */
export interface ScheduleActionAdmissionError {
  code: string;
  permanent: boolean;
  repair: string;
}

export interface ScheduleActionResult {
  schedule_id: string;
  status: ScheduleStatus;
  config_version: number;
  next_fire_at: string | null;
  last_fire_at: string | null;
  occurrence_count: number;
  attention_reason: string | null;
  attention_since: string | null;
  ended_at: string | null;
  end_reason: ScheduleEndReason | null;
  create_time: string;
  update_time: string;
  admission_error?: ScheduleActionAdmissionError;
}

export interface DeleteScheduleResult {
  schedule_id: string;
  deleted: boolean;
}

async function unwrap<T>(response: { data: ApiResponse<T> }): Promise<T> {
  if (response.data.data === null) throw response.data;
  return response.data.data;
}

function normalizeParams<T extends { page?: number; page_size?: number }>(params: T): T {
  const pageSize = params.page_size ?? 20;
  return {
    ...params,
    page: params.page ?? 1,
    page_size: Math.min(pageSize, SCHEDULE_PAGE_SIZE_MAX)
  };
}

/** 写操作统一带追踪 ID 与幂等键：网络重试沿用同一键由服务端重放首次结果。 */
function writeHeaders(): Record<string, string> {
  return { 'X-Request-Id': newRequestId(), 'Idempotency-Key': newRequestId() };
}

export async function listSchedules(
  params: ScheduleListParams = {}
): Promise<Page<ScheduleListItem>> {
  return unwrap(
    await api.get<ApiResponse<Page<ScheduleListItem>>>('/schedules', {
      params: normalizeParams(params)
    })
  );
}

export async function getSchedule(id: string): Promise<ScheduleDetail> {
  return unwrap(await api.get<ApiResponse<ScheduleDetail>>(`/schedules/${id}`));
}

/** Fire 历史：真实 Fire 与 DST 事件在同一次分页响应里分开给出。 */
export async function listScheduleFires(
  id: string,
  params: FireHistoryParams = {}
): Promise<FireHistoryPage> {
  return unwrap(
    await api.get<ApiResponse<FireHistoryPage>>(
      `/schedules/${id}/fires`,
      { params: normalizeParams(params) }
    )
  );
}

/** 暂停/恢复/取消的唯一入口；Console 不提供创建或用户意图编辑。 */
export async function runScheduleAction(
  id: string,
  input: ScheduleActionInput
): Promise<ScheduleActionResult> {
  return unwrap(
    await api.post<ApiResponse<ScheduleActionResult>>(`/schedules/${id}/actions`, input, {
      headers: writeHeaders()
    })
  );
}

/** 管理员清理：软删标记；Fire/Execution/事件历史由后端保留。 */
export async function deleteSchedule(id: string): Promise<DeleteScheduleResult> {
  return unwrap(
    await api.delete<ApiResponse<DeleteScheduleResult>>(`/schedules/${id}`, {
      headers: writeHeaders()
    })
  );
}
